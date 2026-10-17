#!/usr/bin/env python
"""Механический монтаж лонга без LLM: от глав скаута до плана, готового к рендеру.

Дешёвый режим `/long` (settings.json → long_pipeline: "lean"): редакторское решение
принимает только скаут — какие куски исходника и в каком порядке (clip.chapters).
Всё остальное, что в полном режиме делает long-editor на модели, здесь считается кодом:

- speech_edits — auto_cut по каждой главе (паузы и междометия вырезаны);
- крупность — смена общий/средний план на склейках (прячет jump cut), не чаще раза в
  HOLD_SEC, в первые 30 с — чаще; на длинной статике — медленный наезд;
- карточка «ГЛАВА N» на входе в каждую главу (make_cards.round_card);
- фразовые субтитры (build_subtitles, режим chunk);
- подложка только на хуке, швах глав и финале;
- вид ролика: LUT не тот, что в прошлых лонгах, сила LUT и зерно — свои у каждого
  ролика (детерминированно от clip_id: перезапуск даёт тот же результат);
- render_settings лонга, stage = audio_planned, валидация.

Байт в середине и финальный CTA здесь не ставятся — их, если нужны, добавляет long-editor
(`/fix <id> добавь байт`).

Usage:
    python auto_long.py edit-plans/podcast_long01.json
    python auto_long.py edit-plans/podcast_long01.json --dry-run
    python auto_long.py edit-plans/podcast_long01.json --recut --max-pause 1.0
    python auto_long.py edit-plans/podcast_long01.json --height 1440   # мало памяти на 4K
"""
import argparse
import glob
import io
import json
import os
import random
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ASSETS_DIR, EDIT_PLANS_DIR, ROOT, load_json, log, resolve_repo_path, \
    to_repo_relative, transcript_for_plan  # noqa: E402

ZOOM = 1.10           # средний план; больше на 16:9 режет второго собеседника по краю
HOLD_EARLY_SEC = 5.0  # первые 30 с — смена крупности не реже, чем раз в 10–15 с
HOLD_SEC = 9.0        # дальше — не чаще раза в 9 с, иначе дёргано
STATIC_SEC = 14.0     # столько без единой склейки — медленный наезд
DRIFT = 0.05
DRIFT_EASE_MS = 3000
CARD_SEC = 1.6
MUSIC_GAIN_DB = -24
SEAM_PRE, SEAM_POST, OUTRO_SEC, HOOK_MUSIC_MAX = 3.0, 5.0, 12.0, 25.0
LUT_STRENGTH = (0.5, 0.7)
GRAIN = (0.2, 0.3)


def snap(t, fps):
    return round(round(t * fps) / fps, 3)


def timeline(speech_edits):
    """[(сегмент, начало в ролике, конец в ролике)]."""
    out, t = [], 0.0
    for seg in speech_edits:
        dur = seg["source_end_sec"] - seg["source_start_sec"]
        out.append((seg, t, t + dur))
        t += dur
    return out


def chapter_starts(tl):
    """[(номер главы, начало в ролике)] — где меняется speech_edits[].chapter."""
    starts, prev = [], None
    for seg, t0, _ in tl:
        ch = seg.get("chapter")
        if ch is not None and ch != prev:
            starts.append((ch, t0))
        prev = ch
    return starts


def plan_crops(tl, fps, zoom=ZOOM):
    """События крупности по шкале ролика. Склейки внутри главы чередуют общий и средний
    план, новая глава всегда открывается общим, долгая статика — медленный наезд."""
    events = []
    base = 1.0          # общий или средний план — то, между чем чередуем на склейках
    current = 1.0       # фактический зум с учётом наезда
    last = float("-inf")
    prev_ch = None

    def add(t, end, z, kind, ease, purpose, seg):
        events.append({"start_sec": snap(t, fps), "end_sec": snap(end, fps), "type": kind,
                       "segment_ref": seg["id"], "purpose": purpose,
                       "crop": {"crop_x": None, "zoom": round(z, 3), "ease_ms": ease}})

    for i, (seg, t0, t1) in enumerate(tl):
        ch = seg.get("chapter")
        if i == 0:
            add(t0, t1, 1.0, "talking_head", 0, "общий план на входе", seg)
            last = t0
        elif ch is not None and ch != prev_ch:
            base = current = 1.0
            add(t0, t1, 1.0, "talking_head", 0, "новая глава — общий план, сброс крупности", seg)
            last = t0
        elif t0 - last >= (HOLD_EARLY_SEC if t0 < 30 else HOLD_SEC):
            base = zoom if base == 1.0 else 1.0
            current = base
            kind = "punch_in" if base > 1.0 else "punch_out"
            add(t0, t1, base, kind, 0, "смена крупности на склейке прячет jump cut", seg)
            last = t0
        # статика без склеек: медленный наезд и обратно, пока сегмент не кончится
        t = max(last, t0) + STATIC_SEC
        while t < t1 - 1.0:
            pushed = current > base + 1e-6
            current = base if pushed else round(base + DRIFT, 3)
            add(t, t1, current, "punch_out" if pushed else "punch_in", DRIFT_EASE_MS,
                f"{STATIC_SEC:.0f} с без смены кадра — медленный наезд держит внимание", seg)
            last = t
            t += STATIC_SEC
        prev_ch = ch
    return events


