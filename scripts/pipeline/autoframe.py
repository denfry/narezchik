#!/usr/bin/env python
"""Автотрекинг активного говорящего для вертикального кропа.

mediapipe 1.0 отдаёт blendshape jawOpen напрямую, поэтому определение «кто сейчас
говорит» строится на готовом сигнале модели, а не на самодельной эвристике по губам.
Legacy-API mp.solutions в 1.0 удалён — доступен только mediapipe.tasks.

Usage:
    python autoframe.py temp/clip_work.mp4              # только посмотреть детект
    python autoframe.py --plan edit-plans/clip.json     # дописать reframe в план
"""
import argparse
import datetime
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import MODELS_DIR, load_json, log, resolve_repo_path, save_json

MODEL_PATH = os.path.join(MODELS_DIR, "face_landmarker.task")
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")

JAW_SPEAKING = 0.08
AUDIO_SILENT = 0.01
# Порог ffmpeg scene для склеек камер. Подкасты сняты в несколько камер (общий план,
# крупные), и кадр обязан переключаться ровно на склейке, а не доезжать после неё.
SCENE_THRESHOLD = 0.3
# Новый говорящий должен продержаться столько, прежде чем кадр перережется на него:
# jawOpen шумит, и кивок слушающего не должен дёргать кадр.
SWITCH_SEC = 1.0
# Минимальная длина неподвижной позиции внутри одного плана камеры.
MIN_HOLD_SEC = 1.5
# Центры лиц ближе этой доли ширины кропа — один и тот же человек.
SAME_PERSON_FRAC = 0.25
# Склейка ближе этого к краю куска речи или к прошлой склейке игнорируется: такой
# план не успевает прочитаться и только мелькает.
MIN_SHOT_SEC = 0.2


def _landmarker(num_faces=4):
    import mediapipe as mp
    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision

    if not os.path.isfile(MODEL_PATH):
        raise RuntimeError(
            f"нет модели {MODEL_PATH}. Скачать: curl -sL -o \"{MODEL_PATH}\" {MODEL_URL}"
        )
    # Модель передаётся буфером, а не путём: C++-слой mediapipe открывает файл
    # narrow-строкой и не находит его, если в пути есть не-ASCII символы —
    # а в пути к репозиторию часто есть кириллица.
    with open(MODEL_PATH, "rb") as fh:
        model_bytes = fh.read()
    opts = vision.FaceLandmarkerOptions(
        base_options=mpp.BaseOptions(model_asset_buffer=model_bytes),
        running_mode=vision.RunningMode.IMAGE,
        num_faces=num_faces,
        output_face_blendshapes=True,
    )
    return mp, vision.FaceLandmarker.create_from_options(opts)


def _detect_in(mp, lm, rgb, ox, oy, scale):
    """Лица в одном фрагменте rgb, координаты возвращаются в системе полного кадра."""
    h, w = rgb.shape[:2]
    res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    faces = []
    for i, pts in enumerate(res.face_landmarks):
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        jaw = 0.0
        if res.face_blendshapes and i < len(res.face_blendshapes):
            jaw = next((b.score for b in res.face_blendshapes[i]
                        if b.category_name == "jawOpen"), 0.0)
        # Линия глаз: внешние уголки глаз в сетке FaceMesh — точки 33 и 263. Нужна
        # filtergraph, чтобы при наезде глаза оставались на одной высоте кадра, иначе
        # смена крупности внутри реплики читается как скачок. Если сетка короче
        # (детектор вернул усечённый набор), берём верхнюю треть лица — там же глаза.
        if len(pts) > 263:
            eye_y_norm = (pts[33].y + pts[263].y) / 2
        else:
            eye_y_norm = min(ys) + (max(ys) - min(ys)) * 0.38
        faces.append({
            "cx": ox + (min(xs) + max(xs)) / 2 * w / scale,
            "cy": oy + (min(ys) + max(ys)) / 2 * h / scale,
            "w": (max(xs) - min(xs)) * w / scale,
            "h": (max(ys) - min(ys)) * h / scale,
            "eye_y": oy + eye_y_norm * h / scale,
            "jaw_open": jaw,
        })
    return faces


