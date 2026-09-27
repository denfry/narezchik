"""Плашка-тезис переносится по словам и не выходит за ширину кадра."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import generate_subtitles as gs  # noqa: E402

BREAK = r"\N"
FS = r"\fs"


def _lines(text):
    return text.split("}")[-1].split(BREAK)


def test_long_hook_wraps_within_vertical_frame():
    out = gs.fit_hook("ЕМУ НУЖНА СЛАБАЯ ЖЕНЩИНА?", landscape=False)
    lines = _lines(out)
    assert 1 < len(lines) <= gs.HOOK_MAX_LINES
    size = int(out.split(FS)[1].split("}")[0]) if FS in out else gs.HOOK_SIZE["vertical"][0]
    assert all(gs._text_width(ln, size) <= gs.HOOK_SIZE["vertical"][1] for ln in lines)


def test_short_hook_is_untouched_and_manual_breaks_are_kept():
    assert gs.fit_hook("КОРОТКО", landscape=True) == "КОРОТКО"
    manual = "РАЗ" + BREAK + "ДВА"
    assert gs.fit_hook(manual, landscape=False) == manual
