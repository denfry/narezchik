#!/usr/bin/env python
"""Установщик нарезчика: проверяет окружение и ставит то, чего не хватает.

    python install.py              # интерактивно: таблица состояния и меню
    python install.py --check      # только проверить, ничего не ставить
    python install.py --auto       # поставить всё рекомендуемое без вопросов
    python install.py --all        # поставить вообще всё, включая необязательное
    python install.py --only whisper,face   # только выбранные компоненты

Компоненты — COMPONENTS ниже. Python-пакеты ставятся в тот интерпретатор, которым
запущен установщик (для отдельного окружения — `python install.py --venv`).
ffmpeg ставится системным менеджером пакетов: winget (Windows), brew (macOS),
apt/dnf/pacman (Linux) — только после подтверждения.
"""
import argparse
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.join(ROOT, "scripts", "pipeline")
FACE_MODEL = os.path.join(PIPELINE, "models", "face_landmarker.task")
FACE_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
                  "face_landmarker/float16/1/face_landmarker.task")

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
if os.name == "nt":
    os.system("")  # включает ANSI-цвета в консоли Windows 10+

G, R, Y, B, DIM, X = "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[2m", "\033[0m"


def has_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def has_nvidia():
    if not shutil.which("nvidia-smi"):
        return False
    try:
        return subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=15).returncode == 0
    except Exception:  # noqa: BLE001
        return False


# key, название, pip-пакеты, модули для проверки, уровень: required | recommended | optional
COMPONENTS = [
    ("core", "Ядро: рендер, субтитры, графика, валидация планов",
     ["jsonschema>=4.20", "Pillow>=10", "numpy>=1.26", "requests>=2.31", "segno>=1.5"],
     ["jsonschema", "PIL", "numpy", "requests", "segno"], "required"),
    ("whisper", "Транскрипция речи (faster-whisper, модели качаются при первом запуске)",
     ["faster-whisper>=1.0.3"], ["faster_whisper"], "recommended"),
    ("face", "Детект лиц: автокроп под 9:16, обложки, проверка людей в кадре (mediapipe + модель 3.6 МБ)",
     ["mediapipe>=0.10.14"], ["mediapipe", "cv2"], "recommended"),
    ("gpu", "Ускорение транскрипции на NVIDIA: cuBLAS/cuDNN (~1.3 ГБ), CUDA Toolkit не нужен",
     ["nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-nvrtc-cu12"], ["nvidia.cublas"], "gpu"),
    ("channel", "Анализ своего канала для /setup-channel (yt-dlp — субтитры лучших роликов)",
     ["yt-dlp"], ["yt_dlp"], "recommended"),
    ("publish", "Загрузка роликов на YouTube по расписанию (OAuth Google)",
     ["google-api-python-client>=2.100", "google-auth-oauthlib>=1.2", "google-auth-httplib2"],
     ["googleapiclient", "google_auth_oauthlib"], "optional"),
    ("remote", "Рендер и транскрипция на своём сервере по SSH (paramiko)",
     ["paramiko>=3.4"], ["paramiko"], "optional"),
    ("dev", "Тесты пайплайна (pytest)", ["pytest>=8"], ["pytest"], "optional"),
]
LEVEL_LABEL = {"required": "обязательно", "recommended": "рекомендуется", "gpu": "если есть NVIDIA",
               "optional": "по желанию"}


def component_status(comp):
    key, _, _, modules, _ = comp
    have = sum(has_module(m) for m in modules)
    if key == "face" and have == len(modules) and not os.path.isfile(FACE_MODEL):
        return "partial"
    return "ok" if have == len(modules) else ("partial" if have else "missing")


def assets_status():
    luts = os.path.isdir(os.path.join(ROOT, "assets", "luts")) and any(
        f.endswith(".cube") for f in os.listdir(os.path.join(ROOT, "assets", "luts")))
    sfx = os.path.isdir(os.path.join(ROOT, "assets", "sfx")) and any(
        f.endswith(".wav") for f in os.listdir(os.path.join(ROOT, "assets", "sfx")))
    brand = os.path.isfile(os.path.join(ROOT, "channel", "brand.json"))
    return {"luts": luts, "sfx": sfx, "brand": brand}


def mark(ok):
    return f"{G}✓{X}" if ok is True or ok == "ok" else (f"{Y}~{X}" if ok == "partial" else f"{R}✗{X}")


