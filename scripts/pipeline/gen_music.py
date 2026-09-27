#!/usr/bin/env python
"""Локальная генерация музыкальной подложки моделью ACE-Step (Apache 2.0).

Зачем свой генератор, а не скачанные треки: у скачанного файла лицензию проставляет
загрузивший, а не правообладатель, и Content ID прилетает уже после публикации. У
сгенерированного локально трека правообладателя нет вообще — матчить нечего. Плюс
под каждый ролик своя подложка, а не одна и та же на всём канале.

Запускается системным python: сам перезапускает себя интерпретатором из venv ACE-Step
(переменная окружения ACE_STEP_PYTHON, см. docs/music.md), где стоит torch с CUDA.
Чекпоинты (~3.5 ГБ) — в ACE_STEP_CHECKPOINTS (по умолчанию ~/ace-step-checkpoints),
качаются при первом запуске.

Usage:
    python gen_music.py --mood dark_debate --clip-id podcast_long01 --duration 120
    python gen_music.py --prompt "warm lo-fi, soft piano, calm, instrumental" \\
        --out assets/music/my_track.wav --duration 90
Готовые настроения — MOODS ниже; результат всегда instrumental и без вокала.
"""
import argparse
import datetime
import os
import subprocess
import sys

VENV_PY = os.environ.get("ACE_STEP_PYTHON", "")
CKPT_DIR = os.environ.get("ACE_STEP_CHECKPOINTS",
                          os.path.join(os.path.expanduser("~"), "ace-step-checkpoints"))
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MUSIC_DIR = os.path.join(ROOT, "assets", "music")

# Промпт — тегами, как учит ACE-Step: жанр + настроение + инструменты + темп.
# Все подложки медленные и без ударной доли: они лежат под речью на -22…-26 dB.
MOODS = {
    "dark_debate": ("dark ambient, cinematic tension, slow tempo, deep synth drone, "
                    "sparse piano notes, subtle low percussion, minimal, instrumental"),
    "warm_story":  ("warm lo-fi, soft felt piano, gentle pad, calm, slow tempo, "
                    "intimate, instrumental"),
    "money_focus": ("minimal electronic, focused, steady pulse, deep bass, clean synth "
                    "arpeggio, modern, mid-slow tempo, instrumental"),
    "provocation": ("dark trap ambient, heavy sub bass, slow tempo, tense strings, "
                    "sparse hits, cinematic, instrumental"),
    "uplift_end":  ("cinematic uplifting, warm strings, slow build, hopeful piano, "
                    "soft drums, instrumental"),
}


def ensure_venv():
    """Перезапуск под интерпретатором venv, если acestep недоступен в текущем."""
    try:
        import acestep  # noqa: F401
        return
    except ImportError:
        pass
    if not VENV_PY or not os.path.isfile(VENV_PY):
        sys.exit("ACE-Step не найден: задайте ACE_STEP_PYTHON (python из venv ACE-Step) — "
                 "см. docs/music.md")
    # subprocess, а не os.execv: на Windows execv порождает дочерний процесс и сразу
    # возвращает управление — вызывающий скрипт думал, что генерация закончилась
    rc = subprocess.call([VENV_PY, "-u"] + sys.argv)
    sys.exit(rc)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mood", choices=sorted(MOODS))
    ap.add_argument("--prompt", help="свой промпт тегами (перекрывает --mood)")
    ap.add_argument("--clip-id", help="имя ролика для имени файла")
    ap.add_argument("--out", help="явный путь к .wav")
    ap.add_argument("--duration", type=float, default=120.0, help="секунды, до 240")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--variants", type=int, default=1, help="сколько вариантов сгенерить")
    args = ap.parse_args()
    if not (args.mood or args.prompt):
        ap.error("нужен --mood или --prompt")

    ensure_venv()
    from acestep.pipeline_ace_step import ACEStepPipeline

    prompt = args.prompt or MOODS[args.mood]
    os.makedirs(MUSIC_DIR, exist_ok=True)
    os.makedirs(CKPT_DIR, exist_ok=True)

    # cpu_offload — на видеокарте с 8 ГБ без него модель не помещается
    pipe = ACEStepPipeline(checkpoint_dir=CKPT_DIR, dtype="bfloat16",
                           cpu_offload=True, overlapped_decode=True)

    made = []
    for i in range(args.variants):
        base = args.out or os.path.join(
            MUSIC_DIR, f"{args.clip_id or 'track'}_{args.mood or 'custom'}"
                       f"{'' if args.variants == 1 else f'_v{i + 1}'}.wav")
        seeds = [args.seed + i] if args.seed is not None else None
        paths = pipe(
            format="wav",
            audio_duration=min(240.0, max(10.0, args.duration)),
            prompt=prompt,
            lyrics="[instrumental]",  # без вокала: подложка лежит под речью
            infer_step=args.steps,
            guidance_scale=15.0,
            manual_seeds=seeds,
            save_path=base,
        )
        wav = next((p for p in paths if p.lower().endswith(".wav")), base)
        made.append(wav)
        # заметка о происхождении — её потом копируют в assets[].license_note плана
        with open(os.path.splitext(wav)[0] + ".txt", "w", encoding="utf-8") as f:
            f.write(f"ACE-Step, сгенерировано локально {datetime.date.today():%d.%m.%Y}\n"
                    f"prompt: {prompt}\nseed: {seeds}\nduration: {args.duration}\n")
        print(f"wrote {wav}", flush=True)
    return made


if __name__ == "__main__":
    main()
    # torch/CUDA-потоки иногда не дают интерпретатору завершиться штатно —
    # результат уже на диске, выходим жёстко
    sys.stdout.flush()
    os._exit(0)