def _detect_tiled(mp, lm, img_bgr, tile_scale=2.0):
    """Детекция по полному кадру плюс по увеличенным половинам.

    Детектор внутри face_landmarker — короткодистанционный: на общем плане подкаста
    (два человека за столом, лицо ~150 px в 1080p) он не находит никого вообще,
    хотя лицо в кадре очевидно. Проверено на реальном подкасте: 0 лиц на 100%
    кадров, тот же кадр обрезанный до лица — 1 лицо. Поэтому кадр дополнительно
    режется на левую и правую половины с перекрытием и увеличивается вдвое —
    лицо становится ~300 px и детектируется. Дубли между плитками схлопываются
    по расстоянию между центрами.
    """
    import cv2
    h, w = img_bgr.shape[:2]
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    faces = _detect_in(mp, lm, rgb, 0, 0, 1.0)
    tiles = [(0, 0, int(w * 0.6), h), (int(w * 0.4), 0, w, h)]
    for x0, y0, x1, y1 in tiles:
        crop = rgb[y0:y1, x0:x1]
        up = cv2.resize(crop, None, fx=tile_scale, fy=tile_scale, interpolation=cv2.INTER_LINEAR)
        for f in _detect_in(mp, lm, up, x0, y0, tile_scale):
            if all(abs(f["cx"] - g["cx"]) > g["w"] * 0.5 or abs(f["cy"] - g["cy"]) > g["h"] * 0.5
                   for g in faces):
                faces.append(f)
    return faces


def detect_faces(video_path, sample_fps=4.0):
    """Пройти по видео с выборкой sample_fps и вернуть лица по кадрам."""
    import cv2

    mp, lm = _landmarker()
    cap = cv2.VideoCapture(video_path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(src_fps / sample_fps)))

    frames, idx = [], 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        if idx % step == 0:
            frames.append({"t": idx / src_fps, "faces": _detect_tiled(mp, lm, img)})
        idx += 1
    cap.release()
    log(f"autoframe: обработано {len(frames)} выборочных кадров")
    return frames


def pick_active(frame, audio_rms):
    """Активный говорящий: при звуке — самая открытая челюсть, в тишине — самое
    крупное лицо (в тишине угадывать говорящего нечем и незачем)."""
    faces = frame.get("faces") or []
    if not faces:
        return None
    if audio_rms < AUDIO_SILENT:
        return max(faces, key=lambda f: f["w"] * f["h"])
    talking = [f for f in faces if f["jaw_open"] >= JAW_SPEAKING]
    if talking:
        return max(talking, key=lambda f: f["jaw_open"])
    return max(faces, key=lambda f: f["w"] * f["h"])


def parse_scene_cuts(ffmpeg_stderr):
    """Времена склеек из вывода фильтра showinfo (поле pts_time), по возрастанию."""
    return sorted(float(m) for m in
                  re.findall(r"pts_time:\s*([0-9]+(?:\.[0-9]+)?)", ffmpeg_stderr))


def detect_scene_cuts(video_path, threshold=SCENE_THRESHOLD):
    """Склейки камер в файле, в секундах от его начала.

    Кадр уменьшается до 320 px до сравнения: scene-метрике детали не нужны, а декод
    полного кадра шёл бы в разы дольше.
    """
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-i", video_path, "-map", "0:v:0", "-an",
         "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
         "-f", "null", "-"],
        capture_output=True)
    return parse_scene_cuts(r.stderr.decode(errors="replace"))