def report(nvidia):
    print(f"\n{B}Окружение{X}")
    py_ok = sys.version_info >= (3, 10)
    print(f"  {mark(py_ok)} Python {platform.python_version()} ({sys.executable})"
          + ("" if py_ok else f"  {R}нужен 3.10+ (рекомендуется 3.11–3.12){X}"))
    ff = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    print(f"  {mark(ff)} ffmpeg / ffprobe" + ("" if ff else f"  {R}обязательно{X}"))
    print(f"  {mark(nvidia) if nvidia else f'{DIM}-{X}'} NVIDIA GPU"
          + ("" if nvidia else f"  {DIM}нет — транскрипция пойдёт на CPU, это нормально{X}"))
    print(f"  {mark(bool(shutil.which('claude')))} Claude Code CLI"
          + ("" if shutil.which("claude") else f"  {DIM}нужен для агентов: https://claude.com/claude-code{X}"))
    print(f"\n{B}Компоненты{X}")
    for i, comp in enumerate(COMPONENTS, 1):
        st = component_status(comp)
        lvl = comp[4]
        note = LEVEL_LABEL[lvl]
        if lvl == "gpu" and not nvidia:
            note = "не нужно — NVIDIA не найдена"
        print(f"  {i}. {mark(st)} {comp[0]:8} {comp[1]}  {DIM}[{note}]{X}")
    a = assets_status()
    print(f"\n{B}Ассеты и настройки{X}")
    print(f"  {mark(a['luts'])} LUT для цветокора (assets/luts)")
    print(f"  {mark(a['sfx'])} синтезированные SFX и подложки (assets/sfx, assets/music)")
    print(f"  {mark(a['brand'])} channel/brand.json" + ("" if a["brand"] else
          f"  {DIM}создастся из шаблона; заполнит /setup-channel{X}"))
    return ff


def pip_install(packages):
    # без --upgrade: уже стоящая подходящая версия не трогается, чтобы не ломать чужие проекты
    cmd = [sys.executable, "-m", "pip", "install"] + packages
    print(f"{DIM}$ {' '.join(cmd)}{X}")
    return subprocess.call(cmd) == 0


def download_face_model():
    if os.path.isfile(FACE_MODEL):
        return True
    os.makedirs(os.path.dirname(FACE_MODEL), exist_ok=True)
    print(f"качаю модель лиц → {os.path.relpath(FACE_MODEL, ROOT)}")
    try:
        urllib.request.urlretrieve(FACE_MODEL_URL, FACE_MODEL + ".part")
        os.replace(FACE_MODEL + ".part", FACE_MODEL)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"{R}не скачалась: {e}{X}\nвручную: curl -L -o \"{FACE_MODEL}\" {FACE_MODEL_URL}")
        return False


def ffmpeg_install_cmd():
    system = platform.system()
    if system == "Windows" and shutil.which("winget"):
        return ["winget", "install", "-e", "--id", "Gyan.FFmpeg",
                "--accept-source-agreements", "--accept-package-agreements"]
    if system == "Darwin" and shutil.which("brew"):
        return ["brew", "install", "ffmpeg"]
    if system == "Linux":
        for pm, cmd in (("apt-get", ["sudo", "apt-get", "install", "-y", "ffmpeg"]),
                        ("dnf", ["sudo", "dnf", "install", "-y", "ffmpeg"]),
                        ("pacman", ["sudo", "pacman", "-S", "--noconfirm", "ffmpeg"])):
            if shutil.which(pm):
                return cmd
    return None


def install_ffmpeg(ask):
    cmd = ffmpeg_install_cmd()
    if not cmd:
        print(f"{Y}Поставьте ffmpeg вручную (нужна сборка с libx264 и libass):{X}\n"
              "  Windows: https://www.gyan.dev/ffmpeg/builds/ (full build), распаковать и добавить bin в PATH\n"
              "  macOS:   brew install ffmpeg\n  Linux:   пакет ffmpeg из менеджера пакетов")
        return False
    if not ask(f"Установить ffmpeg командой `{' '.join(cmd)}`?"):
        return False
    ok = subprocess.call(cmd) == 0
    if ok and platform.system() == "Windows":
        print(f"{Y}ffmpeg установлен. Перезапустите консоль (и Claude Code), чтобы обновился PATH.{X}")
    return ok


def setup_assets():
    py = sys.executable
    os.makedirs(os.path.join(ROOT, "channel"), exist_ok=True)
    for name in ("brand", "settings", "comment-rules"):
        dst = os.path.join(ROOT, "channel", f"{name}.json")
        example = os.path.join(ROOT, "channel", f"{name}.example.json")
        if not os.path.isfile(dst) and os.path.isfile(example):
            shutil.copyfile(example, dst)
            print(f"создан channel/{name}.json из шаблона")
    for d in ("input", "transcripts", "edit-plans", "temp", "output", "cta", "ideas", "community", "secrets",
              "assets/broll", "assets/music", "assets/sfx", "assets/luts"):
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    a = assets_status()
    if not a["luts"]:
        subprocess.call([py, os.path.join(PIPELINE, "make_luts.py")])
    if not a["sfx"] and shutil.which("ffmpeg"):
        subprocess.call([py, os.path.join(PIPELINE, "synth_assets.py")])


def make_venv():
    venv = os.path.join(ROOT, ".venv")
    py = os.path.join(venv, "Scripts" if os.name == "nt" else "bin", "python")
    if not os.path.isfile(py):
        print(f"создаю виртуальное окружение {venv}")
        subprocess.check_call([sys.executable, "-m", "venv", venv])
    print(f"перезапуск установщика внутри .venv: {py}")
    args = [a for a in sys.argv[1:] if a != "--venv"]
    sys.exit(subprocess.call([py, os.path.abspath(__file__)] + args))


