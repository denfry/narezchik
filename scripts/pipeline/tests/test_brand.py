"""Бренд канала берётся из channel/brand.json, пустые поля — из значений по умолчанию."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brand  # noqa: E402


def test_missing_file_gives_defaults(tmp_path):
    b = brand.load(str(tmp_path / "nope.json"))
    assert b["accent_color"] == brand.DEFAULTS["accent_color"]
    assert b["monogram"] == ""


def test_user_values_override_defaults_and_comments_are_ignored(tmp_path):
    p = tmp_path / "brand.json"
    p.write_text(json.dumps({"_comment": "x", "monogram": "AB", "accent_color": "#00FF00",
                             "cta_title": None}), encoding="utf-8")
    b = brand.load(str(p))
    assert b["monogram"] == "AB"
    assert b["accent_color"] == "#00FF00"
    assert b["cta_title"] == brand.DEFAULTS["cta_title"]  # null не затирает дефолт
    assert "_comment" not in b


def test_rgb_and_ass_colour():
    assert brand.rgb("#E11111") == (225, 17, 17)
    assert brand.rgb("#0f0") == (0, 255, 0)
    # ASS хранит цвет как AABBGGRR
    assert brand.ass_colour("#E11111") == "&H001111E1"
    assert brand.ass_colour("#123456", alpha="80") == "&H80563412"
