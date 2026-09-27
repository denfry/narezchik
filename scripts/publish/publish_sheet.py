"""Собрать лист публикации по готовым edit plans: что, когда и с какими метаданными грузить.

Читает edit-plans/<prefix>_shortNN.json и <prefix>_longN.json, берёт project.youtube_metadata,
QC из temp/<clip>_qc.json и temp/<clip>_people.json, и пишет:
  - edit-plans/<prefix>_ПУБЛИКАЦИЯ.md — человекочитаемый лист для ручной загрузки в Studio;
  - edit-plans/<prefix>_ЗАГРУЗКА.json — план для scripts/publish/youtube_upload.py.

Расписание: шортсы по одному в день в --shorts-time начиная с --start, лонги по
--long-weekday (0=пн … 5=сб) в --long-time, первый — в первую такую дату не раньше --start.
Время — в часовом поясе --utc-offset (по умолчанию +3, Москва).
Связанный лонг для шортса — поле project.related_long в плане шортса.

Usage: python publish_sheet.py podcast12 --start 2026-10-01 [--utc-offset 3]
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
from common import ROOT, log  # noqa: E402

MSK = dt.timezone(dt.timedelta(hours=3))  # переопределяется --utc-offset


def _load(path):
    return json.load(open(path, encoding="utf-8"))


def _duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def _mmss(sec):
    if sec is None:
        return "—"
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def _qc(clip):
    q = os.path.join(ROOT, "temp", f"{clip}_qc.json")
    p = os.path.join(ROOT, "temp", f"{clip}_people.json")
    out = {}
    if os.path.exists(q):
        d = _load(q)
        out["lufs"] = d.get("loudness_lufs")
        out["black"] = d.get("has_black_frames")
        out["drift"] = (d.get("av_drift") or {}).get("drift_sec")
    if os.path.exists(p):
        d = _load(p)
        out["no_face"] = d.get("no_face_share")
        out["bad_gaps"] = d.get("bad_gaps")
    return out


def _related_long(project):
    if project.get("related_long"):
        return project["related_long"]
    m = re.search(r"\((\w+_long\d+)\)", project.get("notes") or "")
    return m.group(1) if m else ""


def build(prefix, start, shorts_time, long_weekday, long_time):
    shorts = sorted(glob.glob(os.path.join(ROOT, "edit-plans", f"{prefix}_short[0-9][0-9].json")))
    longs = sorted(glob.glob(os.path.join(ROOT, "edit-plans", f"{prefix}_long[0-9].json")))
    items = []

    day = start
    for i, p in enumerate(shorts):
        d = _load(p)
        clip = d["project"]["clip_id"]
        ym = d["project"].get("youtube_metadata", {})
        when = dt.datetime.combine(day + dt.timedelta(days=i), shorts_time, MSK)
        items.append({"kind": "short", "n": i + 1, "clip": clip, "plan": os.path.relpath(p, ROOT),
                      "file": f"output/{clip}.mp4", "title": ym.get("title", ""),
                      "description": ym.get("description", ""), "tags": ym.get("tags", []),
                      "hashtags": ym.get("hashtags", []), "publish_at": when,
                      "related": _related_long(d["project"]),
                      "hook": (d.get("speech_edits") or [{}])[0].get("transcript_text", "")[:90],
                      "duration": _duration(os.path.join(ROOT, "output", f"{clip}.mp4")),
                      "qc": _qc(clip)})

    first_long = start
    while first_long.weekday() != long_weekday:
        first_long += dt.timedelta(days=1)
    for i, p in enumerate(longs):
        d = _load(p)
        clip = d["project"]["clip_id"]
        ym = d["project"].get("youtube_metadata", {})
        when = dt.datetime.combine(first_long + dt.timedelta(weeks=i), long_time, MSK)
        items.append({"kind": "long", "n": i + 1, "clip": clip, "plan": os.path.relpath(p, ROOT),
                      "file": f"output/{clip}.mp4", "title": ym.get("title", ""),
                      "alt_titles": ym.get("alternate_titles", []),
                      "description": ym.get("description", ""), "tags": ym.get("tags", []),
                      "hashtags": ym.get("hashtags", []), "cover_prompt": ym.get("cover_prompt", ""),
                      "publish_at": when,
                      "duration": _duration(os.path.join(ROOT, "output", f"{clip}.mp4")),
                      "qc": _qc(clip)})
    return items


def write_md(items, path, prefix):
    L = [f"# Лист публикации — {prefix}", "",
         f"Время — UTC{MSK.utcoffset(None).total_seconds() / 3600:+.0f}. Шортсы: один в день. Лонги: раз в неделю. Порядок шортсов = сила хука по инвентарю.",
         "У шортса в Studio: выбрать первый кадр как превью, поставить «Связанное видео» на указанный лонг,",
         "теги вставить строкой, хэштеги — в конец описания.", "",
         "## Календарь", "", "| Дата | Время | Тип | Файл | Заголовок | Связанное видео |", "|---|---|---|---|---|---|"]
    for it in sorted(items, key=lambda x: x["publish_at"]):
        L.append(f"| {it['publish_at']:%d.%m (%a)} | {it['publish_at']:%H:%M} | {it['kind']} | `{it['file']}` | {it['title']} | {it.get('related', '')} |")
    L += ["", "## QC-сводка", "", "| Файл | Длит. | LUFS | Чёрные кадры | Дрейф A/V | Кадров без лиц | Незаконные отрезки без людей |", "|---|---|---|---|---|---|---|"]
    for it in items:
        q = it["qc"]
        L.append(f"| `{it['file']}` | {_mmss(it['duration'])} | {q.get('lufs', '—')} | {'да' if q.get('black') else 'нет'} | {q.get('drift', '—')} | "
                 f"{'' if q.get('no_face') is None else f'{q['no_face']:.0%}'} | {q.get('bad_gaps', '—')} |")
    for kind, title in (("short", "Шортсы"), ("long", "Лонги")):
        L += ["", f"## {title}", ""]
        for it in [x for x in items if x["kind"] == kind]:
            L += [f"### {it['kind']} {it['n']:02d} — {it['publish_at']:%d.%m %H:%M} — `{it['file']}`", "",
                  f"**Заголовок:** {it['title']}", ""]
            if it.get("alt_titles"):
                L += ["**Альтернативы:** " + " / ".join(it["alt_titles"]), ""]
            if it.get("hook"):
                L += [f"**Хук:** {it['hook']}", ""]
            if it.get("related"):
                L += [f"**Связанное видео:** {it['related']}", ""]
            L += ["**Описание:**", "", "```", it["description"].strip(), "```", "",
                  "**Теги:**", "", "```", ", ".join(it["tags"]), "```", ""]
            if it.get("hashtags"):
                L += ["**Хэштеги:** " + " ".join(it["hashtags"]), ""]
            if it.get("cover_prompt"):
                L += ["**Промпт обложки:**", "", "```", it["cover_prompt"].strip(), "```", ""]
    open(path, "w", encoding="utf-8").write("\n".join(L))


def write_json(items, path):
    data = {"items": [{"file": it["file"], "title": it["title"], "description": it["description"],
                       "tags": it["tags"], "publish_at": it["publish_at"].isoformat()} for it in items]}
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prefix")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD первого шортса")
    ap.add_argument("--shorts-time", default="12:00")
    ap.add_argument("--long-weekday", type=int, default=5)
    ap.add_argument("--long-time", default="18:00")
    ap.add_argument("--utc-offset", type=float, default=3.0, help="часовой пояс расписания")
    a = ap.parse_args()
    global MSK
    MSK = dt.timezone(dt.timedelta(hours=a.utc_offset))
    start = dt.date.fromisoformat(a.start)
    st = dt.time.fromisoformat(a.shorts_time)
    lt = dt.time.fromisoformat(a.long_time)
    items = build(a.prefix, start, st, a.long_weekday, lt)
    md = os.path.join(ROOT, "edit-plans", f"{a.prefix}_ПУБЛИКАЦИЯ.md")
    js = os.path.join(ROOT, "edit-plans", f"{a.prefix}_ЗАГРУЗКА.json")
    write_md(items, md, a.prefix)
    write_json(items, js)
    log(f"{len(items)} роликов -> {md}")


if __name__ == "__main__":
    main()
