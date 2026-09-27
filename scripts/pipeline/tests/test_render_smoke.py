"""Сквозной тест на синтетическом видео: проверяет, что рендер собирается
и что длительность сходится с планом. Настоящий исходник для этого не нужен."""
import os
import subprocess

import pytest

import common
import render_short


@pytest.fixture
def synthetic_source(tmp_path):
    src = str(tmp_path / "src.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=30",
        "-f", "lavfi", "-i", "sine=f=440:d=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", src,
    ], check=True)
    return src


def test_render_produces_video_of_planned_duration(synthetic_source, tmp_path):
    plan = {
        "project": {"clip_id": "unit_smoke", "stage": "audio_planned"},
        "source": {"path": synthetic_source, "duration_sec": 30.0, "fps": 30.0,
                   "width": 1920, "height": 1080, "orientation": "landscape"},
        "clip": {"source_start_sec": 2.0, "source_end_sec": 12.0},
        "speech_edits": [
            {"id": "seg01", "source_start_sec": 2.0, "source_end_sec": 6.0, "role": "hook"},
            {"id": "seg02", "source_start_sec": 8.0, "source_end_sec": 12.0, "role": "payoff"},
        ],
        "visual_events": [
            {"type": "punch_in", "segment_ref": "seg02", "start_sec": 6.0, "end_sec": 8.0,
             "crop": {"crop_x": 300, "zoom": 1.2, "ease_ms": 300}},
        ],
        "subtitle_events": [],
        "audio_events": [{"type": "voice_normalize"}],
        "assets": [],
        "render_settings": {"width": 1080, "height": 1920, "fps": 30, "crf": 23,
                            "preset": "ultrafast", "target_lufs": -14, "master_4k": False},
    }
    plan_path = str(tmp_path / "unit_smoke.json")
    common.save_json(plan_path, plan)

    out = render_short.render(plan_path)

    assert os.path.exists(out)
    info = common.ffprobe_json(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    # план — 8 секунд речи; допуск на округление кадров
    assert abs(float(info["format"]["duration"]) - 8.0) < 0.5
    # звуковая дорожка должна существовать и быть непустой
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert float(a.get("duration", info["format"]["duration"])) > 7.0


def test_master_4k_from_1080p_source_is_assembled_at_plan_size(synthetic_source, tmp_path):
    """Мастер 4K из 1080p-исходника: граф (кроп, наезды, склейка) собирается в размере плана,
    в 4K кадр поднимается одним lanczos в конце. 4K-ветки на каждый субклип только съедали
    память: лонг с 70 субклипами был убит OOM-killer'ом на 8 ГБ."""
    plan = {
        "project": {"clip_id": "unit_master4k", "stage": "audio_planned"},
        "source": {"path": synthetic_source, "duration_sec": 30.0, "fps": 30.0,
                   "width": 1920, "height": 1080, "orientation": "landscape"},
        "clip": {"source_start_sec": 2.0, "source_end_sec": 6.0},
        "speech_edits": [
            {"id": "seg01", "source_start_sec": 2.0, "source_end_sec": 3.0, "role": "hook"},
            {"id": "seg02", "source_start_sec": 5.0, "source_end_sec": 6.0, "role": "payoff"},
        ],
        "visual_events": [
            {"type": "punch_in", "segment_ref": "seg02", "start_sec": 1.0, "end_sec": 2.0,
             "crop": {"crop_x": None, "zoom": 1.1, "ease_ms": 300}},
        ],
        "subtitle_events": [],
        "audio_events": [{"type": "voice_normalize"}],
        "assets": [],
        "render_settings": {"width": 1920, "height": 1080, "fps": 30, "crf": 30,
                            "preset": "ultrafast", "target_lufs": -14, "master_4k": True},
    }
    plan_path = str(tmp_path / "unit_master4k.json")
    common.save_json(plan_path, plan)

    out = render_short.render(plan_path)

    v = next(s for s in common.ffprobe_json(out)["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (3840, 2160)
    graph = open(os.path.join(render_short.TEMP_DIR, "unit_master4k_graph.txt"), encoding="utf-8").read()
    assert "s=1920x1080" in graph and "s=3840x2160" not in graph
    assert graph.count("scale=3840:2160:flags=lanczos") == 1


def test_audio_track_is_not_cut_short_when_music_is_mixed_in(synthetic_source, tmp_path):
    """Звук обязан кончаться там же, где картинка.

    С зацикленной музыкой и ключом -shortest ffmpeg обрывал звуковую дорожку
    примерно на 3 секунды раньше видео — без ошибки и без предупреждения. В ролике
    это выглядело как пропавший звук на последних словах панчлайна, и заметить это
    можно только измерением длительностей дорожек.
    """
    music = str(tmp_path / "bed.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "sine=f=110:d=20", music], check=True)
    plan = {
        "project": {"clip_id": "unit_music", "stage": "audio_planned"},
        "source": {"path": synthetic_source, "duration_sec": 30.0, "fps": 30.0,
                   "width": 1920, "height": 1080, "orientation": "landscape"},
        "clip": {"source_start_sec": 2.0, "source_end_sec": 12.0},
        "speech_edits": [
            {"id": "seg01", "source_start_sec": 2.0, "source_end_sec": 7.0, "role": "hook"},
            {"id": "seg02", "source_start_sec": 8.0, "source_end_sec": 13.0, "role": "payoff"},
        ],
        "visual_events": [], "subtitle_events": [],
        "audio_events": [
            {"type": "voice_normalize"},
            {"type": "music_bed", "asset_ref": "m1", "gain_db": -20},
            {"type": "music_duck"},
        ],
        "assets": [{"id": "m1", "kind": "music", "path": music}],
        "render_settings": {"width": 1080, "height": 1920, "fps": 30, "crf": 28,
                            "preset": "ultrafast", "target_lufs": -14, "master_4k": False},
    }
    plan_path = str(tmp_path / "unit_music.json")
    common.save_json(plan_path, plan)
    out = render_short.render(plan_path)

    info = common.ffprobe_json(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    dv = float(v.get("duration") or info["format"]["duration"])
    da = float(a.get("duration") or info["format"]["duration"])
    assert abs(dv - da) < 0.2, f"звук {da:.2f}с против картинки {dv:.2f}с"


def test_no_frame_hitch_at_the_seam(synthetic_source, tmp_path):
    """Старая реализация роняла кадр ровно на стыке сегментов: интервал между
    кадрами в точке склейки был 0.0667с вместо 0.0333с. Это и читалось как
    «переход лагает». Здесь проверяется, что все интервалы одинаковые."""
    plan = {
        "project": {"clip_id": "unit_seam", "stage": "audio_planned"},
        "source": {"path": synthetic_source, "duration_sec": 30.0, "fps": 30.0,
                   "width": 1920, "height": 1080, "orientation": "landscape"},
        "clip": {"source_start_sec": 2.0, "source_end_sec": 12.0},
        "speech_edits": [
            {"id": "seg01", "source_start_sec": 2.0, "source_end_sec": 6.0, "role": "hook"},
            {"id": "seg02", "source_start_sec": 8.0, "source_end_sec": 12.0, "role": "payoff"},
            {"id": "seg03", "source_start_sec": 20.0, "source_end_sec": 23.0, "role": "payoff"},
        ],
        "visual_events": [], "subtitle_events": [],
        "audio_events": [{"type": "voice_normalize"}], "assets": [],
        "render_settings": {"width": 1080, "height": 1920, "fps": 30, "crf": 28,
                            "preset": "ultrafast", "target_lufs": -14, "master_4k": False},
    }
    plan_path = str(tmp_path / "unit_seam.json")
    common.save_json(plan_path, plan)
    out = render_short.render(plan_path)

    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=pts_time", "-of", "csv=p=0", out],
        capture_output=True, check=True)
    ts = sorted(float(x.strip().rstrip(","))
                for x in r.stdout.decode().splitlines() if x.strip().rstrip(","))
    deltas = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
    hitches = [(round(ts[i], 3), round(deltas[i], 4))
               for i in range(len(deltas)) if abs(deltas[i] - 1 / 30) > 0.005]
    assert not hitches, f"рывок на стыке: {hitches}"


@pytest.fixture
def long_synthetic_source(tmp_path):
    """Двухминутный исходник: куски речи разнесены так же, как в настоящем лонге."""
    src = str(tmp_path / "long_src.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=120",
        "-f", "lavfi", "-i", "sine=f=440:d=120",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", src,
    ], check=True)
    return src


def test_long_form_render_is_horizontal_and_reads_only_the_windows_it_needs(
        long_synthetic_source, tmp_path):
    """Лонг собирается тем же ядром: несколько рабочих окон, один граф, 16:9.

    Куски речи разнесены на минуту — они обязаны попасть в разные рабочие окна,
    иначе ради 13 секунд монтажа перекодируется весь исходник.
    """
    plan = {
        "project": {"clip_id": "unit_long", "stage": "audio_planned"},
        "source": {"path": long_synthetic_source, "duration_sec": 120.0, "fps": 30.0,
                   "width": 1920, "height": 1080, "orientation": "landscape"},
        "clip": {"source_start_sec": 5.0, "source_end_sec": 78.0},
        "speech_edits": [
            {"id": "seg01", "source_start_sec": 5.0, "source_end_sec": 10.0, "role": "hook"},
            {"id": "seg02", "source_start_sec": 70.0, "source_end_sec": 78.0, "role": "payoff"},
        ],
        # crop_x посчитан под вертикальный кроп: в горизонтали он обязан быть зажат,
        # иначе окно кропа уезжает за границу кадра и ffmpeg падает целиком
        "visual_events": [
            {"type": "punch_in", "segment_ref": "seg02", "start_sec": 5.0, "end_sec": 13.0,
             "crop": {"crop_x": 900, "zoom": 1.15, "ease_ms": 400}},
        ],
        "subtitle_events": [
            {"style": "caption", "text": "Мужчина решает сам", "start_sec": 0.5,
             "end_sec": 3.5, "emphasized_words": ["сам"]},
        ],
        "audio_events": [{"type": "voice_normalize"}],
        "assets": [],
        "render_settings": {"width": 1920, "height": 1080, "fps": 30, "crf": 28,
                            "preset": "ultrafast", "target_lufs": -14, "master_4k": False},
    }
    plan_path = str(tmp_path / "unit_long.json")
    common.save_json(plan_path, plan)

    windows = __import__("workfile").window_clusters(plan["speech_edits"])
    assert len(windows) == 2, "куски в минуте друг от друга должны дать разные окна"

    out = render_short.render(plan_path)

    info = common.ffprobe_json(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1920, 1080)
    assert abs(float(info["format"]["duration"]) - 13.0) < 0.5
