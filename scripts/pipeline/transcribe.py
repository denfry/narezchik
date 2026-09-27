#!/usr/bin/env python
"""transcription deterministic backend.

Извлекает аудио из исходника и гонит faster-whisper с word-level timestamps.
Пробует GPU (CUDA через ctranslate2 — работает независимо от того, CPU или GPU
сборка torch, см. docs/setup.md) и падает обратно на CPU int8, если CUDA недоступна
или модель не помещается в память.

Пишет два файла в transcripts/:
    <clip_id>.json  — machine-readable: segments[] с words[] (start/end/word), полный текст
    <clip_id>.txt   — human-readable [start-end] text, тот же формат, что уже использует
                       scripts/transcribe_all.py, чтобы существующий clean_transcript.py
                       мог при желании отработать и по shorts-транскриптам.

Usage:
    python transcribe.py input/interview.mp4 --clip-id interview
    python transcribe.py input/interview.mp4 --clip-id interview --model medium --device cpu
"""
import argparse
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import TEMP_DIR, TRANSCRIPTS_DIR, log, resolve_repo_path, run, save_json

DEFAULT_MODEL = "medium"
HALLUCINATION_MARKERS = ("редактор субтитров", "корректор", "amara.org", "subtitles by")


def extract_audio(video_path, out_wav):
    run([
        "ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", out_wav,
    ], timeout=1800)
    return out_wav


def _add_cuda_dll_dirs():
    """ctranslate2 (faster-whisper's backend) looks up cuBLAS/cuDNN via the standard
    Windows DLL search path. The pip wheels nvidia-cublas-cu12/nvidia-cudnn-cu12/
    nvidia-cuda-nvrtc-cu12 (see setup.md) install the actual DLLs under
    site-packages/nvidia/<pkg>/bin but do NOT add that directory to PATH themselves —
    without this, CUDA fails at inference time (not at model load time) with
    'cublas64_12.dll is not found or cannot be loaded'. Best-effort/no-op if those
    wheels aren't installed (falls straight through to the CPU path)."""
    if os.name != "nt":
        return
    try:
        import nvidia
    except ImportError:
        return
    for base in list(getattr(nvidia, "__path__", [])):
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            bin_dir = os.path.join(base, name, "bin")
            if os.path.isdir(bin_dir):
                try:
                    os.add_dll_directory(bin_dir)
                except (OSError, AttributeError):
                    pass
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


def load_model(device, model_size):
    if device == "cuda":
        _add_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    if device == "cuda":
        model = WhisperModel(model_size, device="cuda", compute_type="int8_float16")
        log(f"loaded model '{model_size}' on CUDA (int8_float16)")
        return model
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    log(f"loaded model '{model_size}' on CPU (int8)")
    return model


def _run_transcription(model, wav_path, language):
    """Runs model load result through transcribe() and fully consumes the segment
    generator. Raising happens here, not just at model construction — a missing CUDA
    runtime lib (e.g. cublas64_12.dll) fails at inference time, not at load time, so
    the CUDA->CPU fallback in transcribe() must wrap this call too, not just load_model()."""
    segments_iter, info = model.transcribe(
        wav_path, language=language, word_timestamps=True, vad_filter=True, beam_size=5,
    )
    segments = []
    full_text_parts = []
    stopped_early = False
    for seg in segments_iter:
        text = seg.text.strip()
        low = text.lower()
        if any(marker in low for marker in HALLUCINATION_MARKERS):
            log(f"stopping at hallucinated tail segment: {text!r}")
            stopped_early = True
            break
        words = [
            {"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip()}
            for w in (seg.words or []) if w.word.strip()
        ]
        segments.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": text,
            "words": words,
        })
        full_text_parts.append(text)
    return segments, full_text_parts, stopped_early, info


