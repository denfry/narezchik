"""Плашка-тезис (стиль Hook) встаёт с той стороны кадра, где фон: align -> тег \\an."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import generate_subtitles as gs  # noqa: E402


def test_hook_align_top_right_adds_an9_after_fade():
    out = gs.build_events([{"start_sec": 1.0, "end_sec": 3.0, "style": "hook_headline",
                            "text": "ЕСЛИ ТЫ МИЛЛИОН —\\NОН ПОЛТОРА", "align": "top_right"}], 42)
    assert len(out) == 1
    assert ",Hook,,0,0,0,,{\\fad(150,250)\\an9}ЕСЛИ ТЫ МИЛЛИОН —\\NОН ПОЛТОРА" in out[0]


def test_hook_without_align_keeps_style_position():
    out = gs.build_events([{"start_sec": 1.0, "end_sec": 3.0, "style": "hook_headline",
                            "text": "ЗАЧЕМ МНЕ НИЩИЙ?"}], 42)
    assert "{\\fad(150,250)}ЗАЧЕМ МНЕ НИЩИЙ?" in out[0]


def test_caption_ignores_align():
    out = gs.build_events([{"start_sec": 1.0, "end_sec": 3.0, "style": "caption",
                            "text": "Вот именно.", "align": "top_right"}], 42)
    assert out[0].endswith(",0,0,0,,Вот именно.")
    assert "\\an" not in out[0]
