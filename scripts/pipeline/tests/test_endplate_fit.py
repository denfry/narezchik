"""Длинный заголовок плашки уменьшается под ширину, а не обрезается за краем."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import make_endplate  # noqa: E402

PIL = pytest.importorskip("PIL")


def _width(text, size):
    from PIL import Image, ImageDraw
    return ImageDraw.Draw(Image.new("RGBA", (1, 1))).textlength(text, font=make_endplate._font(size))


def test_short_title_keeps_full_size():
    geo = make_endplate.layout()
    assert make_endplate.fit_size("ПОДПИШИСЬ", make_endplate.TITLE_SIZE, geo["text_w"]) == make_endplate.TITLE_SIZE


def test_long_title_shrinks_to_fit_plate():
    geo = make_endplate.layout()
    size = make_endplate.fit_size("ВЫЙДИ ИЗ МАТРИЦЫ", make_endplate.TITLE_SIZE, geo["text_w"])
    assert size < make_endplate.TITLE_SIZE
    assert _width("ВЫЙДИ ИЗ МАТРИЦЫ", size) <= geo["text_w"]
