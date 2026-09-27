#!/usr/bin/env python
"""ffmpeg-editor — исполнитель edit plan. Редакторских решений не принимает,
физически собирает ролик по уже утверждённому edit-plans/<clip_id>.json.

Несмотря на имя, это общее рендер-ядро: формат берётся из render_settings, поэтому
тем же скриптом собираются и вертикальные Shorts (1080×1920), и горизонтальные лонги
(1920×1080 или 3840×2160 при 4K-исходнике) — см. docs/pipeline.md, раздел «Лонг».

Пайплайн:
  1. workfile.extract_many — вырезает рабочие окна вокруг кусков речи (иначе split в
     графе заставляет ffmpeg декодировать весь файл целиком). У Shorts окно обычно
     одно, у лонга их несколько — каждое становится отдельным входом того же графа;
  2. filtergraph.build_graph — ОДИН filter_complex: каждый субклип своим входом
     (`-ss/-to`, без split — иначе 4K-кадры копятся в памяти), кроп 9:16, плавные
     наезды, склейка видео и звука фильтром concat. Промежуточных mp4 между
     сегментами нет — именно они раньше давали рывок ровно на стыке (замерено:
     интервал между кадрами в точке склейки был 0.0667с вместо 0.0333с) и
     деградацию картинки от шести перекодов подряд;
  3. голосовая цепочка + бипы + музыка с сайдчейн-дакингом (audiochain);
  4. цветокор и прожиг субтитров хвостом того же графа — единственный финальный
     энкод (итого два перекода на кадр: рабочее окно и финал).

Usage:
    python render_short.py edit-plans/interview_short03.json
    python render_short.py edit-plans/interview_short03.json --keep-work
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audiochain
import filtergraph
import generate_subtitles
import workfile
from common import (ASSETS_DIR, FONTS_DIR, OUTPUT_DIR, TEMP_DIR, ffprobe_json, load_json,
                    log, resolve_repo_path, run, save_json, to_repo_relative)

LUT_DIR = os.path.join(ASSETS_DIR, "luts")


def _probe_dims(path):
    info = ffprobe_json(path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    return int(v["width"]), int(v["height"])


def _asset_path(assets, ref):
    for a in assets:
        if a.get("id") == ref and a.get("path"):
            p = resolve_repo_path(a["path"])
            if os.path.isfile(p):
                return p
    return None


def _music_input(audio_events, assets):
    """Путь к музыке и её параметры, если музыка действительно есть на диске."""
    for ev in audio_events:
        if ev.get("type") != "music_bed":
            continue
        p = _asset_path(assets, ev.get("asset_ref"))
        if p:
            duck = any(e.get("type") == "music_duck" for e in audio_events)
            return p, ev.get("gain_db", -18), duck
    return None, None, False


def _music_window(audio_events, speech_edits):
    """Окно звучания подложки по шкале речи: (start, end) или (None, None).

    music_bed.start_sec > 0 — музыка включается событием после хука; end_sec раньше
    конца речи — music-stop перед панчлайном. Совпадающее с речью окно — значит
    подложка на всю длину, гейт не нужен.
    """
    total = sum(float(s["source_end_sec"]) - float(s["source_start_sec"]) for s in speech_edits)
    for ev in audio_events:
        if ev.get("type") != "music_bed":
            continue
        start = float(ev.get("start_sec", 0) or 0)
        end = float(ev.get("end_sec", total) or total)
        return (start if start > 0.05 else None), (end if end < total - 0.05 else None)
    return None, None


def _music_windows(audio_events):
    """Окна звучания подложки из music_bed.windows: [(start, end, level)] или None.

    Окна по шкале речи, как субтитры. Пересекающиеся окна одной громкости склеиваются
    (два полных окна внахлёст дали бы двойную громкость); окна разной громкости остаются
    раздельными — их встречные подъём и спад дают кроссфейд, например CTA под голосом ->
    громче на заставке, где голоса нет.
    """
    for ev in audio_events:
        if ev.get("type") != "music_bed" or not ev.get("windows"):
            continue
        wins = sorted((float(w[0]), float(w[1]), float(w[2]) if len(w) > 2 else 1.0)
                      for w in ev["windows"] if float(w[1]) > float(w[0]))
        merged = []
        for s, e, lvl in wins:
            if merged and s <= merged[-1][1] and abs(merged[-1][2] - lvl) < 1e-6:
                ps, pe, pl = merged[-1]
                merged[-1] = (ps, max(pe, e), pl)
            else:
                merged.append((s, e, lvl))
        return merged or None
    return None


# Цветокор — мягкие пресеты, чтобы ролик не читался как сырой перезалив исходника.
# Значения подобраны так, чтобы кожа оставалась естественной: это не «фильтр», а
# лёгкий контраст/насыщенность/сдвиг баланса плюс виньетка к краям кадра.
COLOR_GRADES = {
    "cinematic_warm": ("eq=contrast=1.07:saturation=1.10:gamma=0.98,"
                       "colorbalance=rs=0.04:gs=0.00:bs=-0.05:rm=0.02:bm=-0.03,"
                       "vignette=angle=PI/4.6"),
    "cinematic_cool": ("eq=contrast=1.08:saturation=1.05:gamma=0.98,"
                       "colorbalance=rs=-0.03:bs=0.05:rm=-0.02:bm=0.03,"
                       "vignette=angle=PI/4.6"),
    "punchy":         ("eq=contrast=1.12:saturation=1.18:brightness=0.01,"
                       "unsharp=5:5:0.4:5:5:0.0,vignette=angle=PI/4.2"),
    "mono_red":       ("hue=s=0.15,eq=contrast=1.15:gamma=0.96,"
                       "vignette=angle=PI/4.4"),
}


def color_grade_filter(name):
    """Строка видеофильтров для пресета цветокора или None, если он не задан."""
    if not name or name == "none":
        return None
    if name not in COLOR_GRADES:
        raise RuntimeError(f"неизвестный color_grade '{name}', "
                           f"доступны: {', '.join(COLOR_GRADES)}")
    return COLOR_GRADES[name]


def _ff_path(path):
    """Путь в аргумент фильтра: прямые слэши и экранированное двоеточие диска."""
    return path.replace("\\", "/").replace(":", "\\:")


def scrim_graph(label_in, rs, width, height, start_label=0):
    """Затемнение нижней части кадра под субтитрами.

    Белый текст с обводкой читается на тёмной студии, но на светлом фоне (жёлтая стена
    записи 31.08, окно на общем плане) обводка работает на пределе. Приём из гайда по
    нарезкам: затемнить низ кадра на всю длину ролика, одинаково на всех кусках.

    Градиент имитируется тремя полосами drawbox с нарастающей альфой снизу вверх —
    это дешевле, чем geq или наложение картинки, и на 4K не стоит ничего заметного.

    render_settings.subtitle_scrim: число 0..1 (сила) либо объект
    {"strength": 0.45, "height_frac": 0.25}. Пусто или 0 — фильтр не добавляется,
    поведение прежнее.
    Возвращает (части графа, выходная метка).
    """
    cfg = rs.get("subtitle_scrim")
    if not cfg:
        return [], label_in
    if isinstance(cfg, (int, float)):
        cfg = {"strength": float(cfg)}
    strength = min(1.0, max(0.0, float(cfg.get("strength", 0.45))))
    if strength <= 0:
        return [], label_in
    height_frac = min(0.6, max(0.05, float(cfg.get("height_frac", 0.25))))

    band_h = max(2, int(round(height * height_frac / 3)))
    parts, cur, k = [], label_in, start_label
    # Снизу вверх: нижняя полоса самая плотная, верхняя еле заметна.
    for i, factor in enumerate((1.0, 0.62, 0.28)):
        y = height - band_h * (i + 1)
        alpha = round(strength * factor, 3)
        parts.append(f"[{cur}]drawbox=x=0:y={y}:w={width}:h={band_h}:"
                     f"color=black@{alpha}:t=fill[sc{k}]")
        cur = f"sc{k}"
        k += 1
    return parts, cur


def grade_graph(label_in, rs, lut_dir):
    """Хвост графа: пресет цветокора → LUT со смешиванием → зерно.

    LUT (render_settings.lut — имя файла в assets/luts без .cube или путь) не кладётся
    в лоб: split → lut3d → blend с оригиналом на lut_strength (0..1). Так один и тот же
    файл на 0.4 — деликатная коррекция, на 0.8 — явный киношный грейд, и кожа не
    уезжает в оранжевый. film_grain (0..1) — шум только по яркости (c0), чтобы не было
    цветной крошки; он смягчает «цифровую» резкость 4K.
    Возвращает (список частей графа, выходная метка). Список пуст, если ничего не задано.
    """
    parts, cur, k = [], label_in, 0

    def step(filters):
        nonlocal cur, k
        parts.append(f"[{cur}]{filters}[g{k}]")
        cur = f"g{k}"
        k += 1

    preset = color_grade_filter(rs.get("color_grade"))
    if preset:
        step(preset)

    lut = rs.get("lut")
    if lut:
        path = lut if os.path.isabs(lut) or lut.lower().endswith(".cube") \
            else os.path.join(lut_dir, f"{lut}.cube")
        if not os.path.isfile(path):
            raise RuntimeError(f"LUT не найден: {path} (сгенерировать: make_luts.py)")
        strength = float(rs.get("lut_strength", 0.6))
        strength = min(1.0, max(0.0, strength))
        parts.append(f"[{cur}]split[lutA{k}][lutB{k}]")
        parts.append(f"[lutB{k}]lut3d=file='{_ff_path(path)}':interp=tetrahedral[lutG{k}]")
        parts.append(f"[lutA{k}][lutG{k}]blend=all_mode=normal:all_opacity={strength:.2f}[g{k}]")
        cur = f"g{k}"
        k += 1

    grain = float(rs.get("film_grain", 0) or 0)
    if grain > 0:
        step(f"noise=c0s={int(round(min(1.0, grain) * 14))}:c0f=t+u")

    return parts, cur


def encoder_args(video_codec, rs, fps):
    """Параметры финального энкода под требования YouTube.

    YouTube перекодирует всё, что загружено, и чем беднее вход, тем хуже выход:
    для 1080p он рекомендует 8 Мбит/с и выше, для 2160p — 35-45. CRF без нижней
    планки давал 5-6 Мбит/с на говорящей голове, а ещё три перекода подряд —
    отсюда мыло. Здесь CRF ниже, preset медленнее, GOP = 2 с (закрытый), профиль
    High и явный yuv420p, как в рекомендациях YouTube для загрузки.
    """
    gop = str(int(round(fps * 2)))
    if video_codec == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq",
                "-rc", "vbr", "-cq", str(rs.get("cq", 19)), "-b:v", "0",
                "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-g", gop, "-bf", "3", "-spatial-aq", "1", "-temporal-aq", "1"]
    threads = os.environ.get("SHORTS_THREADS")
    return ["-c:v", video_codec, "-preset", rs.get("preset", "slow"),
            "-crf", str(rs.get("crf", 16)),
            *(["-threads", threads] if threads else []),
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-g", gop, "-keyint_min", gop, "-sc_threshold", "0", "-bf", "2"]


def render(edit_plan_path, use_gpu=False, keep_work=False):
    edit_plan_path = resolve_repo_path(edit_plan_path)
    plan = load_json(edit_plan_path)
    clip_id = plan["project"]["clip_id"]
    source_path = resolve_repo_path(plan["source"]["path"])
    speech_edits = plan["speech_edits"]
    visual_events = plan.get("visual_events", [])
    audio_events = plan.get("audio_events", [])
    assets = plan.get("assets", [])
    rs = plan.get("render_settings", {})

    if not speech_edits:
        raise RuntimeError("edit plan не содержит speech_edits — собирать нечего")

    width = rs.get("width", 1080)
    height = rs.get("height", 1920)
    plan_w, plan_h = width, height
    if rs.get("inherit_source_resolution"):
        # лонг из 4K-исходника отдаём в 4K: YouTube на 1440p+ кодирует VP9 даже
        # для роликов без просмотров, а 1080p ждёт AVC с заметно худшей картинкой
        sw, sh = int(plan["source"]["width"]), int(plan["source"]["height"])
        # 1080p-исходник (типичная запись подкаста) всё равно отдаём не ниже
        # 1440p: порог VP9 у YouTube — 1440p, а лёгкий апскейл говорящей головы
        # с lanczos визуально ничего не портит
        min_h = int(rs.get("min_output_height", 1440))
        if sh < min_h:
            sw, sh = int(round(sw * min_h / sh)) // 2 * 2, min_h
        if width >= height:
            width, height = sw, sh
        else:
            height = sh
            width = int(round(sh * 9 / 16)) // 2 * 2
        log(f"разрешение наследуется от исходника: {width}x{height}")
    elif rs.get("master_4k", True) and max(width, height) < 2160:
        # Мастер в 4K по умолчанию (11.09.2026): YouTube кодирует 4K-загрузки в VP9 с
        # заметно большим битрейтом, чем 1080p, и ролик выглядит резче даже на телефоне.
        # Для 4K-исходников кроп идёт в родном разрешении без потери деталей, 1080p-исходники
        # апскейлятся lanczos'ом. Отключить: render_settings.master_4k = false. План с
        # inherit_source_resolution решает разрешение сам и сюда не попадает.
        width, height = width * 2, height * 2
        log(f"мастер 4K: целевое разрешение {width}x{height}")
    fps = float(rs.get("fps", plan["source"].get("fps", 30)) or 30)
    target_lufs = rs.get("target_lufs", -14)

    # 1. рабочие окна. У Shorts оно почти всегда одно; у лонга куски речи разбросаны
    # по всему исходнику, и одно окно означало бы перекод всего файла целиком.
    # Сегменты с source_path (CTA-зазывалка из cta/) окон не требуют — они
    # читаются напрямую из своего файла отдельными входами после окон.
    main_edits = [s for s in speech_edits if not s.get("source_path")]
    if not main_edits:
        raise RuntimeError("все speech_edits внешние — нужен хотя бы один сегмент исходника")
    windows = workfile.window_clusters(main_edits)
    works = workfile.extract_many(source_path, clip_id, windows)
    src_w, src_h = _probe_dims(works[0])
    # 1080p-исходник при мастере 4K собирается в размере плана и поднимается до мастера одним
    # lanczos перед цветокором: 4K-ветки на каждый субклип только съедали память
    asm_w, asm_h = filtergraph.assembly_size((width, height), (plan_w, plan_h), (src_w, src_h))
    if (asm_w, asm_h) != (width, height):
        log(f"сборка графа в {asm_w}x{asm_h}, до {width}x{height} — одним lanczos перед цветокором")

    external = []
    for p in filtergraph.external_sources(speech_edits):
        full = resolve_repo_path(p)
        if not os.path.isfile(full):
            raise RuntimeError(f"внешний сегмент ссылается на отсутствующий файл: {p}")
        external.append(full)
    if external:
        log(f"внешних врезок со звуком: {len(external)}")
    # маппинг «путь из плана → полный путь» для assign_windows
    ext_keys = filtergraph.external_sources(speech_edits)

    # тайминги плана заданы в координатах исходника — переводим в координаты окон
    subclips = filtergraph.assign_windows(
        filtergraph.build_subclips(speech_edits, visual_events), windows, ext_keys)
    # границы на сетку кадров: иначе concat добивает звук тишиной до целого кадра видео,
    # и на лонге план уезжает от звука на доли секунды (см. filtergraph.snap_to_frames)
    subclips = filtergraph.snap_to_frames(subclips, fps)
    works_all = works + external

    # 2. обложка и врезки — дополнительные входы ffmpeg
    cover_cfg = rs.get("cover_frame") or {}
    cover_path = None
    cover_sec = float(cover_cfg.get("duration_sec", 2.5))
    if cover_cfg.get("enabled") and cover_cfg.get("image_path"):
        p = resolve_repo_path(cover_cfg["image_path"])
        if os.path.isfile(p):
            cover_path = p
            log(f"обложка вклеивается первым кадром на {cover_sec:g}с")
        else:
            log(f"обложка указана, но файла нет: {p} — рендерю без неё")

    # хвостовая CTA-плашка: удлиняет ролик, а не ложится поверх панчлайна
    tail_cfg = rs.get("tail_card") or {}
    tail_path = None
    tail_sec = float(tail_cfg.get("duration_sec", 2.5))
    if tail_cfg.get("enabled") and tail_cfg.get("image_path"):
        p = resolve_repo_path(tail_cfg["image_path"])
        if os.path.isfile(p):
            tail_path = p
            log(f"CTA-плашка в конце на {tail_sec:g}с")
        else:
            log(f"CTA-плашка указана, но файла нет: {p} — рендерю без неё")

    expected = sum(s["source_end_sec"] - s["source_start_sec"] for s in speech_edits)
    if cover_path:
        expected += cover_sec
    if tail_path:
        expected += tail_sec
    # таймаут ffmpeg растёт с длиной ролика: 4K preset slow на 3 CPU VDS идёт медленнее
    # реального времени, и фиксированный час обрывал лонг посреди финального прохода
    enc_timeout = max(3600, int(expected * 30))

    overlays = []
    for ev in visual_events:
        if ev.get("type") not in ("broll", "image_insert", "screenshot"):
            continue
        p = _asset_path(assets, ev.get("asset_ref"))
        if not p:
            log(f"пропускаю врезку {ev.get('asset_ref')} — файла нет на диске")
            continue
        overlays.append({
            "path": p,
            "start_sec": ev["start_sec"],
            "end_sec": ev["end_sec"],
            "mode": ev.get("insert_mode", "fullscreen"),
            "corner": ev.get("insert_corner"),
            "is_video": p.lower().endswith((".mp4", ".mkv", ".mov")),
        })
    if overlays:
        log(f"врезок: {len(overlays)}")

    music_path, gain_db, duck = _music_input(audio_events, assets)
    # бипы идут по шкале речи (как субтитры) и сдвигаются на обложку так же, как они
    bleeps = [(float(ev["start_sec"]), float(ev["end_sec"]))
              for ev in audio_events if ev.get("type") == "bleep"]
    bleep_offset = cover_sec if cover_path else 0.0
    if bleeps:
        log(f"запикиваю {len(bleeps)} окон(а)")

    n_sub = len(subclips)
    graph = filtergraph.build_graph(
        subclips, width=asm_w, height=asm_h, fps=fps,
        src_height=src_h, src_width=src_w,
        cover_path=cover_path, cover_sec=cover_sec,
        tail_path=tail_path, tail_sec=tail_sec, overlays=overlays,
        # порядок входов строго: субклипы, музыка, обложка, CTA-плашка, врезки
        first_extra_input=n_sub + (1 if music_path else 0),
    )
    clip_inputs = filtergraph.subclip_inputs(subclips, works_all)
    log(f"граф собран: {len(subclips)} субклип(ов) из {len(works)} рабочего(их) окна(он)")

    # 3. один граф на всю сборку + звук
    if music_path:
        music_start, music_end = _music_window(audio_events, speech_edits)
        music_windows = _music_windows(audio_events)
        if cover_path:
            music_start = (music_start or 0.0) + cover_sec if music_start is not None else None
            music_end = music_end + cover_sec if music_end is not None else None
            if music_windows:
                music_windows = [(s + cover_sec, e + cover_sec, lvl) for s, e, lvl in music_windows]
        if music_windows:
            log("музыка окнами: " + ", ".join(f"{s:.1f}-{e:.1f}" for s, e, _ in music_windows))
        elif music_start is not None or music_end is not None:
            log(f"музыка: вход {music_start} с, стоп {music_end} с")
        # голос нормализуется ДО микса — иначе уровень музыки поплывёт за динамикой речи
        voice = audiochain.voice_chain(target_lufs)
        full_graph = (
            f"{graph};[aout]{voice}[voiceclean];"
            f"{audiochain.bleep_chain(bleeps, 'voiceclean', 'voice', offset=bleep_offset)};"
            # музыка идёт входом сразу за субклипами: индекс 1 указывал бы на аудио
            # второго субклипа, а не на подложку
            f"{audiochain.music_mix(gain_db, duck, target_lufs, music_index=n_sub, music_start=music_start, music_end=music_end, music_windows=music_windows)}"
        )
        inputs = clip_inputs + ["-stream_loop", "-1", "-i", music_path]
        log(f"подмешиваю музыку {os.path.basename(music_path)} (дакинг={duck})")
    else:
        full_graph = (
            f"{graph};[aout]{audiochain.voice_chain(target_lufs)}[voiceclean];"
            f"{audiochain.bleep_chain(bleeps, 'voiceclean', 'afinal', offset=bleep_offset)}"
        )
        inputs = list(clip_inputs)
        log("музыки нет — только голосовая цепочка")

    if cover_path:
        inputs += ["-loop", "1", "-t", f"{cover_sec:.3f}", "-i", cover_path]
    if tail_path:
        inputs += ["-loop", "1", "-t", f"{tail_sec:.3f}", "-i", tail_path]
    for ov in overlays:
        if ov["is_video"]:
            inputs += ["-i", ov["path"]]
        else:
            inputs += ["-loop", "1", "-t", f"{ov['end_sec'] - ov['start_sec']:.3f}",
                       "-i", ov["path"]]

    # 3. цветокор + субтитры — хвост того же графа, а не отдельный проход.
    # Раньше сборка писалась в промежуточный mp4 (crf 16 veryfast) и субтитры
    # прожигались вторым энкодом: в сумме выходило три перекода на кадр, и на
    # 1080p ролик уходил на YouTube с ~6 Мбит/с — отсюда «пиксельное 1080p».
    final_out = os.path.join(OUTPUT_DIR, f"{clip_id}.mp4")
    # пишем во временный файл: упавший рендер не должен затирать прошлый выход
    render_tmp = os.path.join(TEMP_DIR, f"{clip_id}_render.mp4")
    video_codec = "h264_nvenc" if use_gpu else rs.get("video_codec", "libx264")
    tail_filters = []

    vlabel = "vout"
    if (asm_w, asm_h) != (width, height):
        full_graph += f";[vout]scale={width}:{height}:flags=lanczos[vup]"
        vlabel = "vup"
    # Затемнение под субтитрами ставится ДО цветокора: LUT и зерно должны лечь на
    # уже затемнённый низ, иначе на стыке полосы видна граница обработки.
    scrim_parts, vlabel = scrim_graph(vlabel, rs, width, height)
    if scrim_parts:
        full_graph += ";" + ";".join(scrim_parts)
        log(f"затемнение под субтитрами: {rs.get('subtitle_scrim')}")
    grade_parts, grade_out = grade_graph(vlabel, rs, LUT_DIR)
    if grade_parts:
        log("цветокор: " + ", ".join(
            f"{key}={rs[key]}" for key in ("color_grade", "lut", "lut_strength", "film_grain")
            if rs.get(key) not in (None, "", 0, "none")))

    ass_path = os.path.join(TEMP_DIR, f"{clip_id}.ass")
    try:
        if rs.get("subtitle_mode") == "none":
            raise RuntimeError("subtitle_mode=none — субтитры отключены планом")
        # субтитры размечены в таймлайне речи, а обложка занимает начало ролика —
        # без сдвига они пошли бы поверх обложки и разъехались с речью на cover_sec
        generate_subtitles.generate(edit_plan_path, ass_path,
                                    time_offset=cover_sec if cover_path else 0.0)
        # fontsdir вместо установки шрифта в систему: libass берёт гарнитуру прямо
        # из assets/fonts, поэтому на новой машине ничего доустанавливать не нужно.
        # Субтитры — строго после цветокора, иначе LUT перекрасит белый текст.
        tail_filters.append(f"ass=f='{_ff_path(ass_path)}':fontsdir='{_ff_path(FONTS_DIR)}'")
        log("субтитры будут прожжены")
    except RuntimeError as e:
        log(f"рендерю без субтитров: {e}")

    if grade_parts:
        full_graph += ";" + ";".join(grade_parts)
    if tail_filters:
        full_graph += f";[{grade_out}]" + ",".join(tail_filters) + "[vfinal]"
        vmap = "[vfinal]"
    else:
        vmap = f"[{grade_out}]"

    # Граф уходит файлом, а не аргументом: в Windows длина командной строки ограничена
    # ~32767 символами, и на лонге со 130+ субклипами граф один занимает больше — ffmpeg
    # даже не запускался, падало на CreateProcess с WinError 206 («слишком длинное имя
    # файла»), что читается как что угодно, только не как «граф не влез».
    graph_path = os.path.join(TEMP_DIR, f"{clip_id}_graph.txt")
    with open(graph_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(full_graph)
    log(f"граф записан в {os.path.basename(graph_path)} ({len(full_graph)} символов)")

    threads = os.environ.get("SHORTS_THREADS")
    cmd = ["ffmpeg", "-y"] + (["-filter_complex_threads", threads, "-filter_threads", threads]
                              if threads else []) + inputs + [
        "-filter_complex_script", graph_path,
        "-map", vmap, "-map", "[afinal]",
    ]
    # -shortest здесь НЕ ставить. Музыка подаётся зацикленной (-stream_loop -1), и
    # вместе с -shortest ffmpeg обрывает звуковую дорожку примерно на 3 секунды раньше
    # видео — молча, без ошибки. В ролике это последние слова панчлайна без звука.
    # Длина и так ограничена: amix=duration=first берёт длительность голоса.
    cmd += encoder_args(video_codec, rs, fps)
    # loudnorm внутри графа поднимает частоту дискретизации до 192 кГц, и без явного
    # -ar в файл уходило 96 кГц — YouTube это съест, но стандарт для видео 48 кГц
    cmd += ["-c:a", "aac", "-b:a", rs.get("audio_bitrate", "192k"), "-ar", "48000",
            "-movflags", "+faststart", render_tmp]
    run(cmd, timeout=enc_timeout)
    os.replace(render_tmp, final_out)

    if not keep_work:
        for w in works:
            if os.path.exists(w):
                os.remove(w)

    actual = float(ffprobe_json(final_out)["format"]["duration"])
    if abs(actual - expected) > max(1.0, expected * 0.05):
        log(f"ВНИМАНИЕ: ожидалось ~{expected:.1f}с, получилось {actual:.1f}с")
    else:
        log(f"длительность в норме: ожидалось ~{expected:.1f}с, получилось {actual:.1f}с")

    plan["output_path"] = to_repo_relative(final_out)
    plan["project"]["stage"] = "rendered"
    plan["project"]["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_json(edit_plan_path, plan)

    log(f"ГОТОВО -> {final_out}")
    return final_out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("edit_plan")
    ap.add_argument("--use-gpu", action="store_true",
                    help="h264_nvenc вместо libx264 (только на машине с NVIDIA)")
    ap.add_argument("--keep-work", action="store_true",
                    help="не удалять temp/<clip_id>_work.mp4 — ускоряет повторные прогоны")
    args = ap.parse_args()
    try:
        render(args.edit_plan, args.use_gpu, args.keep_work)
    except Exception as e:
        log(f"ОШИБКА: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
