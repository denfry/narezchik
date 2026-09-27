#!/usr/bin/env python
"""Очередь недели: скачать → расшифровать → отрендерить, по одной задаче, в щадящем режиме.

Зачем: на домашнем ПК (особенно с HDD) параллельные закачка, извлечение звука и 4K-рендер
кладут диск, и машиной невозможно пользоваться. Этот скрипт гоняет задачи строго
последовательно, каждую — с приоритетом Idle (Windows отдаёт ей CPU и диск только в
простое) и ограничением потоков, поэтому его можно запускать и во время игры, и на ночь.

Две фазы, потому что между ними нужен редактор (Claude Code): планы монтажа не собираются
скриптом.

    python scripts/pipeline/run_queue.py prepare   # закачки (Яндекс.Диск) + транскрипция
    python scripts/pipeline/run_queue.py render    # рендер всех планов со stage audio_planned
    python scripts/pipeline/run_queue.py status    # что уже сделано

Список источников — channel/queue.json (образец — channel/queue.example.json):

    {"yadisk_public_key": "https://disk.yandex.ru/d/XXXX",   # необязательно
     "format": "short",                                      # short | long — заготовка плана
     "items": {"podcast12": "/Подкасты/Выпуск 12.mp4",       # путь в публичной папке Диска
               "stream03": "input/stream03.mp4"}}            # или файл, уже лежащий локально

Готовые шаги пропускаются, так что скрипт можно перезапускать сколько угодно.
Лог — temp/queue_<фаза>.log.
"""
import argparse
import datetime
import glob
import io
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CHANNEL_DIR, ROOT, TEMP_DIR  # noqa: E402

API = "https://cloud-api.yandex.net/v1/disk/public/resources"
QUEUE_PATH = os.path.join(CHANNEL_DIR, "queue.json")
INPUT_DIR = os.path.join(ROOT, "input")
TRANSCRIPTS_DIR = os.path.join(ROOT, "transcripts")
PLANS_DIR = os.path.join(ROOT, "edit-plans")
OUTPUT_DIR = os.path.join(ROOT, "output")

