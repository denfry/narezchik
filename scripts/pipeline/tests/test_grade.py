import os

import pytest

import render_short


def test_no_grade_settings_means_empty_tail():
    parts, out = render_short.grade_graph("vout", {}, "/nowhere")
    assert parts == [] and out == "vout"


def test_lut_is_blended_with_original_not_applied_raw(tmp_path):
    lut = tmp_path / "x.cube"
    lut.write_text("LUT_3D_SIZE 2\n" + "0 0 0\n" * 8)
    parts, out = render_short.grade_graph(
        "vout", {"lut": "x", "lut_strength": 0.45, "film_grain": 0.3}, str(tmp_path))
    g = ";".join(parts)
    assert "split[lutA0][lutB0]" in g
    assert "lut3d=file=" in g and "interp=tetrahedral" in g
    assert "blend=all_mode=normal:all_opacity=0.45" in g
    # зерно только по яркости и после LUT
    assert g.index("noise=c0s=4:c0f=t+u") > g.index("blend=")
    assert out == "g1"


def test_missing_lut_is_an_error_not_a_silent_skip(tmp_path):
    with pytest.raises(RuntimeError):
        render_short.grade_graph("vout", {"lut": "nope"}, str(tmp_path))


def test_legacy_preset_still_works_and_precedes_lut(tmp_path):
    lut = tmp_path / "x.cube"
    lut.write_text("LUT_3D_SIZE 2\n" + "0 0 0\n" * 8)
    parts, _ = render_short.grade_graph(
        "vout", {"color_grade": "cinematic_warm", "lut": "x"}, str(tmp_path))
    assert parts[0].startswith("[vout]eq=")
    assert "lut3d" in parts[2]