def choose_manually(nvidia, ask_line):
    print(f"\nНомера компонентов через пробел (например: 1 2 3 5). Пусто — только ядро.")
    for i, comp in enumerate(COMPONENTS, 1):
        default = comp[4] in ("required", "recommended") or (comp[4] == "gpu" and nvidia)
        print(f"  {i}. {comp[0]:8} {comp[1]}{'  (рекомендуется)' if default else ''}")
    raw = ask_line("> ").replace(",", " ").split()
    keys = {"core"}
    for tok in raw:
        if tok.isdigit() and 1 <= int(tok) <= len(COMPONENTS):
            keys.add(COMPONENTS[int(tok) - 1][0])
        elif tok in {c[0] for c in COMPONENTS}:
            keys.add(tok)
    return keys


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="только проверить")
    ap.add_argument("--auto", action="store_true", help="поставить рекомендуемое без вопросов")
    ap.add_argument("--all", action="store_true", help="поставить все компоненты")
    ap.add_argument("--only", help="список компонентов через запятую: " + ",".join(c[0] for c in COMPONENTS))
    ap.add_argument("--yes", "-y", action="store_true", help="на все вопросы отвечать «да»")
    ap.add_argument("--venv", action="store_true", help="ставить в отдельное окружение .venv")
    ap.add_argument("--test", action="store_true", help="после установки прогнать тесты пайплайна")
    a = ap.parse_args()
    if a.venv and not sys.prefix.startswith(os.path.join(ROOT, ".venv")):
        make_venv()

    noninteractive = a.auto or a.all or a.only or a.yes or not sys.stdin.isatty()

    def ask_line(prompt):
        try:
            return input(prompt)
        except EOFError:
            return ""

    def ask(question):
        if a.yes or a.auto or a.all:
            return True
        if not sys.stdin.isatty():
            return False
        return ask_line(f"{question} [Y/n] ").strip().lower() in ("", "y", "yes", "д", "да")

    print(f"{B}Нарезчик — установка{X}  {DIM}{ROOT}{X}")
    nvidia = has_nvidia()
    ff = report(nvidia)
    if a.check:
        return

    recommended = {c[0] for c in COMPONENTS if c[4] in ("required", "recommended")
                   or (c[4] == "gpu" and nvidia)}
    if a.all:
        keys = {c[0] for c in COMPONENTS if c[4] != "gpu" or nvidia}
    elif a.only:
        keys = {"core"} | {k.strip() for k in a.only.split(",") if k.strip()}
    elif a.auto or noninteractive:
        keys = recommended
    else:
        print(f"\n{B}Что делаем?{X}\n"
              "  1) Установить всё рекомендуемое автоматически\n"
              "  2) Установить вообще всё (+ публикация на YouTube, сервер, тесты)\n"
              "  3) Выбрать компоненты вручную\n"
              "  4) Только проверить и выйти")
        choice = ask_line("> ").strip() or "1"
        if choice == "4":
            return
        keys = (recommended if choice == "1"
                else {c[0] for c in COMPONENTS if c[4] != "gpu" or nvidia} if choice == "2"
                else choose_manually(nvidia, ask_line))

    unknown = keys - {c[0] for c in COMPONENTS}
    if unknown:
        sys.exit(f"неизвестные компоненты: {', '.join(sorted(unknown))}")

    failed = []
    if not ff and not install_ffmpeg(ask):
        failed.append("ffmpeg")

    todo = [c for c in COMPONENTS if c[0] in keys and not all(has_module(m) for m in c[3])]
    packages = [p for c in todo for p in c[2]]
    if packages:
        print(f"\n{B}Ставлю пакеты:{X} {', '.join(c[0] for c in todo)}")
        if not pip_install(packages):
            # одним вызовом быстрее; при ошибке — по компонентам, чтобы понять, что именно упало
            for c in todo:
                if not pip_install(c[2]):
                    failed.append(c[0])
    if "face" in keys and not download_face_model():
        failed.append("face-model")

    print(f"\n{B}Ассеты{X}")
    setup_assets()

    if a.test and has_module("pytest"):
        subprocess.call([sys.executable, "-m", "pytest", "tests/", "-q"], cwd=PIPELINE)

    report(nvidia)
    if failed:
        print(f"\n{R}Не получилось: {', '.join(failed)}.{X} Подробности выше; ручная установка — docs/setup.md.")
    else:
        print(f"\n{G}Готово.{X}")
    print(f"\n{B}Дальше:{X}\n"
          "  1. Откройте эту папку в Claude Code: `claude`\n"
          "  2. Настройте канал: `/setup-channel @ваш_канал` (нужен YouTube API-ключ — docs/setup-channel.md)\n"
          "  3. Положите видео в input/ и скажите: `/shorts input/видео.mp4` или `/long input/видео.mp4`")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
