#!/usr/bin/env python
"""Синтез музыкальных подложек и переходных звуков средствами ffmpeg.

Зачем синтез, а не скачивание. Готовый трек с чужой лицензией — это риск claim по
Content ID на канале, причём риск отложенный: ролик уже опубликован, просмотры идут,
а монетизация уходит правообладателю. Особенно это касается «CC0»-заливок на файловых
архивах, где лицензию проставил загрузивший, а не автор. Всё, что генерирует этот
скрипт, создано из синусов и шума прямо здесь, поэтому прав третьих лиц на нём нет.

Это подложки, а не музыка на первом плане: они звучат на -18…-22 dB под голосом и с
дакингом. Их задача — убрать ощущение необработанной записи, а не быть заметными.
Если понадобится настоящая мелодия, брать её в YouTube Audio Library — она встроена
в Studio и безопаснее всего по Content ID.

Usage:
    python synth_assets.py            # собрать всё, что ещё не собрано
    python synth_assets.py --force    # пересобрать заново
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, log, run

MUSIC_DIR = os.path.join(ROOT, "assets", "music")
SFX_DIR = os.path.join(ROOT, "assets", "sfx")

MUSIC_SEC = 75
SR = 48000

# Каждая подложка — сумма синусов с медленной амплитудной модуляцией. Ноты взяты в
# миноре от одного корня, чтобы бесшовно склеиваться с любым местом ролика: подложка
# без выраженной гармонической прогрессии не спорит с интонацией речи.
MUSIC = {
    # ля-минор, суб-бас и квинта: тёмный неподвижный гул под жёсткие монологи
    "dark_drone_01": (
        "(0.24*sin(2*PI*55*t) + 0.11*sin(2*PI*82.41*t) + 0.05*sin(2*PI*110*t))"
        "*(0.62+0.38*sin(2*PI*t*0.125))"
    ),
    # пульс раз в 0.75 с — нарастание под перечисления и подводку к payoff
    "tense_pulse_01": (
        "(0.20*sin(2*PI*73.42*t) + 0.09*sin(2*PI*146.83*t))"
        "*(0.35+0.65*pow(abs(sin(PI*t/0.75)),6))"
    ),
    # верхний воздух без баса — под спокойный личный разговор
    "soft_air_01": (
        "(0.10*sin(2*PI*220*t) + 0.07*sin(2*PI*329.63*t) + 0.05*sin(2*PI*440*t))"
        "*(0.55+0.45*sin(2*PI*t*0.09))"
    ),
}

# Переходные звуки. Экспонента exp(-k*t) — это затухание: без неё щелчок звучит как
# обрубленный тон, потому что резкий обрыв волны сам по себе даёт слышимый клик.
SFX = {
    # свуш: розовый шум, полосовой фильтр, быстрый вход и мягкий выход
    "whoosh_01": {
        "src": f"anoisesrc=d=0.45:c=pink:a=0.9:r={SR}",
        "af": ("bandpass=f=1400:width_type=o:w=2.2,"
               "volume='pow(min(1,t*8),2)*exp(-5.5*t)':eval=frame,"
               "alimiter=limit=0.9"),
        "dur": 0.45,
    },
    # низкий удар: частота падает со 120 до ~35 Гц, отсюда квадрат по t в фазе
    "impact_low_01": {
        "expr": "0.85*sin(2*PI*(120*t - 55*t*t))*exp(-2.6*t)",
        "dur": 1.4,
    },
    # тик: короткий высокий щелчок на смену мысли
    "tick_01": {
        "expr": "0.55*sin(2*PI*2100*t)*exp(-55*t)",
        "dur": 0.16,
    },
}


SFX_TARGET_PEAK_DB = -6.0


def _peak_db(path):
    import re
    import subprocess
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"max_volume: (\S+) dB", r.stderr)
    return float(m.group(1)) if m else None


def _normalise_peak(path, target_db=SFX_TARGET_PEAK_DB):
    """Привести пик файла к общему уровню.

    Фильтры дают очень разный выход: полосовой фильтр на шуме срезает почти всю
    энергию, и свуш выходил на 17 дБ тише удара. Подбирать усиление руками под каждый
    звук нельзя — оно разъедется при первой же правке выражения, поэтому усиление
    считается из измеренного пика.
    """
    peak = _peak_db(path)
    if peak is None or abs(peak - target_db) < 0.5:
        return
    gain = target_db - peak
    tmp = path + ".tmp.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", path,
         "-af", f"volume={gain:.2f}dB", "-c:a", "pcm_s16le", tmp], timeout=120)
    os.replace(tmp, path)
    log(f"  пик {peak:+.1f} -> {target_db:+.1f} dB (усиление {gain:+.1f})")


def _exists(path, force):
    if os.path.exists(path) and not force:
        log(f"уже есть, пропускаю: {os.path.basename(path)}")
        return True
    return False


def build_music(force=False):
    os.makedirs(MUSIC_DIR, exist_ok=True)
    out_paths = []
    for name, expr in MUSIC.items():
        out = os.path.join(MUSIC_DIR, f"{name}.mp3")
        out_paths.append(out)
        if _exists(out, force):
            continue
        log(f"синтезирую подложку {name} ({MUSIC_SEC}с)")
        run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"aevalsrc='{expr}':d={MUSIC_SEC}:s={SR}",
            # плавные края, чтобы подложку можно было ставить в любое место ролика
            # Фейд на входе очень короткий: он нужен только чтобы не щёлкнуло на старте
            # волны. Длинный фейд-ин означает, что первые секунды ролика идут почти в
            # тишине — а именно там и стоит обложка, где голоса ещё нет.
            "-af", f"afade=t=in:st=0:d=0.05,afade=t=out:st={MUSIC_SEC - 3}:d=3,"
                   # Трек сводится к той же громкости, что и целевая громкость ролика
                   # (-14 LUFS). Тогда gain_db из плана читается буквально: -18 дБ
                   # означает подложку на 18 дБ тише голоса. Если свести трек тише,
                   # gain_db вычтется поверх, и подложка уйдёт в неслышимое — так и
                   # было: трек -20 LUFS плюс gain -20 дБ дали 26 дБ под голосом.
                   "aformat=channel_layouts=stereo,loudnorm=I=-14:TP=-2:LRA=7",
            "-c:a", "libmp3lame", "-b:a", "192k", out,
        ], timeout=600)
    return out_paths


def build_sfx(force=False):
    os.makedirs(SFX_DIR, exist_ok=True)
    out_paths = []
    for name, spec in SFX.items():
        out = os.path.join(SFX_DIR, f"{name}.wav")
        out_paths.append(out)
        if _exists(out, force):
            continue
        log(f"синтезирую звук {name}")
        if "expr" in spec:
            src = f"aevalsrc='{spec['expr']}':d={spec['dur']}:s={SR}"
            af = "aformat=channel_layouts=stereo"
        else:
            src = spec["src"]
            af = spec["af"] + ",aformat=channel_layouts=stereo"
        run([
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src,
            "-af", af, "-c:a", "pcm_s16le", out,
        ], timeout=300)
        _normalise_peak(out)
    return out_paths


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="пересобрать существующие файлы")
    args = ap.parse_args()
    build_music(args.force)
    build_sfx(args.force)
    log("готово")


if __name__ == "__main__":
    main()