def shot_ranges(start, end, cuts, min_shot_sec=MIN_SHOT_SEC):
    """Разбить кусок речи [start, end) на планы по склейкам камер исходника."""
    bounds = [start]
    for c in cuts:
        if c - bounds[-1] >= min_shot_sec and end - c >= min_shot_sec:
            bounds.append(c)
    bounds.append(end)
    return list(zip(bounds[:-1], bounds[1:]))


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def frame_shot(samples, shot_start, *, same_px, switch_sec=SWITCH_SEC,
               min_hold_sec=MIN_HOLD_SEC):
    """Неподвижные позиции кадра внутри одного плана камеры: [(время начала, cx)].

    samples — [(t, cx активного лица или None)]. Кадр не ездит за лицом. Раньше
    траектория сглаживалась экспонентой через все склейки, и после каждой склейки
    кроп стартовал со старой позиции и ступеньками полз к человеку через пустой стол
    (замечено на реальном шортсе). Теперь на говорящего ставится одна позиция — медиана его
    центров, — а смена говорящего делается жёстким перерезом, как смена камеры.
    Первая позиция берётся по большинству выборок первой секунды, чтобы план
    начинался сразу на человеке.
    """
    pts = [(t, cx) for t, cx in samples if cx is not None]
    if not pts:
        return [(shot_start, None)]

    head = [cx for t, cx in pts if t < pts[0][0] + switch_sec]
    seed = max(head, key=lambda c: sum(1 for x in head if abs(x - c) <= same_px))

    runs = []
    cur_start, cur, cur_streak = shot_start, [], 0
    cand_start, cand = None, []
    for t, cx in pts:
        ref = _median(cur) if cur else seed
        if abs(cx - ref) <= same_px:
            cur.append(cx)
            cur_streak += 1
            # одиночная выборка прежнего говорящего посреди чужой реплики — шум jawOpen,
            # а не возврат слова; кандидата сбрасывает только подтверждённый возврат
            if cur_streak >= 2:
                cand_start, cand = None, []
            continue
        cur_streak = 0
        if cand and abs(cx - _median(cand)) <= same_px:
            cand.append(cx)
        else:
            cand_start, cand = t, [cx]
        switch_at = max(cand_start, cur_start + min_hold_sec)
        if t - cand_start >= switch_sec and t >= switch_at:
            runs.append((cur_start, _median(cur) if cur else seed))
            cur_start, cur = switch_at, cand
            cand_start, cand = None, []
    runs.append((cur_start, _median(cur) if cur else seed))
    return runs


def shot_events(runs, shot_end, seg_id, offset, src_width, crop_w, eye_y=None):
    """Позиции плана -> события reframe в таймлайне ролика.

    offset — на сколько время рабочего окна сдвинуто относительно ролика для этого
    куска речи. Крупность не задаётся: это решение visual-director, автотрекинг
    отвечает только за горизонталь и за линию глаз.

    eye_y — доля высоты кадра (0..1), на которой в этом шоте находятся глаза
    говорящего. Попадает в crop.eye_y и используется filtergraph при наезде.
    """
    max_x = max(0, src_width - crop_w)
    events = []
    for k, (t0, cx) in enumerate(runs):
        t1 = runs[k + 1][0] if k + 1 < len(runs) else shot_end
        x = None if cx is None else max(0, min(max_x, int(round(cx - crop_w / 2))))
        events.append({
            "type": "reframe",
            "segment_ref": seg_id,
            "start_sec": round(offset + t0, 3),
            "end_sec": round(offset + t1, 3),
            "crop": {"crop_x": x} if eye_y is None
                    else {"crop_x": x, "eye_y": round(float(eye_y), 4)},
            "reason": "autoframe: неподвижный план на активном говорящем",
            "purpose": "держать активного говорящего в кадре при вертикальном кропе, "
                       "чтобы лицо не уезжало за границу окна 9:16",
        })
    return events


def _work_file_for(plan, force=False):
    import workfile
    edits = plan["speech_edits"]
    start, end = workfile.window_bounds(edits)
    return workfile.extract(resolve_repo_path(plan["source"]["path"]),
                            plan["project"]["clip_id"], start, end, force=force)


