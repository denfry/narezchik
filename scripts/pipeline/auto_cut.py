#!/usr/bin/env python
"""Черновой EDL без LLM: нарезка выбранного диапазона по паузам и мусорным звукам.

Самая дорогая по токенам часть монтажа — вручную выписать десятки сегментов речи с
точными таймкодами. Механическую часть этого делает скрипт: берёт слова диапазона
clip.source_start_sec..source_end_sec, вырезает паузы длиннее --max-pause и междометия
(«э», «эм», «мм»…), ставит границы на сетку кадров и пишет speech_edits в план.
Агенту-сценаристу остаётся редакторское: переставить хук, выкинуть смысловые повторы,
поправить роли. Слова-паразиты вроде «ну», «короче», «типа» скрипт НЕ режет: иногда
это манера спикера — решает сценарист.

Лонг из нескольких кусков исходника: если в плане есть clip.chapters (диапазоны глав от
скаута), режется каждая глава по порядку, а сегменты помечаются номером главы
(`speech_edits[].chapter`) — по нему auto_long.py ставит карточки и швы.

Usage:
    python auto_cut.py edit-plans/podcast_short03.json                 # записать в план
    python auto_cut.py edit-plans/podcast_short03.json --dry-run       # только показать
    python auto_cut.py edit-plans/podcast_long01.json --max-pause 0.9  # лонгу паузы нужнее
"""
import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, log, resolve_repo_path, transcript_for_plan  # noqa: E402

FILLERS = {"э", "ээ", "эээ", "эм", "эмм", "мм", "ммм", "м", "а-а", "э-э", "хм", "uh", "um", "erm"}
PAD_BEFORE = 0.08   # запас до первого слова: не съесть атаку согласной
# запас после: whisper ставит конец слова раньше, чем затихает звук, — при 0.12 с последнее
# слово сегмента обрывалось на середине («войну», «аудиторию» на живом прогоне)
PAD_AFTER = 0.28


def bare(word):
    return re.sub(r"[^\wёЁ-]", "", word).lower()


def words_between(transcript, start, end):
    out = []
    for seg in transcript["segments"]:
        for w in seg.get("words", []):
            if w["start"] >= start - 0.01 and w["end"] <= end + 0.01:
                out.append(w)
    return out


def snap(t, fps, up=False):
    frames = t * fps
    return (int(frames) + (1 if up and frames != int(frames) else 0)) / fps


def cut(words, max_pause, fps, min_seg=0.35):
    """Слова → список сегментов [start, end, [слова]] без длинных пауз и междометий."""
    kept = [w for w in words if bare(w["word"]) not in FILLERS]
    segs = []
    for w in kept:
        if segs and w["start"] - segs[-1][1] <= max_pause:
            segs[-1][1] = w["end"]
            segs[-1][2].append(w)
        else:
            segs.append([w["start"], w["end"], [w]])
    out = []
    for i, (s, e, ws) in enumerate(segs):
        prev_end = segs[i - 1][1] if i else s - 1
        next_start = segs[i + 1][0] if i + 1 < len(segs) else e + 1
        spoken = e - s  # длина самой речи: запасы по краям огрызок длиннее не делают
        s = max(s - PAD_BEFORE, (prev_end + s) / 2)
        e = min(e + PAD_AFTER, (e + next_start) / 2)
        s, e = snap(s, fps), snap(e, fps, up=True)
        if out and spoken < min_seg:
            # огрызок из одного-двух слов приклеиваем к предыдущему — отдельный стык хуже паузы
            out[-1][1] = e
            out[-1][2] += ws
            continue
        out.append([s, e, ws])
    return out


def cut_chapters(transcript, chapters, max_pause, fps):
    """Главы лонга → speech_edits по порядку глав, с номером главы в каждом сегменте.

    Глава с role "hook" (холодное начало) даёт сегменты hook, первый сегмент остальных
    глав — chapter, последний сегмент ролика — payoff. Пустая глава (нет слов в
    диапазоне) — ошибка скаута, её не пропускаем молча.
    """
    edits = []
    for ci, ch in enumerate(chapters):
        words = words_between(transcript, ch["source_start_sec"], ch["source_end_sec"])
        if not words:
            raise ValueError(f"глава {ci} «{ch.get('title', '')}» "
                             f"[{ch['source_start_sec']}–{ch['source_end_sec']}]: нет слов")
        for j, (s, e, ws) in enumerate(cut(words, max_pause, fps)):
            if ch.get("role") == "hook":
                role = "hook"
            else:
                role = "chapter" if j == 0 else "development"
            edits.append({
                "id": f"seg{len(edits) + 1:03d}", "source_start_sec": round(s, 3),
                "source_end_sec": round(e, 3), "role": role, "chapter": ci,
                "transcript_text": " ".join(w["word"].strip() for w in ws),
                "cut_reason": "auto_cut: границы по паузам > %.2f с" % max_pause,
            })
    if edits and edits[-1]["role"] == "development":
        edits[-1]["role"] = "payoff"
    return edits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--transcript")
    ap.add_argument("--max-pause", type=float, default=0.6,
                    help="паузы длиннее вырезаются (Shorts 0.5–0.6, лонг 0.8–1.0)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    path = resolve_repo_path(a.plan)
    with io.open(path, encoding="utf-8") as f:
        plan = json.load(f)
    clip = plan.get("clip") or {}
    start, end = clip.get("source_start_sec"), clip.get("source_end_sec")
    chapters = clip.get("chapters")
    if not chapters and (start is None or end is None):
        sys.exit("в плане нет clip.source_start_sec/source_end_sec — сначала выбор момента")
    tpath = a.transcript or transcript_for_plan(plan)
    with io.open(resolve_repo_path(tpath), encoding="utf-8") as f:
        transcript = json.load(f)
    fps = float(plan.get("source", {}).get("fps") or plan.get("render_settings", {}).get("fps") or 30)

    if chapters:
        try:
            edits = cut_chapters(transcript, chapters, a.max_pause, fps)
        except ValueError as e:
            sys.exit(str(e))
        before = sum(ch["source_end_sec"] - ch["source_start_sec"] for ch in chapters)
    else:
        words = words_between(transcript, start, end)
        if not words:
            sys.exit(f"в диапазоне {start}–{end} нет слов — проверьте транскрипт")
        segs = cut(words, a.max_pause, fps)
        edits = []
        for i, (s, e, ws) in enumerate(segs, 1):
            role = "hook" if i == 1 else ("payoff" if i == len(segs) else "development")
            edits.append({
                "id": f"seg{i:02d}", "source_start_sec": round(s, 3), "source_end_sec": round(e, 3),
                "role": role, "transcript_text": " ".join(w["word"].strip() for w in ws),
                "cut_reason": "auto_cut: границы по паузам > %.2f с" % a.max_pause,
            })
        before = end - start
    after = sum(e["source_end_sec"] - e["source_start_sec"] for e in edits)
    log(f"{len(edits)} сегментов, {before:.1f} с → {after:.1f} с (−{before - after:.1f} с пауз и междометий)",
        tag="auto_cut")
    if a.dry_run:
        for e in edits:
            print(f"{e['id']} {e['source_start_sec']:.2f}-{e['source_end_sec']:.2f}  {e['transcript_text'][:90]}")
        return
    plan["speech_edits"] = edits
    plan.setdefault("source", {}).setdefault("transcript_path", os.path.relpath(
        resolve_repo_path(tpath), ROOT).replace("\\", "/"))
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
