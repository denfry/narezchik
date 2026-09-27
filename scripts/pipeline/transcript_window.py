#!/usr/bin/env python
"""Кусок транскрипта в компактном виде — чтобы агент не читал двухчасовой JSON целиком.

Полный transcripts/<clip_id>.json с пословными таймкодами двухчасового подкаста — это
сотни тысяч токенов. Агентам после скаута нужен только их диапазон: этот скрипт печатает
его построчно (`[start-end] текст`) или пословно (`слово@секунда`) для точных резов.

Usage:
    python transcript_window.py podcast --from 1520 --to 1580
    python transcript_window.py podcast --from 1520 --to 1580 --words
    python transcript_window.py edit-plans/podcast_short03.json          # диапазон clip.* из плана
    python transcript_window.py podcast --outline 60                     # оглавление: по строке на минуту
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, TRANSCRIPTS_DIR, transcript_for_plan  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def load(ref):
    """clip_id или путь к плану → (транскрипт, start, end)."""
    start = end = None
    if ref.endswith(".json") and os.path.isfile(os.path.join(ROOT, ref) if not os.path.isabs(ref) else ref):
        path = ref if os.path.isabs(ref) else os.path.join(ROOT, ref)
        with io.open(path, encoding="utf-8") as f:
            plan = json.load(f)
        if "segments" in plan:
            return plan, None, None
        clip = plan.get("clip", {})
        start, end = clip.get("source_start_sec"), clip.get("source_end_sec")
        path = transcript_for_plan(plan)
    else:
        path = os.path.join(TRANSCRIPTS_DIR, f"{ref}.json")
    with io.open(path, encoding="utf-8") as f:
        return json.load(f), start, end


def fmt(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ref", help="clip_id транскрипта или путь к edit plan")
    ap.add_argument("--from", dest="start", type=float)
    ap.add_argument("--to", dest="end", type=float)
    ap.add_argument("--pad", type=float, default=5.0, help="запас вокруг диапазона плана, с")
    ap.add_argument("--words", action="store_true", help="пословно: слово@секунда")
    ap.add_argument("--outline", type=float, help="оглавление: первая фраза каждые N секунд")
    a = ap.parse_args()

    t, ps, pe = load(a.ref)
    start = a.start if a.start is not None else (ps - a.pad if ps is not None else 0.0)
    end = a.end if a.end is not None else (pe + a.pad if pe is not None else 1e9)

    if a.outline:
        nxt = 0.0
        for seg in t["segments"]:
            if seg["start"] >= nxt:
                print(f"[{fmt(seg['start'])}] {seg['text'].strip()[:110]}")
                nxt = seg["start"] + a.outline
        return

    for seg in t["segments"]:
        if seg["end"] < start or seg["start"] > end:
            continue
        if a.words:
            ws = [w for w in seg.get("words", []) if start <= w["start"] <= end]
            print(" ".join(f"{w['word'].strip()}@{w['start']:.2f}" for w in ws)
                  + (f"  ⟂{ws[-1]['end']:.2f}" if ws else ""))
        else:
            print(f"[{seg['start']:.2f}-{seg['end']:.2f}] {seg['text'].strip()}")


if __name__ == "__main__":
    main()
