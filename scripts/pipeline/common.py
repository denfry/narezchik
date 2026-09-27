"""Общие хелперы для scripts/pipeline/*.py: ffprobe, ffmpeg-запуск, пути, логирование, тайм-коды.

Ничего в этом файле не принимает редакторских решений — только техническая обвязка,
которую используют все deterministic-скрипты пайплайна shorts.
"""
import io
import json
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INPUT_DIR = os.path.join(ROOT, "input")
TRANSCRIPTS_DIR = os.path.join(ROOT, "transcripts")
EDIT_PLANS_DIR = os.path.join(ROOT, "edit-plans")
ASSETS_DIR = os.path.join(ROOT, "assets")
TEMP_DIR = os.path.join(ROOT, "temp")
OUTPUT_DIR = os.path.join(ROOT, "output")
SCHEMA_PATH = os.path.join(ROOT, "schemas", "edit-plan.schema.json")
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
CTA_DIR = os.path.join(ROOT, "cta")
CHANNEL_DIR = os.path.join(ROOT, "channel")
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")

# консоль Windows в cp1251 падает на «→», «−» и кириллице в логах
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

for _d in (INPUT_DIR, TRANSCRIPTS_DIR, EDIT_PLANS_DIR, TEMP_DIR, OUTPUT_DIR):
    os.makedirs(_d, exist_ok=True)


def log(msg, tag="shorts"):
    line = f"[{time.strftime('%H:%M:%S')}][{tag}] {msg}"
    print(line, flush=True)


def die(msg, code=1):
    log(f"ERROR: {msg}")
    sys.exit(code)


def run(cmd, timeout=None, check=True):
    """Run a subprocess command, return CompletedProcess. Raises RuntimeError with the
    tail of stderr on failure (unless check=False)."""
    # SHORTS_LOW_PRIORITY=1 — ffmpeg/whisper запускаются с пониженным приоритетом,
    # чтобы многочасовой рендер не подвешивал остальную работу на машине
    flags = 0
    low = os.environ.get("SHORTS_LOW_PRIORITY")
    if os.name == "nt" and low:
        # idle — когда за машиной играют: Windows отдаёт CPU и диск рендеру только
        # в простое, игра фризов не видит; рендер при этом идёт в 3-4 раза дольше
        flags = subprocess.IDLE_PRIORITY_CLASS if low.lower() == "idle"             else subprocess.BELOW_NORMAL_PRIORITY_CLASS
    result = subprocess.run(cmd, capture_output=True, timeout=timeout,
                            creationflags=flags)
    if check and result.returncode != 0:
        stderr_tail = result.stderr.decode(errors="replace")[-2500:]
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(str(c) for c in cmd)}\n{stderr_tail}"
        )
    return result


def ffprobe_json(path):
    """Full ffprobe -show_format -show_streams as a dict."""
    result = run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ])
    return json.loads(result.stdout.decode("utf-8", errors="replace"))


def load_json(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def fmt_ts_ass(t):
    """Seconds -> ASS timestamp H:MM:SS.CC"""
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def fmt_ts_human(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def resolve_repo_path(p):
    """Accept a repo-relative path, an absolute path, or an HTTP(S) source URL."""
    if p.startswith(("http://", "https://")):
        return p
    if os.path.isabs(p):
        return p
    return os.path.join(ROOT, p)
def transcript_for_plan(plan):
    """Путь к транскрипту плана: source.transcript_path, иначе transcripts/<clip_id>.json,
    иначе транскрипт исходника — clip_id без суффикса _shortNN/_longNN (один исходник
    даёт много планов, транскрипт у них общий)."""
    import re
    tp = (plan.get("source") or {}).get("transcript_path")
    if tp:
        return resolve_repo_path(tp)
    cid = plan["project"]["clip_id"]
    base = re.sub(r"_(short|long)\d+$", "", cid)
    for c in (cid, base):
        p = os.path.join(TRANSCRIPTS_DIR, f"{c}.json")
        if os.path.isfile(p):
            return p
    return os.path.join(TRANSCRIPTS_DIR, f"{base}.json")


def to_repo_relative(p):
    if p.startswith(("http://", "https://")):
        return p
    p = os.path.abspath(p)
    try:
        rel = os.path.relpath(p, ROOT)
    except ValueError:
        return p
    return rel.replace("\\", "/")