def music_windows(tl, chapters):
    """Окна подложки по шкале ролика: хук, швы глав, финал."""
    if not tl:
        return []
    total = tl[-1][2]
    hook_end = max((t1 for seg, _, t1 in tl if seg.get("role") == "hook"), default=None)
    wins = [[0.0, round(min(hook_end or 20.0, HOOK_MUSIC_MAX, total), 3)]]
    for ch, t in chapter_starts(tl):
        if t <= 0.05 or (chapters and chapters[ch].get("role") == "hook"):
            continue
        wins.append([round(max(0.0, t - SEAM_PRE), 3), round(min(total, t + SEAM_POST), 3)])
    wins.append([round(max(0.0, total - OUTRO_SEC), 3), round(total, 3)])
    return wins


def recent_luts(exclude_path, n=2):
    """LUT последних n лонгов, кроме этого плана, — их не повторяем."""
    plans = [p for p in glob.glob(os.path.join(EDIT_PLANS_DIR, "*_long*.json"))
             if os.path.abspath(p) != os.path.abspath(exclude_path)]
    plans.sort(key=os.path.getmtime, reverse=True)
    out = []
    for p in plans[:n]:
        try:
            lut = (load_json(p).get("render_settings") or {}).get("lut")
        except (OSError, ValueError):
            continue
        if lut:
            out.append(os.path.splitext(os.path.basename(lut))[0])
    return out


def pick_look(clip_id, luts, recent, forced=None):
    """Свой вид у каждого ролика: LUT не из недавних, сила и зерно в пределах вкуса."""
    rng = random.Random(clip_id)
    pool = [l for l in luts if l not in recent] or list(luts)
    lut = forced or (rng.choice(sorted(pool)) if pool else None)
    return {"lut": lut,
            "lut_strength": round(rng.uniform(*LUT_STRENGTH), 2),
            "film_grain": round(rng.uniform(*GRAIN), 2)}