def _load_queue():
    if not os.path.isfile(QUEUE_PATH):
        return {}, {}
    with io.open(QUEUE_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg, cfg.get("items", {})


CONFIG, QUEUE = _load_queue()
PUBLIC_KEY = os.environ.get("YADISK_PUBLIC_KEY") or CONFIG.get("yadisk_public_key", "")


def is_disk_path(ref):
    """«/Папка/файл.mp4» — путь в публичной папке Диска; остальное — локальный файл."""
    return bool(PUBLIC_KEY) and ref.startswith("/") and not os.path.isfile(ref)

IDLE_ENV = {"SHORTS_LOW_PRIORITY": "idle", "SHORTS_THREADS": "4",
            "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def log(msg, phase):
    line = f"[{datetime.datetime.now():%H:%M:%S}][queue] {msg}"
    print(line, flush=True)
    with io.open(os.path.join(TEMP_DIR, f"queue_{phase}.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def idle_flags():
    return subprocess.IDLE_PRIORITY_CLASS if os.name == "nt" else 0


def run(cmd, phase, env_extra=None):
    env = dict(os.environ, **IDLE_ENV, **(env_extra or {}))
    log("$ " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd), phase)
    rc = subprocess.call(cmd, env=env, creationflags=idle_flags())
    if rc != 0:
        log(f"ОШИБКА rc={rc}: {cmd[0]} — шаг пропущен, очередь продолжается", phase)
    return rc == 0


def disk_meta(path):
    import requests
    r = requests.get(API, params={"public_key": PUBLIC_KEY, "path": path}, timeout=60)
    r.raise_for_status()
    return r.json()


def disk_href(path):
    import requests
    r = requests.get(API + "/download", params={"public_key": PUBLIC_KEY, "path": path},
                     timeout=60)
    r.raise_for_status()
    return r.json()["href"]


def local_source(clip_id):
    ref = QUEUE[clip_id]
    if is_disk_path(ref):
        return os.path.join(INPUT_DIR, os.path.basename(ref))
    return ref if os.path.isabs(ref) else os.path.join(ROOT, ref)


def download(clip_id, phase):
    dst = local_source(clip_id)
    if not is_disk_path(QUEUE[clip_id]):
        ok = os.path.isfile(dst)
        log(f"{clip_id}: локальный исходник {'на месте' if ok else 'НЕ НАЙДЕН: ' + dst}", phase)
        return ok
    meta = disk_meta(QUEUE[clip_id])
    if os.path.isfile(dst) and os.path.getsize(dst) == meta["size"]:
        log(f"{clip_id}: исходник уже скачан ({meta['size'] / 1e9:.1f} ГБ)", phase)
        return True
    log(f"{clip_id}: качаю {meta['size'] / 1e9:.1f} ГБ → {dst}", phase)
    # curl с докачкой: обрыв сети не заставляет качать 4 ГБ заново.
    # --limit-rate держит HDD свободным для остальной работы (12 МБ/с ≈ 4 ГБ за 6 минут)
    return run(["curl", "-L", "-C", "-", "-sS", "--retry", "10", "--retry-all-errors",
                "--limit-rate", "12M", "-o", dst, disk_href(QUEUE[clip_id])], phase)


def transcribe(clip_id, phase):
    if os.path.isfile(os.path.join(TRANSCRIPTS_DIR, f"{clip_id}.json")):
        log(f"{clip_id}: транскрипт уже есть", phase)
        return True
    src = local_source(clip_id)
    if not os.path.isfile(src):
        log(f"{clip_id}: нет исходника, транскрипция пропущена", phase)
        return False
    return run([sys.executable, os.path.join(ROOT, "scripts", "pipeline", "transcribe.py"),
                src, "--clip-id", clip_id, "--model", "medium", "--device", "auto"], phase)


def probe_plan(clip_id, phase):
    """Заготовка edit plan с секцией source — редактору останется контент."""
    plan_path = os.path.join(PLANS_DIR, f"{clip_id}.json")
    if os.path.isfile(plan_path):
        return True
    src = local_source(clip_id)
    probe_json = os.path.join(TEMP_DIR, f"{clip_id}_probe.json")
    ok = run([sys.executable, os.path.join(ROOT, "scripts", "pipeline", "probe_video.py"),
              src, "--json-out", probe_json], phase)
    if not ok:
        return False
    source = json.load(io.open(probe_json, encoding="utf-8"))
    long_form = CONFIG.get("format", "short") == "long"
    tail = os.path.join("cta", "tail.png")
    tail_card = ({"enabled": True, "duration_sec": 3.5, "image_path": tail}
                 if os.path.isfile(os.path.join(ROOT, tail)) else {"enabled": False})
    plan = {
        "project": {"clip_id": clip_id, "stage": "moment_selected",
                    "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "notes": "Заготовка от run_queue.py: source заполнен, контент — за редактором."},
        "source": source,
        "clip": {"source_start_sec": 0.0, "source_end_sec": source["duration_sec"],
                 "hook": "TODO", "core_idea": "TODO", "payoff": "TODO", "reason_to_watch": "TODO",
                 "virality_score": 0, "retention_score": 0, "clarity_score": 0,
                 "context_dependency": 0, "suggested_title": "TODO", "alt_titles": []},
        "speech_edits": [], "visual_events": [], "subtitle_events": [], "audio_events": [],
        "assets": [],
        "render_settings": {
            "width": 1920 if long_form else 1080, "height": 1080 if long_form else 1920,
            "fps": source.get("fps", 30),
            "video_codec": "libx264", "audio_codec": "aac", "crf": 16, "preset": "medium",
            "audio_bitrate": "192k", "target_lufs": -14, "use_gpu_encode": False,
            "inherit_source_resolution": long_form, "min_output_height": 1440,
            "lut": None, "lut_strength": 0.55, "film_grain": 0.25, "color_grade": None,
            "cover_frame": {"enabled": False},
            "tail_card": tail_card,
            "subtitle_mode": "chunk" if long_form else "word"},
        "output_path": f"output/{clip_id}.mp4",
    }
    io.open(plan_path, "w", encoding="utf-8", newline="\n").write(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    log(f"{clip_id}: заготовка плана записана → {plan_path}", phase)
    return True


def render_ready(phase):
    """Рендерит все планы со stage audio_planned, у которых ещё нет выхода."""
    plans = sorted(glob.glob(os.path.join(PLANS_DIR, "*.json")))
    todo = []
    for p in plans:
        d = json.load(io.open(p, encoding="utf-8"))
        stage = d.get("project", {}).get("stage")
        out = os.path.join(ROOT, d.get("output_path", ""))
        if stage in ("audio_planned", "cta_planned") and not (out and os.path.isfile(out)):
            todo.append(p)
        elif stage in ("audio_planned", "cta_planned") and out and os.path.isfile(out):
            log(f"{os.path.basename(p)}: выход уже есть, пропускаю (перерендер — удалить файл)", phase)
    if not todo:
        log("рендерить нечего: нет планов со stage audio_planned без готового выхода", phase)
        return
    for p in todo:
        # подложка должна существовать, иначе validate упадёт — это сигнал редактору
        ok = run([sys.executable, os.path.join(ROOT, "scripts", "pipeline", "validate_edit_plan.py"), p], phase)
        if not ok:
            log(f"{os.path.basename(p)}: план не валиден — пропущен", phase)
            continue
        run([sys.executable, os.path.join(ROOT, "scripts", "pipeline", "render_short.py"), p], phase)


def status():
    for cid in QUEUE:
        src = os.path.isfile(local_source(cid))
        tr = os.path.isfile(os.path.join(TRANSCRIPTS_DIR, f"{cid}.json"))
        plan = os.path.join(PLANS_DIR, f"{cid}.json")
        stage = json.load(io.open(plan, encoding="utf-8"))["project"].get("stage") if os.path.isfile(plan) else "-"
        out = os.path.isfile(os.path.join(OUTPUT_DIR, f"{cid}.mp4"))
        print(f"{cid:22} исходник:{'да' if src else 'нет':4} транскрипт:{'да' if tr else 'нет':4} "
              f"план:{stage:16} выход:{'да' if out else 'нет'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["prepare", "render", "status"])
    ap.add_argument("--only", nargs="*", help="ограничить clip_id")
    args = ap.parse_args()
    if not QUEUE:
        sys.exit(f"очередь пуста: создайте {QUEUE_PATH} по образцу channel/queue.example.json")
    if args.phase == "status":
        return status()
    ids = args.only or list(QUEUE)
    if args.phase == "prepare":
        for cid in ids:
            download(cid, "prepare")
        for cid in ids:
            transcribe(cid, "prepare")
            probe_plan(cid, "prepare")
        log("prepare завершён — дальше редактор собирает планы, затем `run_queue.py render`", "prepare")
    else:
        render_ready("render")
        log("render завершён", "render")


if __name__ == "__main__":
    main()