def transcribe(video_path, clip_id, model_size=DEFAULT_MODEL, device="auto", language="ru",
                json_path=None, txt_path=None):
    """If json_path/txt_path are given, writes output files BEFORE the function returns
    and BEFORE process exit — while the CUDA model object is still alive/in-scope.

    This works around a reproducible crash on this machine: ctranslate2's CUDA model
    destructor (triggered when `model` goes out of scope on return, or at normal
    interpreter shutdown) hard-crashes the process (observed as exit code 127 via
    git-bash's DLL-not-found exit mapping) AFTER transcription completes successfully
    but BEFORE any code following the transcription call gets to run. Writing results
    first, then calling os._exit(0) immediately (bypassing normal object finalization/
    atexit/interpreter shutdown), sidesteps the crash entirely. See docs/setup.md notes on
    GPU transcription for background on the CUDA DLL setup this depends on."""
    video_path = resolve_repo_path(video_path)
    wav_path = os.path.join(TEMP_DIR, f"{clip_id}_audio.wav")
    # Вход может уже БЫТЬ тем самым wav: удобно переиспользовать извлечённое аудио,
    # не гоняя ffmpeg по 4K-исходнику второй раз. Без этой проверки ffmpeg открывает
    # файл на запись и читает его же — вход уничтожается, а транскрипция молча
    # получается на несколько секунд вместо получаса.
    if os.path.abspath(video_path) == os.path.abspath(wav_path):
        log(f"вход уже является целевым wav, извлечение пропущено: {wav_path}")
    else:
        log(f"extracting audio -> {wav_path}")
        extract_audio(video_path, wav_path)

    t0 = time.time()
    try_order = ["cuda", "cpu"] if device == "auto" else [device]
    last_error = None
    segments = full_text_parts = stopped_early = info = used_device = None
    for candidate in try_order:
        try:
            model = load_model(candidate, model_size)
            segments, full_text_parts, stopped_early, info = _run_transcription(model, wav_path, language)
            used_device = candidate
            break
        except ImportError:
            log("faster-whisper is not installed. Run: pip install -r scripts/pipeline/requirements.txt")
            raise
        except Exception as e:
            last_error = e
            if candidate == "cuda":
                log(f"CUDA transcription failed ({e}), falling back to CPU")
            else:
                raise
    if used_device is None:
        raise last_error

    elapsed = time.time() - t0
    log(f"transcribed {len(segments)} segments in {elapsed:.0f}s "
        f"({info.duration / elapsed:.1f}x realtime, device={used_device})"
        + (" [stopped on hallucinated tail]" if stopped_early else ""))

    out = {
        "clip_id": clip_id,
        "source_path": video_path,
        "language": language,
        "duration_sec": round(info.duration, 3),
        "device_used": used_device,
        "model": model_size,
        "text": " ".join(full_text_parts),
        "segments": segments,
    }

    if os.path.exists(wav_path):
        os.remove(wav_path)

    if json_path and txt_path:
        # Write NOW, while `model`/`segments_iter` are still alive in this frame — see
        # docstring above. Then hard-exit before returning, to skip the CUDA destructor
        # that crashes the process on normal return/interpreter shutdown on this machine.
        save_json(json_path, out)
        write_txt(out, txt_path)
        log(f"wrote {json_path}")
        log(f"wrote {txt_path}")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    return out


def write_txt(data, txt_path):
    with io.open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"# source: {data['source_path']}\n")
        for seg in data["segments"]:
            f.write(f"[{seg['start']:.1f}-{seg['end']:.1f}] {seg['text']}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="путь к видео или .wav")
    ap.add_argument("--clip-id", required=True, help="слаг для имён выходных файлов")
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=["tiny", "base", "small", "medium", "large-v3"])
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--language", default="ru")
    args = ap.parse_args()

    json_path = os.path.join(TRANSCRIPTS_DIR, f"{args.clip_id}.json")
    txt_path = os.path.join(TRANSCRIPTS_DIR, f"{args.clip_id}.txt")

    try:
        data = transcribe(args.input, args.clip_id, args.model, args.device, args.language,
                           json_path=json_path, txt_path=txt_path)
    except ImportError:
        sys.exit(2)
    except Exception as e:
        log(f"FAILED: {e}")
        sys.exit(1)

    # Normal return only happens if transcribe() was called without json_path/txt_path
    # (not the case here) or in future direct-import usage — keep as a safety net.
    if data is not None:
        save_json(json_path, data)
        write_txt(data, txt_path)
        log(f"wrote {json_path}")
        log(f"wrote {txt_path}")


if __name__ == "__main__":
    main()
