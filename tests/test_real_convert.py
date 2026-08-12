"""Optional end-to-end test: only runs when a real FFmpeg is installed.

Generates a 1-second synthetic clip with ffmpeg's ``lavfi`` test sources, then
exercises probe + convert + extract-audio + gif on it.  Skips cleanly when
FFmpeg is not present so the suite stays green on a bare CI box.
"""

import os
import subprocess

import pytest

from mediakit import (
    convert,
    gif_run,
    extract_audio_run,
    probe,
)
from mediakit import ffmpeg as ff

pytestmark = pytest.mark.skipif(
    not ff.is_available(), reason="FFmpeg/ffprobe not installed on this box")


@pytest.fixture(scope="module")
def sample_clip(tmp_path_factory):
    """A tiny 1s 320x240 clip with a sine audio track."""
    d = tmp_path_factory.mktemp("clip")
    out = os.path.join(str(d), "sample.mp4")
    cmd = [
        ff.ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", out,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.isfile(out):
        pytest.skip(f"could not synthesize a test clip: {proc.stderr.strip()}")
    return out


def test_probe_reports_streams(sample_clip):
    info = probe(sample_clip)
    assert info["duration"] is not None and info["duration"] > 0
    assert info["resolution"] == "320x240"
    assert info["vcodec"] == "h264"
    assert info["acodec"] == "aac"


def test_convert_to_mkv(sample_clip, tmp_path):
    seen = []
    out = str(tmp_path / "out.mkv")
    convert(sample_clip, out, on_progress=lambda f: seen.append(f))
    assert os.path.isfile(out) and os.path.getsize(out) > 0
    assert seen and seen[-1] == pytest.approx(1.0)


def test_extract_audio(sample_clip, tmp_path):
    out = str(tmp_path / "out.mp3")
    extract_audio_run(sample_clip, out)
    assert os.path.isfile(out) and os.path.getsize(out) > 0
    assert probe(out)["acodec"] is not None


def test_gif(sample_clip, tmp_path):
    out = str(tmp_path / "out.gif")
    gif_run(sample_clip, out, fps=8, width=160)
    assert os.path.isfile(out) and os.path.getsize(out) > 0
