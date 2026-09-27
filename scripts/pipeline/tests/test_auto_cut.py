"""auto_cut: паузы и междометия вырезаются, границы на сетке кадров, слова не теряются."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auto_cut  # noqa: E402


def _w(word, s, e):
    return {"word": word, "start": s, "end": e}


def test_long_pause_splits_and_filler_is_dropped():
    words = [_w("Деньги", 10.0, 10.4), _w("любят", 10.45, 10.8), _w("э", 10.9, 11.3),
             _w("тишину.", 12.5, 13.0)]
    segs = auto_cut.cut(words, max_pause=0.6, fps=25)
    assert len(segs) == 2
    assert [w["word"] for w in segs[0][2]] == ["Деньги", "любят"]
    assert [w["word"] for w in segs[1][2]] == ["тишину."]


def test_boundaries_on_frame_grid_and_padded():
    segs = auto_cut.cut([_w("раз", 1.0, 1.3), _w("два", 5.0, 5.4)], max_pause=0.6, fps=25)
    for s, e, _ in segs:
        assert abs(s * 25 - round(s * 25)) < 1e-6 and abs(e * 25 - round(e * 25)) < 1e-6
    assert segs[0][0] < 1.0 and segs[0][1] > 1.3


def test_tiny_fragment_glued_to_previous():
    words = [_w("длинная", 0.0, 0.5), _w("фраза", 0.55, 1.0), _w("да", 2.0, 2.1)]
    segs = auto_cut.cut(words, max_pause=0.6, fps=30)
    assert len(segs) == 1
    assert [w["word"] for w in segs[0][2]] == ["длинная", "фраза", "да"]
