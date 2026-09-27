"""Вырезание рабочего окна из большого исходника.

filter_complex из filtergraph.py раздаёт декодированный поток по веткам через split,
поэтому ffmpeg проходит декодером весь входной файл. Прогонять 27-минутный 4K ради
60 секунд полезного материала бессмысленно — сначала режем окно, граф работает по нему.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import TEMP_DIR, ffprobe_json, log, run


def window_bounds(speech_edits, pad_sec=2.0):
    """Границы одного непрерывного окна, покрывающего все куски речи, с запасом."""
    if not speech_edits:
        raise ValueError("window_bounds: speech_edits пуст")
    start = min(s["source_start_sec"] for s in speech_edits) - pad_sec
    end = max(s["source_end_sec"] for s in speech_edits) + pad_sec
    return max(0.0, start), end


def window_clusters(speech_edits, pad_sec=2.0, max_gap_sec=20.0):
    """Список окон, покрывающих речь: соседние куски сливаются в одно окно, но только
    пока дыра между ними меньше max_gap_sec.

    Для Shorts почти всегда получается одно окно — материал берётся из одного места
    исходника. Для лонга это принципиально другое: там сегменты разбросаны по всему
    27-минутному файлу (хук с 07:51, бэкстори с 01:05, практика с 18:38), и одно общее
    окно означало бы перекод почти всего исходника в crf 14 ради 5 минут полезного
    материала. Несколько окон — несколько входов одного и того же графа, склейка
    по-прежнему происходит внутри единственного filter_complex.
    """
    if not speech_edits:
        raise ValueError("window_clusters: speech_edits пуст")

    spans = sorted((s["source_start_sec"], s["source_end_sec"]) for s in speech_edits)
    windows = [[max(0.0, spans[0][0] - pad_sec), spans[0][1] + pad_sec]]
    for start, end in spans[1:]:
        if start - pad_sec - windows[-1][1] <= max_gap_sec:
            windows[-1][1] = max(windows[-1][1], end + pad_sec)
        else:
            windows.append([max(0.0, start - pad_sec), end + pad_sec])
    return [(w[0], w[1]) for w in windows]


def extract_many(source_path, clip_id, windows, force=False):
    """Вырезать все окна. Одно окно сохраняет старое имя temp/<clip_id>_work.mp4 —
    на него рассчитывают autoframe.py и make_cover.py."""
    if len(windows) == 1:
        return [extract(source_path, clip_id, windows[0][0], windows[0][1], force)]
    return [extract(source_path, clip_id, s, e, force, part=i)
            for i, (s, e) in enumerate(windows)]



def _duration_ok(path, expected_sec, tolerance_sec=1.0):
    """Файл существует и по длительности совпадает с окном (±tolerance)."""
    try:
        info = ffprobe_json(path)
        actual = float(info["format"]["duration"])
    except Exception:
        return False
    return abs(actual - expected_sec) <= tolerance_sec


def extract(source_path, clip_id, start, end, force=False, part=None):
    """Вырезать окно в temp/<clip_id>_work.mp4 (или _work<NN>.mp4 для лонга с
    несколькими окнами).

    crf 12 — визуально неотличимо от исходника (было 14: три перекода подряд давали мыло). Звук в pcm_s16le: у PCM нет priming
    padding, поэтому промежуточный файл не вносит рассинхрон на стыках.
    """
    name = f"{clip_id}_work.mp4" if part is None else f"{clip_id}_work{part:02d}.mp4"
    out = os.path.join(TEMP_DIR, name)
    if os.path.exists(out) and not force:
        # Обрывок от упавшего прогона (например, x264 не хватило памяти) тоже
        # «существует» — но короче окна. Без проверки длительности он молча
        # уходил в граф, и ролик собирался с дырой на этом месте.
        if _duration_ok(out, end - start):
            log(f"рабочее окно уже есть, переиспользую: {out}")
            return out
        log(f"рабочее окно {out} битое/неполное — перевырезаю")
        os.remove(out)

    log(f"вырезаю рабочее окно {start:.2f}-{end:.2f} ({end - start:.1f}с) из {source_path}")
    # ffmpeg пишет во временное имя, готовое окно появляется только после успеха. Иначе
    # файл, оборванный OOM-killer'ом или таймаутом, следующий прогон молча переиспользовал
    # бы как готовый (так 14.09.2026 ломались smoke-тесты: окно на 0 байт)
    tmp_out = out + ".part.mp4"
    try:
        run([
            "ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", source_path,
            "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
            "-c:a", "pcm_s16le",
            tmp_out,
        ], timeout=1800)
    except BaseException:
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        raise
    os.replace(tmp_out, out)
    return out
