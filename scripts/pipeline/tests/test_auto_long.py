"""auto_long: главы режутся по порядку, крупность меняется на склейках без дёрганья,
новая глава открывается общим планом, подложка только на швах, вид ролика свой."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auto_cut  # noqa: E402
import auto_long  # noqa: E402


def _w(word, s, e):
    return {"word": word, "start": s, "end": e}


def _seg(i, s, e, ch, role="development"):
    return {"id": f"seg{i:03d}", "source_start_sec": s, "source_end_sec": e,
            "chapter": ch, "role": role}


def test_cut_chapters_keeps_order_and_marks_chapters():
    tr = {"segments": [{"words": [_w("поздно", 500.0, 500.5), _w("всё", 500.55, 500.9),
                                  _w("начало", 10.0, 10.5), _w("было", 10.55, 11.0)]}]}
    chapters = [{"role": "hook", "source_start_sec": 499.9, "source_end_sec": 501.0},
                {"title": "Начало", "source_start_sec": 9.9, "source_end_sec": 11.1}]
    edits = auto_cut.cut_chapters(tr, chapters, max_pause=0.9, fps=25)
    assert [e["chapter"] for e in edits] == [0, 1]
    assert edits[0]["role"] == "hook" and edits[0]["source_start_sec"] > 499
    assert edits[1]["role"] == "chapter"
    assert len({e["id"] for e in edits}) == 2


def test_empty_chapter_is_an_error():
    tr = {"segments": [{"words": [_w("раз", 1.0, 1.5)]}]}
    try:
        auto_cut.cut_chapters(tr, [{"source_start_sec": 50, "source_end_sec": 60}], 0.9, 25)
    except ValueError:
        return
    raise AssertionError("пустая глава должна падать")


def test_crops_alternate_but_hold_and_reset_on_chapter():
    # сегменты по 4 с: в главе 0 — 40 с, потом глава 1
    segs = [_seg(i, i * 10.0, i * 10.0 + 4.0, 0) for i in range(10)]
    segs += [_seg(10 + i, 200 + i * 10.0, 204 + i * 10.0, 1) for i in range(3)]
    ev = auto_long.plan_crops(auto_long.timeline(segs), fps=25)
    assert ev[0]["type"] == "talking_head" and ev[0]["start_sec"] == 0
    # сброс на новой главе выдержку не ждёт — шов закрывает карточка
    changes = [e["start_sec"] for e in ev if e["start_sec"] < 40]
    gaps = [b - a for a, b in zip(changes, changes[1:])]
    assert all(g >= auto_long.HOLD_EARLY_SEC - 1e-6 for g in gaps)
    ch1 = next(e for e in ev if e["segment_ref"] == "seg010")
    assert ch1["crop"]["zoom"] == 1.0 and ch1["start_sec"] == 40.0
    zooms = {e["crop"]["zoom"] for e in ev}
    assert zooms <= {1.0, auto_long.ZOOM}


def test_long_static_segment_gets_slow_push_and_back():
    ev = auto_long.plan_crops(auto_long.timeline([_seg(1, 0.0, 60.0, 0)]), fps=25)
    drifts = [e for e in ev if e["crop"]["ease_ms"] == auto_long.DRIFT_EASE_MS]
    assert len(drifts) >= 3
    assert max(e["crop"]["zoom"] for e in ev) <= 1.0 + auto_long.DRIFT + 1e-6
    for e in ev:
        assert abs(e["start_sec"] * 25 - round(e["start_sec"] * 25)) < 1e-6


def test_music_only_on_hook_seams_and_outro():
    segs = [_seg(1, 500, 520, 0, "hook"), _seg(2, 10, 110, 1, "chapter"),
            _seg(3, 300, 400, 2, "chapter")]
    chapters = [{"role": "hook"}, {"title": "a"}, {"title": "b"}]
    wins = auto_long.music_windows(auto_long.timeline(segs), chapters)
    assert wins[0] == [0.0, 20.0]
    assert [117.0, 125.0] in wins          # шов главы 2 на 120 с
    assert wins[-1] == [208.0, 220.0]
    assert sum(e - s for s, e in wins) < 60


def test_look_is_stable_per_clip_and_skips_recent_luts():
    luts = ["a", "b", "c"]
    one = auto_long.pick_look("pod_long01", luts, recent=["a", "b"])
    assert one["lut"] == "c"
    assert one == auto_long.pick_look("pod_long01", luts, recent=["a", "b"])
    assert 0.5 <= one["lut_strength"] <= 0.7 and 0.2 <= one["film_grain"] <= 0.3
    assert auto_long.pick_look("x", luts, recent=luts)["lut"] in luts