def fmt(t):
    t = int(round(t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def make_cards(plan, chapters, starts, fps, dry_run):
    """Карточки «ГЛАВА N» → (assets, visual_events)."""
    import make_cards as mc
    clip_id = plan["project"]["clip_id"]
    assets, events, n = [], [], 0
    for ch, t in starts:
        info = chapters[ch] if chapters and ch < len(chapters) else {}
        if info.get("role") == "hook":
            continue
        n += 1
        if t < 0.05:
            continue  # ролик открывается спикером, а не карточкой
        rel = f"temp/{clip_id}_ch{n:02d}.png"
        if not dry_run:
            os.makedirs(os.path.dirname(resolve_repo_path(rel)), exist_ok=True)
            mc.round_card(n, info.get("title", ""), "a" if n % 2 else "b",
                          label="ГЛАВА").convert("RGB").save(resolve_repo_path(rel))
        aid = f"card_ch{n:02d}"
        assets.append({"id": aid, "kind": "image", "path": rel,
                       "license_note": "make_cards.py, своя графика"})
        events.append({"start_sec": snap(t, fps), "end_sec": snap(t + CARD_SEC, fps),
                       "type": "image_insert", "asset_ref": aid, "insert_mode": "fullscreen",
                       "purpose": f"вход в главу {n}: зритель видит структуру и остаётся на шве"})
    return assets, events


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--transcript")
    ap.add_argument("--max-pause", type=float, default=0.9)
    ap.add_argument("--recut", action="store_true",
                    help="пересобрать speech_edits из clip.chapters, даже если они уже есть")
    ap.add_argument("--zoom", type=float, default=ZOOM)
    ap.add_argument("--lut", help="LUT вместо автоматического выбора (имя из assets/luts)")
    ap.add_argument("--height", type=int, choices=[1080, 1440],
                    help="фиксированная высота выхода вместо разрешения исходника (меньше памяти)")
    ap.add_argument("--no-cards", action="store_true")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    import auto_cut
    import build_subtitles
    import validate_edit_plan

    path = resolve_repo_path(a.plan)
    plan = load_json(path)
    clip = plan.get("clip") or {}
    chapters = clip.get("chapters") or []
    clip_id = plan["project"]["clip_id"]
    src = plan.get("source") or {}
    fps = float(src.get("fps") or (plan.get("render_settings") or {}).get("fps") or 30)
    tpath = a.transcript or transcript_for_plan(plan)
    transcript = load_json(resolve_repo_path(tpath))

    if chapters and (a.recut or not plan.get("speech_edits")):
        try:
            plan["speech_edits"] = auto_cut.cut_chapters(transcript, chapters, a.max_pause, fps)
        except ValueError as e:
            sys.exit(str(e))
    if not plan.get("speech_edits"):
        sys.exit("нет ни speech_edits, ни clip.chapters — сначала скаут (структура лонга)")
    src.setdefault("transcript_path", to_repo_relative(resolve_repo_path(tpath)))
    plan["source"] = src

    tl = timeline(plan["speech_edits"])
    total = tl[-1][2]
    starts = chapter_starts(tl)

    visual = plan_crops(tl, fps, a.zoom)
    assets = [x for x in plan.get("assets", [])
              if not x.get("id", "").startswith(("card_ch", "music_bed"))]
    if not a.no_cards and starts:
        card_assets, card_events = make_cards(plan, chapters, starts, fps, a.dry_run)
        assets += card_assets
        visual += card_events
    visual.sort(key=lambda e: e["start_sec"])

    subs, _, _ = build_subtitles.build(plan, transcript, "chunk", build_subtitles.MAX_CHARS,
                                       build_subtitles.MAX_DUR, build_subtitles.PAUSE_GAP)

    audio = [e for e in plan.get("audio_events", []) if e.get("type") != "music_bed"]
    tracks = sorted(glob.glob(os.path.join(ASSETS_DIR, "music", "*.mp3"))
                    + glob.glob(os.path.join(ASSETS_DIR, "music", "*.wav")))
    track = None
    if tracks and not a.no_music:
        track = random.Random(clip_id + "/music").choice(tracks)
        assets.append({"id": "music_bed", "kind": "music", "path": to_repo_relative(track),
                       "license_note": "assets/music — см. assets/music/README.md"})
        audio.append({"start_sec": 0.0, "end_sec": round(total, 3), "type": "music_bed",
                      "asset_ref": "music_bed", "gain_db": MUSIC_GAIN_DB,
                      "windows": music_windows(tl, chapters),
                      "description": "подложка только на хуке, швах глав и финале"})

    luts = [os.path.splitext(os.path.basename(p))[0]
            for p in glob.glob(os.path.join(ASSETS_DIR, "luts", "*.cube"))]
    look = pick_look(clip_id, luts, recent_luts(path), a.lut)

    rs = dict(plan.get("render_settings") or {})
    rs.update({"width": 1920, "height": 1080, "fps": fps,
               "inherit_source_resolution": True, "min_output_height": 1440,
               "cover_frame": {"enabled": False}, "subtitle_mode": "chunk", **look})
    rs.setdefault("video_codec", "libx264")
    rs.setdefault("audio_codec", "aac")
    if a.height:
        rs.update({"width": a.height * 16 // 9, "height": a.height,
                   "inherit_source_resolution": False})

    plan.update({"visual_events": visual, "subtitle_events": subs, "audio_events": audio,
                 "assets": assets, "render_settings": rs})
    toc = []
    for ch, t in starts:
        info = chapters[ch] if ch < len(chapters) else {}
        toc.append(f"{fmt(t)} {info.get('title') or ('Хук' if info.get('role') == 'hook' else f'Глава {ch}')}")
    note = "auto_long: главы (время ролика): " + " · ".join(toc) if toc else ""
    notes = [ln for ln in (plan["project"].get("notes") or "").splitlines()
             if not ln.startswith("auto_long:")]
    plan["project"]["notes"] = "\n".join(notes + ([note] if note else []))
    plan["project"]["stage"] = "audio_planned"
    plan["project"]["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    log(f"{clip_id}: {len(plan['speech_edits'])} сегментов, ролик {fmt(total)}", tag="auto_long")
    for line in toc:
        log("  " + line, tag="auto_long")
    log(f"крупность: {sum(e['type'] != 'image_insert' for e in visual)} событий, карточек "
        f"{sum(e['type'] == 'image_insert' for e in visual)}, субтитров {len(subs)}, "
        f"музыка {os.path.basename(track) if track else 'нет'}",
        tag="auto_long")
    log(f"вид: LUT {look['lut']} × {look['lut_strength']}, зерно {look['film_grain']}", tag="auto_long")
    if a.dry_run:
        log("dry-run: план не изменён", tag="auto_long")
        return

    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    errors, warnings = validate_edit_plan.validate(path)
    for w in warnings:
        log(f"WARN: {w}", tag="auto_long")
    if errors:
        for e in errors:
            log(f"FAIL: {e}", tag="auto_long")
        sys.exit(1)
    log(f"записано в {os.path.relpath(path, ROOT)}, план валиден", tag="auto_long")


if __name__ == "__main__":
    main()
