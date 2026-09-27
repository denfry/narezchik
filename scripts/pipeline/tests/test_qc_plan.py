"""Тишина на обложке и финальной карточке законна, в середине ролика — дефект."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qc_video  # noqa: E402

PLAN = {"render_settings": {"tail_card": {"enabled": True, "duration_sec": 2.2},
                            "cover_frame": {"enabled": False}}}


def test_tail_card_silence_is_allowed():
    ranges = qc_video.still_ranges(PLAN, 28.8)
    assert ranges == [(28.8 - 2.2, 28.8)]
    assert qc_video.outside([{"start": 26.61, "end": 28.8}], ranges) == []


def test_silence_in_the_middle_is_a_defect():
    ranges = qc_video.still_ranges(PLAN, 28.8)
    mid = [{"start": 10.0, "end": 12.0}]
    assert qc_video.outside(mid, ranges) == mid


def test_no_plan_means_nothing_is_allowed():
    assert qc_video.still_ranges(None, 30.0) == []
