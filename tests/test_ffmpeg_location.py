"""Tests for binary location and the missing-binary error path.

The missing-ffmpeg tests point the lookup at an empty directory (via the
``MEDIAKIT_FFMPEG_DIR`` override) *and* clear ``PATH``/common dirs so they pass
regardless of whether FFmpeg is installed on the box.
"""

import os

import pytest

from mediakit import MediaKitError
from mediakit import ffmpeg as ff


@pytest.fixture
def no_ffmpeg(tmp_path, monkeypatch):
    """Force every lookup route to come up empty."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("MEDIAKIT_FFMPEG_DIR", str(empty))
    monkeypatch.setenv("PATH", str(empty))
    # neutralise the hard-coded common install dirs and the app dirs
    monkeypatch.setattr(ff, "_COMMON_DIRS", [str(empty)])
    monkeypatch.setattr(ff, "_app_dirs", lambda: [str(empty)])
    return empty


def test_find_binary_returns_none_when_absent(no_ffmpeg):
    assert ff.find_binary("ffmpeg") is None
    assert ff.find_binary("ffprobe") is None


def test_ffmpeg_path_raises_clean_error(no_ffmpeg):
    with pytest.raises(MediaKitError) as ei:
        ff.ffmpeg_path()
    msg = str(ei.value)
    assert "ffmpeg" in msg.lower()
    # the message should be helpful, mentioning PATH / installer
    assert "PATH" in msg or "path" in msg
    assert "install" in msg.lower()


def test_ffprobe_path_raises_clean_error(no_ffmpeg):
    with pytest.raises(MediaKitError):
        ff.ffprobe_path()


def test_is_available_false_when_absent(no_ffmpeg):
    assert ff.is_available() is False


def test_env_override_is_honoured(tmp_path, monkeypatch):
    """A binary in MEDIAKIT_FFMPEG_DIR is found even with an empty PATH."""
    d = tmp_path / "bin"
    d.mkdir()
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    fake = d / name
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("MEDIAKIT_FFMPEG_DIR", str(d))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(ff, "_COMMON_DIRS", [])
    monkeypatch.setattr(ff, "_app_dirs", lambda: [])
    assert ff.find_binary("ffmpeg") == str(fake)


def test_probe_missing_file_raises():
    with pytest.raises(MediaKitError):
        ff.probe("/no/such/file/at/all.mp4")
