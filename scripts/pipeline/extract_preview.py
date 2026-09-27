#!/usr/bin/env python
"""Быстрый превью-инструмент для ревью без полного просмотра в плеере.

Два режима:
    grid — контактный лист N кадров (по умолчанию 9, сетка 3x3) в один .jpg —
           быстро окинуть взглядом визуальное разнообразие ролика.
    clip — маленькое низкобитрейтное .mp4 (по умолчанию 640px по широкой стороне) —
           для быстрого прогона глазами без ожидания финального рендера в полном качестве.

Usage:
    python extract_preview.py output/interview_short03.mp4 --mode grid
    python extract_preview.py output/interview_short03.mp4 --mode clip --out temp/preview.mp4
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import TEMP_DIR, ffprobe_json, log, resolve_repo_path, run


def get_duration(path):
    data = ffprobe_json(path)
    return float(data["format"]["duration"])


def make_grid(path, out_path, tiles=9, cols=3):
    duration = get_duration(path)
    rows = -(-tiles // cols)
    interval = max(duration / tiles, 0.5)
    run([
        "ffmpeg", "-y", "-i", path,
        "-vf", f"fps=1/{interval},scale=320:-1,tile={cols}x{rows}",
        "-frames:v", "1", out_path,
    ], timeout=300)
    return out_path


def make_clip(path, out_path, target_width=640):
    run([
        "ffmpeg", "-y", "-i", path,
        "-vf", f"scale={target_width}:-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        "-c:a", "aac", "-b:a", "96k", out_path,
    ], timeout=300)
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("--mode", choices=["grid", "clip"], default="grid")
    ap.add_argument("--out", help="путь к выходному файлу (по умолчанию temp/<name>_preview.jpg|mp4)")
    ap.add_argument("--tiles", type=int, default=9, help="кол-во кадров для режима grid")
    args = ap.parse_args()

    path = resolve_repo_path(args.video)
    base = os.path.splitext(os.path.basename(path))[0]

    if args.out:
        out_path = resolve_repo_path(args.out)
    else:
        ext = "jpg" if args.mode == "grid" else "mp4"
        out_path = os.path.join(TEMP_DIR, f"{base}_preview.{ext}")

    try:
        if args.mode == "grid":
            make_grid(path, out_path, args.tiles)
        else:
            make_clip(path, out_path)
    except Exception as e:
        log(f"FAILED: {e}")
        sys.exit(1)

    log(f"wrote {out_path}")


if __name__ == "__main__":
    main()
