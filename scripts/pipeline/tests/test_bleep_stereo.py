"""Бип не сводит микс в моно и не оставляет цифровой тишины после окна."""
import array
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import audiochain  # noqa: E402


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")
def test_bleep_keeps_stereo_and_no_silent_frame_after_window():
    chain = audiochain.bleep_chain([(1.0, 1.5)], "voiceclean", "out")
    graph = "[0:a]aformat=channel_layouts=stereo,anull[voiceclean];" + chain
    wav = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:sample_rate=48000:d=3",
                          "-filter_complex", graph, "-map", "[out]", "-f", "wav", "-"],
                         capture_output=True, check=True).stdout
    assert int.from_bytes(wav[22:24], "little") == 2
    raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:sample_rate=48000:d=3",
                          "-filter_complex", graph, "-map", "[out]", "-ac", "1", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    pcm = array.array("h", raw)
    after = pcm[int(1.51 * 48000):int(1.53 * 48000)]  # 20 мс сразу после окна — уже голос
    assert max(abs(x) for x in after) > 1000
