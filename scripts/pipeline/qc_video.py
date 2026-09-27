#!/usr/bin/env python
"""shorts-qc deterministic backend — только измеримые технические проверки.

Это не полный QC (полный QC — editorial pass у shorts-qc skill, LLM смотрит превью
и оценивает hook/retention/clarity/pacing/visuals/subtitles/audio/payoff). Этот скрипт
даёт объективные числа, на которые тот pass опирается:
    - resolution/fps совпадают с render_settings;
    - чёрные кадры (blackdetect);
    - неожиданная тишина (silencedetect) — длинные паузы, которые retention-editor
      должен был вырезать;
    - integrated loudness (ebur128) относительно target_lufs.

Usage:
    python qc_video.py output/interview_short03.mp4 --json-out temp/interview_short03_qc.json
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_json, ffprobe_json, log, resolve_repo_path, run, save_json

SILENCE_NOISE_DB = "-35dB"
SILENCE_MIN_DUR = 1.5   # секунд — тишина короче считается естественной паузой, не проблемой
BLACK_MIN_DUR = 0.3
# Интерфейс YouTube Shorts (описание, кнопки, аватар) занимает примерно нижние
# 320 px кадра 1080x1920. Текст под ним зрителю просто не виден.
SHORTS_UI_BOTTOM_PX = 320


def check_subtitle_safe_area(ass_path, width=1080, height=1920):
    """Нижний отступ субтитров должен быть больше зоны интерфейса Shorts.

    Берётся худший стиль из файла, а не первый: достаточно одного стиля с малым
    отступом, чтобы часть текста ушла под кнопки.

    Зона в 320 px существует только у вертикального плеера Shorts. У горизонтального
    лонга поверх кадра лежит лишь панель прогресса, и требовать от него отступ
    в треть высоты бессмысленно — проверка тогда пропускается.
    """
    import io

    if width >= height:
        return {"ok": True, "margin_v": None,
                "message": "горизонтальный формат — зоны интерфейса Shorts нет, проверять нечего"}

    text = io.open(ass_path, "r", encoding="utf-8").read()
    margins = []
    columns = None
    for line in text.splitlines():
        # Число колонок в стиле задаётся строкой Format:, а не фиксировано — читаем её,
        # иначе позиции полей угадываются и проверка молча смотрит не на то поле.
        if line.startswith("Format:") and columns is None and "Alignment" in line:
            columns = [c.strip() for c in line[len("Format:"):].split(",")]
            continue
        if not line.startswith("Style:") or not columns:
            continue
        fields = [f.strip() for f in line[len("Style:"):].split(",")]
        if len(fields) != len(columns):
            continue
        row = dict(zip(columns, fields))
        try:
            alignment, margin_v = int(row["Alignment"]), int(row["MarginV"])
        except (KeyError, ValueError):
            continue
        # MarginV отсчитывается от того края, к которому прижат стиль. Проверять
        # имеет смысл только прижатые к низу (alignment 1-3): у верхних (7-9) те же
        # 180 px означают отступ сверху и к зоне интерфейса отношения не имеют,
        # у средних (4-6) MarginV вообще не влияет на положение.
        if alignment in (1, 2, 3):
            margins.append(margin_v)
    if not margins:
        return {"ok": True, "margin_v": None,
                "message": "стилей, прижатых к низу кадра, нет — проверять нечего"}
    worst = min(margins)
    ok = worst >= SHORTS_UI_BOTTOM_PX
    return {
        "ok": ok,
        "margin_v": worst,
        "message": (f"нижний отступ {worst}px меньше зоны интерфейса Shorts "
                    f"({SHORTS_UI_BOTTOM_PX}px) — текст перекроется кнопками"
                    if not ok else f"нижний отступ {worst}px — в безопасной зоне"),
    }


def check_seam_artifacts(video_path):
    """Чёрные кадры и вставленная тишина на стыках.

    До перехода на сборку одним графом каждый стык давал 20-45 мс тишины: сегменты
    кодировались в отдельные файлы, и AAC-энкодер дописывал priming padding в начало
    каждого. Порог здесь намного жёстче основной проверки тишины — ищем именно
    короткие технические дыры, а не смысловые паузы.

    Один участок тишины допустим: им может быть обложка или CTA-плашка в конце.

    Порог именно -70 dB, а не -50: priming padding — это буквально нулевые сэмплы,
    а на -50 dB в счёт попадали микропаузы речи в те моменты, когда подложка ещё
    прижата сайдчейном (музыка -24 dB минус ~20 dB дакинга уходит под -50 dB).
    На десятиминутном лонге таких пауз набирается несколько, и проверка падала
    на чистом ролике.
    """
    r = run(["ffmpeg", "-v", "info", "-i", resolve_repo_path(video_path),
             "-vf", "blackdetect=d=0.05:pic_th=0.98",
             "-af", "silencedetect=noise=-70dB:d=0.02",
             "-f", "null", "-"], check=False)
    err = r.stderr.decode(errors="replace")
    blacks = re.findall(r"black_start:(\d+\.?\d*)", err)
    silences = re.findall(r"silence_start: (\d+\.?\d*)", err)
    return {
        "ok": not blacks and len(silences) <= 1,
        "black_frames": blacks,
        "silences": silences,
        "message": f"чёрных участков: {len(blacks)}, участков тишины: {len(silences)}",
    }


def check_av_drift(video_path):
    """Рассинхрон видео и звука: расхождение длительностей дорожек.

    Значимое расхождение означает, что склейка вставила или потеряла сэмплы —
    к концу ролика губы разъедутся с голосом.
    """
    info = ffprobe_json(resolve_repo_path(video_path))
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    dv = float(v.get("duration") or info["format"]["duration"])
    da = float(a.get("duration") or info["format"]["duration"])
    drift = abs(dv - da)
    return {
        "ok": drift < 0.1,
        "drift_sec": round(drift, 3),
        "message": f"расхождение дорожек {drift:.3f}с",
    }


def check_resolution_fps(path, expected_width=1080, expected_height=1920, min_fps=24):
    data = ffprobe_json(path)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    width, height = int(v["width"]), int(v["height"])
    fps_raw = v.get("avg_frame_rate", "0/1")
    num, den = fps_raw.split("/")
    fps = float(num) / float(den) if float(den) else 0
    duration = float(data["format"]["duration"])
    return {
        "width": width, "height": height, "fps": round(fps, 2), "duration_sec": round(duration, 2),
        # мастер рендерится в 4K — ровно вдвое больше разрешения плана (docs/render.md),
        # а лонг с inherit_source_resolution — в 1440p и выше; пропорции при этом те же
        "resolution_ok": (width >= expected_width and height >= expected_height
                          and abs(width / height - expected_width / expected_height) < 0.01),
        "fps_ok": fps >= min_fps,
    }


def detect_black_frames(path):
    result = run([
        "ffmpeg", "-nostats", "-i", path,
        "-vf", f"blackdetect=d={BLACK_MIN_DUR}:pic_th=0.98",
        "-an", "-f", "null", "-",
    ], timeout=600, check=False)
    stderr = result.stderr.decode(errors="replace")
    events = re.findall(r"black_start:(\d+\.?\d*) black_end:(\d+\.?\d*) black_duration:(\d+\.?\d*)", stderr)
    return [{"start": float(s), "end": float(e), "duration": float(d)} for s, e, d in events]


def detect_silence(path):
    result = run([
        "ffmpeg", "-nostats", "-i", path,
        "-af", f"silencedetect=noise={SILENCE_NOISE_DB}:d={SILENCE_MIN_DUR}",
        "-vn", "-f", "null", "-",
    ], timeout=600, check=False)
    stderr = result.stderr.decode(errors="replace")
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?\d+\.?\d*)", stderr)]
    ends_durs = re.findall(r"silence_end:\s*(-?\d+\.?\d*)\s*\|\s*silence_duration:\s*(-?\d+\.?\d*)", stderr)
    events = []
    for i, start in enumerate(starts):
        if i < len(ends_durs):
            end, dur = ends_durs[i]
            events.append({"start": start, "end": float(end), "duration": float(dur)})
    return events


def measure_loudness(path):
    result = run([
        "ffmpeg", "-nostats", "-i", path, "-af", "ebur128=framelog=quiet",
        "-f", "null", "-",
    ], timeout=600, check=False)
    stderr = result.stderr.decode(errors="replace")
    m = re.findall(r"I:\s*(-?\d+\.?\d*)\s*LUFS", stderr)
    return float(m[-1]) if m else None


def still_ranges(plan, duration):
    """Отрезки, где тишина законна: обложка в начале и финальная карточка в конце."""
    rs = (plan or {}).get("render_settings") or {}
    out = []
    cover = rs.get("cover_frame") or {}
    if cover.get("enabled"):
        out.append((0.0, float(cover.get("duration_sec", 2.5))))
    tail = rs.get("tail_card") or {}
    if tail.get("enabled"):
        out.append((duration - float(tail.get("duration_sec", 2.5)), duration))
    return out


def outside(events, ranges, slack=0.3):
    """События тишины, не лежащие целиком внутри законных отрезков."""
    return [e for e in events
            if not any(a - slack <= e["start"] and e["end"] <= b + slack for a, b in ranges)]


def run_qc(path, expected_width=1080, expected_height=1920, target_lufs=-14,
           ass_path=None, plan=None):
    path = resolve_repo_path(path)
    if plan:
        rs = plan.get("render_settings") or {}
        expected_width = rs.get("width", expected_width)
        expected_height = rs.get("height", expected_height)
        target_lufs = rs.get("target_lufs", target_lufs)
    log(f"probing {path}")
    res = check_resolution_fps(path, expected_width, expected_height)
    log("checking for black frames...")
    black = detect_black_frames(path)
    log("checking for unexpected silence...")
    silence = detect_silence(path)
    silence = outside(silence, still_ranges(plan, res["duration_sec"]))
    log("measuring loudness...")
    loudness = measure_loudness(path)
    log("checking seams and a/v sync...")
    seams = check_seam_artifacts(path)
    drift = check_av_drift(path)

    # .ass лежит рядом в temp/ под именем клипа — если его нет, проверять нечего
    if ass_path is None:
        guess = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "..", "temp",
                             os.path.splitext(os.path.basename(path))[0] + ".ass")
        guess = os.path.normpath(guess)
        ass_path = guess if os.path.isfile(guess) else None
    safe_area = (check_subtitle_safe_area(ass_path, res["width"], res["height"])
                 if ass_path else None)

    report = {
        "resolution_ok": res["resolution_ok"],
        "fps_ok": res["fps_ok"],
        "width": res["width"], "height": res["height"], "fps": res["fps"],
        "duration_sec": res["duration_sec"],
        "has_black_frames": len(black) > 0,
        "black_frame_events": black,
        "has_unexpected_silence": len(silence) > 0,
        "silence_events": silence,
        "loudness_lufs": loudness,
        "loudness_within_1lu_of_target": (abs(loudness - target_lufs) <= 1.0) if loudness is not None else None,
        "seam_artifacts": seams,
        "av_drift": drift,
        "subtitle_safe_area": safe_area,
    }
    report["pass"] = bool(
        report["resolution_ok"] and report["fps_ok"]
        and not report["has_unexpected_silence"]
        and seams["ok"] and drift["ok"]
        and (safe_area is None or safe_area["ok"])
    )
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--target-lufs", type=float, default=-14)
    ap.add_argument("--json-out")
    ap.add_argument("--plan", help="edit plan ролика: разрешение и громкость берутся из него, "
                                   "тишина на обложке и финальной карточке не считается дефектом")
    ap.add_argument("--ass", help="путь к .ass для проверки safe area "
                                  "(по умолчанию ищется temp/<имя ролика>.ass)")
    args = ap.parse_args()

    try:
        plan = load_json(resolve_repo_path(args.plan)) if args.plan else None
        report = run_qc(args.video, args.width, args.height, args.target_lufs, args.ass, plan)
    except Exception as e:
        log(f"FAILED: {e}")
        sys.exit(1)

    log(f"resolution={report['width']}x{report['height']} ok={report['resolution_ok']}, "
        f"fps={report['fps']} ok={report['fps_ok']}, "
        f"black_frames={len(report['black_frame_events'])}, "
        f"silence_events={len(report['silence_events'])}, "
        f"loudness={report['loudness_lufs']} LUFS")
    log(f"стыки: {report['seam_artifacts']['message']}; "
        f"{report['av_drift']['message']}; "
        f"субтитры: {report['subtitle_safe_area']['message'] if report['subtitle_safe_area'] else 'нет .ass рядом'}")
    log(f"technical QC: {'PASS' if report['pass'] else 'FAIL'}")

    if args.json_out:
        save_json(resolve_repo_path(args.json_out), report)
        log(f"wrote {args.json_out}")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