def _audio_rms(path, sample_fps, n_samples):
    """Громкость по окнам той же частоты, что и выборка кадров."""
    win = 1.0 / sample_fps
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0",
         "-af", f"astats=metadata=1:reset={max(1, int(win * 100))},"
                f"ametadata=print:key=lavfi.astats.Overall.RMS_level",
         "-f", "null", "-"],
        capture_output=True)
    vals = []
    for line in r.stderr.decode(errors="replace").splitlines():
        if "RMS_level=" in line:
            try:
                vals.append(10 ** (float(line.split("=")[-1]) / 20))
            except ValueError:
                pass
    if not vals:
        return [1.0] * n_samples
    return [vals[min(len(vals) - 1, int(i * len(vals) / max(1, n_samples)))]
            for i in range(n_samples)]


def annotate_plan(edit_plan_path, sample_fps=4.0, force=False):
    """Дописать в план события reframe по траектории активного говорящего.

    Существующие punch_in/jump_cut не трогаются: автотрекинг отвечает только за то,
    куда смотрит кадр по горизонтали, а крупность — осознанное решение visual-director.
    """
    import filtergraph
    import workfile

    edit_plan_path = resolve_repo_path(edit_plan_path)
    plan = load_json(edit_plan_path)
    work = _work_file_for(plan, force=force)

    frames = detect_faces(work, sample_fps=sample_fps)
    if not frames:
        log("autoframe: кадры не прочитаны, план не изменён")
        return edit_plan_path

    rms = _audio_rms(work, sample_fps, len(frames))
    active = [pick_active(fr, level) for fr, level in zip(frames, rms)]
    cuts = detect_scene_cuts(work)
    log(f"autoframe: склеек камер в окне: {len(cuts)}")

    src_w = plan["source"].get("width", 1920)
    src_h = plan["source"].get("height", 1080)
    rs = plan.get("render_settings", {})
    crop_w = filtergraph.even(src_h * rs.get("width", 1080) / rs.get("height", 1920))
    same_px = crop_w * SAME_PERSON_FRAC

    win_start, _ = workfile.window_bounds(plan["speech_edits"])
    events = [e for e in plan.get("visual_events", [])
              if not (e.get("type") == "reframe"
                      and (e.get("reason") or "").startswith("autoframe"))]

    cum = 0.0
    for seg in plan["speech_edits"]:
        s = seg["source_start_sec"] - win_start
        e = seg["source_end_sec"] - win_start
        # Время выборки берётся из самого кадра, а не как номер / sample_fps: шаг
        # выборки округляется до целых кадров (25 fps -> каждый 6-й = 0.24 с), и к концу
        # 100-секундного окна кроп смотрел в кадр четырьмя секундами раньше речи.
        for a, b in shot_ranges(s, e, cuts):
            in_shot = [(fr, face) for fr, face in zip(frames, active) if a <= fr["t"] < b]
            samples = [(fr["t"], face["cx"] if face else None) for fr, face in in_shot]
            # Линия глаз берётся медианой по шоту: у говорящей головы она почти
            # постоянна, а отдельные кадры детектор может промахнуть.
            eyes = [face["eye_y"] / src_h for _, face in in_shot
                    if face and face.get("eye_y") is not None]
            eye_y = _median(eyes) if eyes else None
            events.extend(shot_events(frame_shot(samples, a, same_px=same_px),
                                      b, seg["id"], cum - s, src_w, crop_w,
                                      eye_y=eye_y))
        cum += e - s

    plan["visual_events"] = events
    plan["project"]["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_json(edit_plan_path, plan)
    log(f"autoframe: записано {sum(1 for e in events if e['type'] == 'reframe')} событий reframe")
    return edit_plan_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video", nargs="?", help="видеофайл для пробного детекта")
    ap.add_argument("--plan", help="дописать reframe-события в edit plan")
    ap.add_argument("--sample-fps", type=float, default=4.0)
    args = ap.parse_args()

    if args.plan:
        annotate_plan(args.plan, args.sample_fps)
        return
    if not args.video:
        ap.error("укажите видеофайл или --plan")
    frames = detect_faces(args.video, args.sample_fps)
    with_faces = sum(1 for f in frames if f["faces"])
    log(f"кадров с лицом: {with_faces}/{len(frames)}")


if __name__ == "__main__":
    main()
