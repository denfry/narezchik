"""Музыка «на швах»: огибающая по нескольким окнам (audiochain.music_windows_gate)."""
import array
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import audiochain  # noqa: E402
import render_short  # noqa: E402


def test_no_windows_means_no_gate():
    assert audiochain.music_windows_gate(None) == ""
    assert audiochain.music_windows_gate([]) == ""


def test_every_window_becomes_its_own_trapezoid():
    gate = audiochain.music_windows_gate([(10, 20), (100, 112)])
    assert gate.startswith(",asetnsamples=n=256,volume='")
    assert gate.endswith("':eval=frame")
    assert "clip((t-10.000)/0.8,0,1)*clip((20.000-t)/1.2,0,1)" in gate
    assert "clip((t-100.000)/0.8,0,1)*clip((112.000-t)/1.2,0,1)" in gate


def test_level_scales_only_its_window():
    gate = audiochain.music_windows_gate([(0, 5), (30, 60, 0.5)])
    assert "0.5*clip((t-30.000)" in gate
    assert "*clip((t-0.000)" not in gate


def test_short_window_fades_do_not_overlap():
    # окно в 1 с: подъём и спад по 0.5 с, а не 0.8 + 1.2
    gate = audiochain.music_windows_gate([(4, 5)])
    assert "clip((t-4.000)/0.5,0,1)*clip((5.000-t)/0.5,0,1)" in gate


def test_windows_take_priority_over_start_end():
    mix = audiochain.music_mix(-24, True, -14, music_index=3, music_start=2.0,
                               music_end=9.0, music_windows=[(1, 4)])
    assert "[3:a]volume=-24dB,asetnsamples" in mix
    assert "afade" not in mix


def test_plan_windows_are_sorted_and_same_level_overlaps_merged():
    events = [{"type": "voice_normalize"},
              {"type": "music_bed", "windows": [[50, 60], [10, 20], [15, 30], [70, 70]]}]
    assert render_short._music_windows(events) == [(10.0, 30.0, 1.0), (50.0, 60.0, 1.0)]
    assert render_short._music_windows([{"type": "music_bed"}]) is None


def test_overlapping_windows_of_different_level_stay_separate_for_crossfade():
    events = [{"type": "music_bed", "windows": [[730, 758.5], [757.7, 769, 5.0]]}]
    assert render_short._music_windows(events) == [(730.0, 758.5, 1.0), (757.7, 769.0, 5.0)]
    gate = audiochain.music_windows_gate(render_short._music_windows(events))
    assert "5*clip((t-757.700)" in gate


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")
def test_ffmpeg_silences_music_outside_windows():
    gate = audiochain.music_windows_gate([(1.0, 2.0), (3.0, 4.0, 0.5)],
                                         fade_in=0.1, fade_out=0.1)
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:sample_rate=8000:d=5",
           "-af", gate.lstrip(","), "-f", "s16le", "-ac", "1", "-ar", "8000", "-"]
    pcm = array.array("h", subprocess.run(cmd, capture_output=True, check=True).stdout)

    def peak(a, b):
        return max(abs(x) for x in pcm[int(a * 8000):int(b * 8000)])

    full = peak(1.3, 1.7)
    assert full > 2000
    assert peak(0.0, 0.9) == 0                       # до первого окна — тишина
    assert peak(2.2, 2.8) == 0                       # между окнами — тишина
    assert 0.4 * full < peak(3.3, 3.7) < 0.6 * full  # второе окно на половине громкости
    assert peak(4.2, 5.0) == 0
