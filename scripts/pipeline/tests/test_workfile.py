import os

import pytest

import workfile


def test_window_bounds_covers_all_segments_with_padding():
    edits = [
        {"source_start_sec": 100.0, "source_end_sec": 105.0},
        {"source_start_sec": 200.0, "source_end_sec": 210.0},
    ]
    start, end = workfile.window_bounds(edits, pad_sec=2.0)
    assert start == 98.0
    assert end == 212.0


def test_window_bounds_never_goes_negative():
    edits = [{"source_start_sec": 0.5, "source_end_sec": 4.0}]
    start, end = workfile.window_bounds(edits, pad_sec=2.0)
    assert start == 0.0


def test_window_bounds_rejects_empty_input():
    with pytest.raises(ValueError):
        workfile.window_bounds([])


def test_close_segments_land_in_one_window():
    """Для Shorts материал берётся из одного места — окно должно остаться одним,
    иначе рендер получит лишние входы там, где хватает одного."""
    edits = [
        {"source_start_sec": 100.0, "source_end_sec": 105.0},
        {"source_start_sec": 110.0, "source_end_sec": 120.0},
    ]
    assert workfile.window_clusters(edits, pad_sec=2.0, max_gap_sec=20.0) == [(98.0, 122.0)]


def test_far_apart_segments_get_separate_windows():
    """У лонга хук берётся с 07:51, бэкстори с 01:05, практика с 18:38.

    Одно общее окно означало бы перекод почти всего 27-минутного 4K-исходника
    в crf 14 ради пяти минут полезного материала.
    """
    edits = [
        {"source_start_sec": 65.0, "source_end_sec": 148.0},
        {"source_start_sec": 471.0, "source_end_sec": 497.0},
        {"source_start_sec": 1118.0, "source_end_sec": 1160.0},
    ]
    windows = workfile.window_clusters(edits, pad_sec=2.0, max_gap_sec=20.0)
    assert windows == [(63.0, 150.0), (469.0, 499.0), (1116.0, 1162.0)]
    # суммарно вырезается меньше трёх минут вместо девятнадцати
    assert sum(e - s for s, e in windows) < 180


def test_windows_are_built_from_sorted_spans_even_if_plan_reorders_speech():
    """speech_edits идут в порядке монтажа, а он не обязан совпадать с порядком
    в исходнике: бэкстори может стоять после хука, взятого позже по таймлайну."""
    edits = [
        {"source_start_sec": 471.0, "source_end_sec": 497.0},
        {"source_start_sec": 65.0, "source_end_sec": 148.0},
    ]
    windows = workfile.window_clusters(edits, pad_sec=2.0, max_gap_sec=20.0)
    assert windows == [(63.0, 150.0), (469.0, 499.0)]


def test_failed_extract_leaves_no_window_to_reuse(tmp_path, monkeypatch):
    """extract переиспользует существующее окно, поэтому оборванный ffmpeg не должен
    оставлять файл под его именем — иначе битое окно ломает все следующие прогоны."""
    monkeypatch.setattr(workfile, "TEMP_DIR", str(tmp_path))

    def killed(cmd, **kw):
        with open(cmd[-1], "wb") as f:
            f.write(b"half")
        raise RuntimeError("command failed (-9)")

    monkeypatch.setattr(workfile, "run", killed)
    with pytest.raises(RuntimeError):
        workfile.extract("src.mp4", "c", 0.0, 1.0)
    assert list(tmp_path.iterdir()) == []


def test_window_appears_only_after_successful_extract(tmp_path, monkeypatch):
    monkeypatch.setattr(workfile, "TEMP_DIR", str(tmp_path))

    def ok(cmd, **kw):
        with open(cmd[-1], "wb") as f:
            f.write(b"ok")

    monkeypatch.setattr(workfile, "run", ok)
    out = workfile.extract("src.mp4", "c", 0.0, 1.0, part=1)
    assert out == os.path.join(str(tmp_path), "c_work01.mp4")
    assert [p.name for p in tmp_path.iterdir()] == ["c_work01.mp4"]
