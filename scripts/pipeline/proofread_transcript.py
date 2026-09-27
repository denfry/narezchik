#!/usr/bin/env python
"""Вычитка транскрипта без потери word-level таймингов.

ASR (faster-whisper) ошибается в словах, а субтитры берутся прямо из
transcripts/<clip_id>.json — значит, опечатка ASR становится опечаткой на экране.
Скрипт даёт два прохода:

    dump   — выгрузить сегменты с нумерованными словами в temp/<clip_id>_words.txt,
             чтобы редактор (человек или LLM) прочитал и выписал правки.
    apply  — применить правки из JSON-файла к транскрипту, сохранив тайминги.
             Оригинал сохраняется рядом как <clip_id>.raw.json (один раз).

Формат файла правок (JSON):
    {
      "words":    [[seg_idx, word_idx, "новое слово"], ...],   # замена одного слова
      "segments": [[seg_idx, "полный новый текст сегмента"], ...]  # замена текста сегмента
    }

Для "segments" число слов (по пробелам) обязано совпасть с числом words[] в сегменте —
тайминги раздаются 1:1. Если нужно склеить два ASR-слова в одно или разбить одно на два,
используйте синтаксис: слово с подчёркиванием "по_моему" займёт один слот, а "~" в слоте
удалит слово (его время уйдёт соседу слева). Пунктуация и регистр — часть слова.

Usage:
    python proofread_transcript.py dump transcripts/podcast.json
    python proofread_transcript.py apply transcripts/podcast.json temp/podcast_fixes.json
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import TEMP_DIR, log  # noqa: E402


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(transcript_path, out_path=None):
    t = load(transcript_path)
    clip_id = t.get("clip_id") or os.path.splitext(os.path.basename(transcript_path))[0]
    out_path = out_path or os.path.join(TEMP_DIR, f"{clip_id}_words.txt")
    lines = []
    for si, seg in enumerate(t["segments"]):
        lines.append(f"### seg {si}  [{seg['start']:.2f}-{seg['end']:.2f}]")
        lines.append("   " + " ".join(f"{wi}:{w['word'].strip()}" for wi, w in enumerate(seg["words"])))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log(f"dumped {len(t['segments'])} segments -> {out_path}")
    return out_path


def _apply_segment(seg, new_text):
    tokens = new_text.split()
    words = seg["words"]
    if len(tokens) != len(words):
        raise SystemExit(
            f"seg text has {len(tokens)} tokens but segment has {len(words)} words:\n"
            f"  old: {' '.join(w['word'].strip() for w in words)}\n  new: {new_text}"
        )
    kept = []
    for tok, w in zip(tokens, words):
        if tok == "~":
            if kept:
                kept[-1]["end"] = w["end"]
            continue
        w = dict(w)
        w["word"] = tok.replace("_", " ")
        kept.append(w)
    seg["words"] = kept
    seg["text"] = " ".join(w["word"] for w in kept)


def apply(transcript_path, fixes_path):
    raw_path = transcript_path.replace(".json", ".raw.json")
    if not os.path.exists(raw_path):
        shutil.copyfile(transcript_path, raw_path)
        log(f"raw ASR kept as {raw_path}")
    t = load(transcript_path)
    fixes = load(fixes_path)
    n = 0
    for si, wi, new in fixes.get("words", []):
        w = t["segments"][si]["words"][wi]
        w["word"] = new
        t["segments"][si]["text"] = " ".join(x["word"].strip() for x in t["segments"][si]["words"])
        n += 1
    for si, new_text in fixes.get("segments", []):
        _apply_segment(t["segments"][si], new_text)
        n += 1
    t["text"] = " ".join(seg["text"] for seg in t["segments"])
    t["proofread"] = True
    with open(transcript_path, "w", encoding="utf-8") as f:
        json.dump(t, f, ensure_ascii=False, indent=1)
    txt_path = transcript_path.replace(".json", ".txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        for seg in t["segments"]:
            f.write(f"[{seg['start']:.2f}-{seg['end']:.2f}] {seg['text']}\n")
    log(f"applied {n} fixes -> {transcript_path} (+ .txt)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("transcript")
    d.add_argument("--out")
    a = sub.add_parser("apply")
    a.add_argument("transcript")
    a.add_argument("fixes")
    args = ap.parse_args()
    if args.cmd == "dump":
        dump(args.transcript, args.out)
    else:
        apply(args.transcript, args.fixes)


if __name__ == "__main__":
    main()
