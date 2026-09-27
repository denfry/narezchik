import io
import subprocess

import qc_video


def _write_ass(tmp_path, margin_v):
    p = tmp_path / "t.ass"
    io.open(p, "w", encoding="utf-8").write(
        "[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Dialogue,Montserrat ExtraBold,96,&H00FFFFFF,-1,2,60,60,{margin_v},1\n"
    )
    return str(p)


def test_safe_area_fails_when_text_sits_under_the_shorts_interface(tmp_path):
    res = qc_video.check_subtitle_safe_area(_write_ass(tmp_path, 200))
    assert res["ok"] is False
    assert "320" in res["message"]


def test_safe_area_passes_with_enough_bottom_margin(tmp_path):
    assert qc_video.check_subtitle_safe_area(_write_ass(tmp_path, 420))["ok"] is True


def test_top_aligned_style_is_not_judged_by_the_bottom_rule(tmp_path):
    """У стиля, прижатого к верху, MarginV — отступ сверху.

    Заголовок с отступом 180 px от верхнего края к зоне интерфейса Shorts
    отношения не имеет, и ронять из-за него весь QC нельзя.
    """
    p = tmp_path / "top.ass"
    io.open(p, "w", encoding="utf-8").write(
        "[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Bottom,Montserrat ExtraBold,96,&H00FFFFFF,-1,2,60,60,420,1\n"
        "Style: Top,Montserrat ExtraBold,116,&H00FFFFFF,-1,8,30,30,180,1\n"
    )
    assert qc_video.check_subtitle_safe_area(str(p))["ok"] is True


def test_safe_area_takes_the_worst_style_not_the_first(tmp_path):
    """Проверять надо самый рискованный стиль: достаточно одного плохого,
    чтобы часть текста ушла под кнопки."""
    p = tmp_path / "many.ass"
    io.open(p, "w", encoding="utf-8").write(
        "[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: A,Montserrat ExtraBold,96,&H00FFFFFF,-1,2,60,60,420,1\n"
        "Style: B,Montserrat ExtraBold,96,&H00FFFFFF,-1,2,60,60,100,1\n"
    )
    assert qc_video.check_subtitle_safe_area(str(p))["ok"] is False


def _make(tmp_path, name, vdur, adur):
    p = str(tmp_path / name)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:duration={vdur}",
        "-f", "lavfi", "-i", f"sine=f=440:d={adur}",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", p,
    ], check=True)
    return p


def test_av_drift_passes_on_a_correctly_muxed_file(tmp_path):
    res = qc_video.check_av_drift(_make(tmp_path, "sync.mp4", 5, 5))
    assert res["ok"] is True
    assert res["drift_sec"] < 0.1


def test_av_drift_flags_a_file_whose_audio_is_shorter_than_video(tmp_path):
    res = qc_video.check_av_drift(_make(tmp_path, "drift.mp4", 5, 2))
    assert res["ok"] is False
    assert res["drift_sec"] > 2.0


def test_seam_check_reports_black_and_silence_counts(tmp_path):
    res = qc_video.check_seam_artifacts(_make(tmp_path, "clean.mp4", 3, 3))
    assert res["ok"] is True
    assert res["black_frames"] == []
