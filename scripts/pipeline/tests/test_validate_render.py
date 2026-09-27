"""Валидатор не пропускает чужую частоту кадров."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import validate_edit_plan as v  # noqa: E402


def _plan(out_fps, **rs):
    return {"source": {"fps": 25.0, "duration_sec": 100}, "clip": {}, "speech_edits": [],
            "render_settings": {"width": 1080, "height": 1920, "fps": out_fps, **rs}}


def test_fps_mismatch_is_an_error():
    errors, _ = v.sanity_errors(_plan(30))
    assert any("fps" in e for e in errors)


def test_same_fps_and_explicit_override_pass():
    assert not any("fps" in e for e in v.sanity_errors(_plan(25))[0])
    assert not any("fps" in e for e in v.sanity_errors(_plan(30, allow_fps_change=True))[0])
