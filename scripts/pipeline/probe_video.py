#!/usr/bin/env python
"""video-ingest deterministic backend.

Пробегает ffprobe/ffmpeg по исходнику и выдаёт JSON с техническими параметрами
(resolution/fps/codec/audio/duration/orientation/loudness/кол-во дорожек) — ровно
поля 'source' из schemas/edit-plan.schema.json, плюс человекочитаемое summary в stdout.

Usage:
    python probe_video.py input/interview.mp4
    python probe_video.py input/interview.mp4 --json-out temp/interview_probe.json
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ffprobe_json, log, resolve_repo_path, run, save_json, to_repo_relative


def parse_fps(rate_str):
    if not rate_str or rate_str == "0/0":
        return None
    if "/" in rate_str:
        num, den = rate_str.split("/")
        den = float(den)
        return round(float(num) / den, 3) if den else None
    return float(rate_str)


LOUDNESS_SAMPLE_SEC = 180


def measure_loudness(path, duration=0.0):
    """Integrated loudness (LUFS) via ffmpeg ebur128. Returns None if measurement fails
    (e.g. no audio stream) — loudness is informational, must never block the pipeline.

    Длинный файл меряется по выборке: три минуты с середины. Полный проход по 18-ГБ
    подкасту с HDD шёл больше десяти минут ради справочного числа.
    """
    try:
        seek = []
        if duration > LOUDNESS_SAMPLE_SEC * 2:
            seek = ["-ss", f"{duration / 2:.0f}", "-t", str(LOUDNESS_SAMPLE_SEC)]
        result = run([
            "ffmpeg", "-nostats", *seek, "-i", path, "-vn", "-af", "ebur128=framelog=quiet",
            "-f", "null", "-",
        ], timeout=600)
        stderr = result.stderr.decode(errors="replace")
        m = re.findall(r"I:\s*(-?\d+\.?\d*)\s*LUFS", stderr)
        return float(m[-1]) if m else None
    except Exception as e:
        log(f"loudness measurement skipped: {e}")
        return None


def probe(path, measure_audio=True):
    path = resolve_repo_path(path)
    data = ffprobe_json(path)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        raise RuntimeError(f"no video stream found in {path}")
    v = video_streams[0]
    a = audio_streams[0] if audio_streams else {}

    width = int(v.get("width") or 0)
    height = int(v.get("height") or 0)
    if width == height:
        orientation = "square"
    elif height > width:
        orientation = "portrait"
    else:
        orientation = "landscape"

    duration = float(fmt.get("duration") or v.get("duration") or 0)

    out = {
        "path": to_repo_relative(path),
        "duration_sec": round(duration, 3),
        "fps": parse_fps(v.get("avg_frame_rate") or v.get("r_frame_rate")),
        "width": width,
        "height": height,
        "orientation": orientation,
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name"),
        "audio_channels": int(a.get("channels")) if a.get("channels") else None,
        "audio_tracks": len(audio_streams),
        "loudness_lufs": measure_loudness(path, duration) if (measure_audio and audio_streams) else None,
        "bit_rate": int(fmt.get("bit_rate")) if fmt.get("bit_rate") else None,
        "file_size_bytes": int(fmt.get("size")) if fmt.get("size") else None,
    }
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="путь к видео (в input/ или абсолютный)")
    ap.add_argument("--json-out", help="куда сохранить JSON (по умолчанию только stdout)")
    ap.add_argument("--no-loudness", action="store_true", help="пропустить измерение loudness (быстрее)")
    args = ap.parse_args()

    try:
        info = probe(args.input, measure_audio=not args.no_loudness)
    except Exception as e:
        log(f"FAILED: {e}")
        sys.exit(1)

    log(f"{info['width']}x{info['height']} @ {info['fps']}fps, {info['duration_sec']:.1f}s, "
        f"{info['orientation']}, video={info['video_codec']}, audio={info['audio_codec']}"
        f"({info['audio_tracks']} track(s)), loudness={info['loudness_lufs']} LUFS")

    if args.json_out:
        save_json(resolve_repo_path(args.json_out), info)
        log(f"wrote {args.json_out}")

    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
