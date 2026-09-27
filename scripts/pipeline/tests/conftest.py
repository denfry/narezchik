import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def speech_edits():
    return [
        {"id": "seg01", "source_start_sec": 100.0, "source_end_sec": 105.0, "role": "hook"},
        {"id": "seg02", "source_start_sec": 200.0, "source_end_sec": 210.0, "role": "payoff"},
    ]


@pytest.fixture
def visual_events():
    return [
        {"type": "talking_head", "segment_ref": "seg01", "start_sec": 0.0, "end_sec": 5.0,
         "crop": {"crop_x": 400, "zoom": 1.0}},
        {"type": "punch_in", "segment_ref": "seg02", "start_sec": 8.0, "end_sec": 15.0,
         "crop": {"crop_x": 500, "zoom": 1.25, "ease_ms": 300}},
    ]
