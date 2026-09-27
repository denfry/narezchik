import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import make_endplate


def test_text_starts_after_the_monogram():
    """Текст не должен наезжать на монограмму — иначе плашка читается как каша."""
    geo = make_endplate.layout()
    mx, _, msize = geo["monogram"]
    assert geo["text_x"] >= mx + msize


def test_text_column_is_the_larger_half():
    """Название проекта — главное на плашке, монограмма лишь метка канала."""
    geo = make_endplate.layout()
    assert geo["text_w"] > make_endplate.PLATE_W / 2


def test_rows_do_not_overlap():
    geo = make_endplate.layout()
    assert geo["sub_y"] - geo["title_y"] >= (make_endplate.TITLE_SIZE
                                             + make_endplate.SUB_SIZE) / 2
