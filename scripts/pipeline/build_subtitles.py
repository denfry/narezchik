#!/usr/bin/env python
"""Собрать subtitle_events из speech_edits и word-level транскрипта.

Зачем отдельный скрипт: тайминги субтитров — чистая арифметика (взять слова, попадающие
в интервал сегмента, и пересчитать их в кумулятивное время готового ролика). Раньше эти
300+ событий на ролик писались руками, и любая ошибка в сумме длительностей давала
рассинхрон, который видно только на готовом рендере. Редакторское тут — только emphasis
и плашки (hook_headline/emphasis_callout): их ставит subtitle-director поверх результата.

Режимы:
    chunk — фразовые субтитры (лонги, горизонталь): чанк рвётся по паузе, длине строки
            или максимальной длительности, границы берутся по словам.
    word  — пословные (Shorts, вертикаль): одно событие на speech_edit, внутри words[].

Сегменты с source_path (врезки из другого файла — зазывалка, DJI) пропускаются: там своя
речь, которую этот транскрипт не описывает. Их субтитры, если нужны, ставит редактор.

    python build_subtitles.py edit-plans/foo.json --transcript transcripts/foo.json
    python build_subtitles.py edit-plans/foo.json --mode word --dry-run
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, log, transcript_for_plan  # noqa: E402

# Границы чанка. Подобраны под кегль 96 и ширину 16:9: 42 символа — это одна строка,
# которую успевает прочитать зритель, не отрываясь от лица говорящего.
MAX_CHARS = 42
MAX_DUR = 3.2
# Пауза, по которой рвём фразу: меньше — рвётся посреди мысли, больше — чанк уезжает
# от речи. 0.45 с — типичная граница между словами внутри фразы и между фразами.
PAUSE_GAP = 0.45


def load_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def words_in_range(transcript, start, end):
    """Слова, чья середина попадает в [start, end) — так слово на границе не задваивается."""
    out = []
    for seg in transcript.get("segments", []):
        if seg.get("end", 0) < start or seg.get("start", 0) > end:
            continue
        for w in seg.get("words", []):
            mid = (w["start"] + w["end"]) / 2.0
            if start <= mid < end:
                out.append(w)
    return sorted(out, key=lambda w: w["start"])


def chunk_words(words, max_chars, max_dur, pause_gap):
    """Разбить слова на читаемые чанки по паузе / длине / длительности."""
    chunks, cur = [], []
    for w in words:
        if cur:
            gap = w["start"] - cur[-1]["end"]
            text_len = len(" ".join(x["word"].strip() for x in cur + [w]))
            dur = w["end"] - cur[0]["start"]
            if gap >= pause_gap or text_len > max_chars or dur > max_dur:
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build(plan, transcript, mode, max_chars, max_dur, pause_gap):
    events, offset, skipped = [], 0.0, []
    for seg in plan.get("speech_edits", []):
        dur = seg["source_end_sec"] - seg["source_start_sec"]
        if seg.get("source_path"):
            # врезка из другого файла — своя речь, этого транскрипта там нет
            skipped.append(seg.get("id", "?"))
            offset += dur
            continue
        words = words_in_range(transcript, seg["source_start_sec"], seg["source_end_sec"])
        base = offset - seg["source_start_sec"]  # source time -> output time

        def at(t):
            """Время слова в таймлайне ролика, прижатое к границам сегмента.

            Слово попадает в сегмент по своей СЕРЕДИНЕ, поэтому на краях оно может
            начинаться раньше начала сегмента или кончаться позже его конца. Без
            прижатия первое слово получало отрицательное время, и план не проходил
            валидацию (start_sec >= 0), а на рендере субтитр уезжал за начало.
            """
            return round(min(max(t + base, offset), offset + dur), 3)
        if mode == "word":
            if words:
                events.append({
                    "start_sec": at(words[0]["start"]),
                    "end_sec": at(words[-1]["end"]),
                    "text": " ".join(w["word"].strip() for w in words),
                    "style": "caption",
                    "segment_ref": seg.get("id"),
                    "words": [{"word": w["word"].strip(),
                               "start_sec": at(w["start"]),
                               "end_sec": at(w["end"]),
                               "emphasis": False} for w in words],
                })
        else:
            for ch in chunk_words(words, max_chars, max_dur, pause_gap):
                events.append({
                    "start_sec": at(ch[0]["start"]),
                    "end_sec": at(ch[-1]["end"]),
                    "text": " ".join(w["word"].strip() for w in ch),
                    "style": "caption",
                    "segment_ref": seg.get("id"),
                })
        offset += dur
    return events, offset, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--transcript", help="по умолчанию source.transcript_path или transcripts/<исходник>.json")
    ap.add_argument("--mode", choices=["chunk", "word"],
                    help="по умолчанию из render_settings.subtitle_mode, иначе из формата")
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS)
    ap.add_argument("--max-dur", type=float, default=MAX_DUR)
    ap.add_argument("--pause-gap", type=float, default=PAUSE_GAP)
    ap.add_argument("--dry-run", action="store_true", help="показать, но не писать в план")
    args = ap.parse_args()

    plan = load_json(args.plan)
    clip_id = plan["project"]["clip_id"]
    tr_path = args.transcript or transcript_for_plan(plan)
    if not os.path.isfile(tr_path):
        sys.exit(f"нет транскрипта: {tr_path} (укажи --transcript)")
    transcript = load_json(tr_path)

    rs = plan.get("render_settings", {})
    mode = args.mode or rs.get("subtitle_mode") or (
        "word" if rs.get("height", 0) > rs.get("width", 1) else "chunk")

    events, total, skipped = build(plan, transcript, mode, args.max_chars,
                                   args.max_dur, args.pause_gap)
    log(f"{clip_id}: режим {mode}, событий {len(events)}, речь {total:.2f}с")
    if skipped:
        log(f"пропущены врезки со своим звуком (субтитры к ним — вручную): {', '.join(skipped)}")
    if not events:
        sys.exit("ни одного события — проверь, что speech_edits попадают в транскрипт")
    gaps = [e for e in events if e["end_sec"] <= e["start_sec"]]
    if gaps:
        sys.exit(f"кривые интервалы: {len(gaps)} шт — транскрипт не совпадает со speech_edits")
    log(f"первый {events[0]['start_sec']:.2f}-{events[0]['end_sec']:.2f} «{events[0]['text'][:40]}»")
    log(f"последний {events[-1]['start_sec']:.2f}-{events[-1]['end_sec']:.2f} «{events[-1]['text'][:40]}»")

    if args.dry_run:
        log("dry-run: план не изменён")
        return
    plan["subtitle_events"] = events
    with io.open(args.plan, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    log(f"записано в {args.plan}")


if __name__ == "__main__":
    main()
