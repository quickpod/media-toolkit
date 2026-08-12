"""Unit tests for the pure ffmpeg argument builders.

None of these run FFmpeg -- they only assert on the argv lists produced by the
``build_*`` helpers, so the whole module passes on a box with no FFmpeg present.
"""

import pytest

from mediakit import (
    MediaKitError,
    build_compress_args,
    build_convert_args,
    build_extract_audio_args,
    build_gif_args,
    build_subtitle_burn_args,
    build_trim_args,
)
from mediakit.edit import COMPRESS_LEVELS, build_thumbnail_args
from mediakit import ffmpeg as ff


def _pairs(args):
    """Return a dict of ``flag -> following value`` for quick assertions."""
    return {args[i]: args[i + 1] for i in range(len(args) - 1)}


# --- convert ---------------------------------------------------------------
def test_convert_minimal_input_before_output():
    args = build_convert_args("in.mkv", "out.mp4")
    assert args[0] == "-i" and args[1] == "in.mkv"
    assert args[-1] == "out.mp4"
    # input must precede output
    assert args.index("in.mkv") < args.index("out.mp4")


def test_convert_all_options_present_and_ordered():
    args = build_convert_args("in.mkv", "out.mp4", vcodec="libx264",
                              acodec="aac", crf=23, preset="slow",
                              extra=["-movflags", "+faststart"])
    p = _pairs(args)
    assert p["-c:v"] == "libx264"
    assert p["-c:a"] == "aac"
    assert p["-crf"] == "23"
    assert p["-preset"] == "slow"
    assert "-movflags" in args and "+faststart" in args
    # codecs/options come after -i and before the output
    assert args.index("-i") < args.index("-c:v") < args.index("out.mp4")
    assert args[-1] == "out.mp4"


def test_convert_omits_unset_options():
    args = build_convert_args("in.mkv", "out.webm")
    assert "-c:v" not in args
    assert "-crf" not in args
    assert "-preset" not in args


def test_convert_requires_paths():
    with pytest.raises(MediaKitError):
        build_convert_args("", "out.mp4")
    with pytest.raises(MediaKitError):
        build_convert_args("in.mkv", "")


# --- trim ------------------------------------------------------------------
def test_trim_streamcopy_is_default():
    args = build_trim_args("in.mp4", "out.mp4", 5, 10, reencode=False)
    assert "-c" in args and _pairs(args)["-c"] == "copy"
    assert "-c:v" not in args  # no encoder chosen when copying
    p = _pairs(args)
    assert p["-ss"] == "5"
    assert p["-to"] == "10"
    # -ss precedes -i (fast seek); -i precedes -to
    assert args.index("-ss") < args.index("-i") < args.index("-to")
    assert args[-1] == "out.mp4"


def test_trim_reencode_selects_encoders_not_copy():
    args = build_trim_args("in.mp4", "out.mp4", 0, 3, reencode=True)
    p = _pairs(args)
    assert p["-c:v"] == "libx264"
    assert p["-c:a"] == "aac"
    assert "copy" not in args


def test_trim_accepts_timestamp_strings():
    args = build_trim_args("in.mp4", "out.mp4", "00:00:05", "00:01:30")
    p = _pairs(args)
    assert p["-ss"] == "00:00:05"
    assert p["-to"] == "00:01:30"


def test_trim_start_only_or_end_only():
    a = build_trim_args("in.mp4", "out.mp4", 5, None)
    assert "-ss" in a and "-to" not in a
    b = build_trim_args("in.mp4", "out.mp4", None, 20)
    assert "-to" in b and "-ss" not in b


def test_trim_requires_at_least_one_bound():
    with pytest.raises(MediaKitError):
        build_trim_args("in.mp4", "out.mp4", None, None)


# --- compress --------------------------------------------------------------
@pytest.mark.parametrize("level", sorted(COMPRESS_LEVELS))
def test_compress_crf_matches_level_table(level):
    args = build_compress_args("in.mp4", "out.mp4", level=level)
    p = _pairs(args)
    assert p["-crf"] == str(COMPRESS_LEVELS[level]["crf"])
    assert p["-preset"] == COMPRESS_LEVELS[level]["preset"]
    assert p["-b:a"] == COMPRESS_LEVELS[level]["audio_bitrate"]
    assert p["-c:v"] == "libx264"
    assert args[-1] == "out.mp4"


def test_compress_crf_increases_with_level():
    low = int(_pairs(build_compress_args("i", "o", "low"))["-crf"])
    med = int(_pairs(build_compress_args("i", "o", "medium"))["-crf"])
    high = int(_pairs(build_compress_args("i", "o", "high"))["-crf"])
    # higher compression == higher CRF == smaller/lower-quality
    assert low < med < high


def test_compress_rejects_unknown_level():
    with pytest.raises(MediaKitError):
        build_compress_args("in.mp4", "out.mp4", level="ultra")


# --- extract audio ---------------------------------------------------------
def test_extract_audio_drops_video():
    args = build_extract_audio_args("in.mp4", "out.mp3")
    assert "-vn" in args
    assert args[-1] == "out.mp3"


def test_extract_audio_infers_codec_from_extension():
    assert _pairs(build_extract_audio_args("i.mp4", "o.mp3"))["-c:a"] == "libmp3lame"
    assert _pairs(build_extract_audio_args("i.mp4", "o.m4a"))["-c:a"] == "aac"
    assert _pairs(build_extract_audio_args("i.mp4", "o.flac"))["-c:a"] == "flac"
    assert _pairs(build_extract_audio_args("i.mp4", "o.wav"))["-c:a"] == "pcm_s16le"


def test_extract_audio_explicit_codec_wins():
    args = build_extract_audio_args("in.mp4", "out.mp3", acodec="copy")
    assert _pairs(args)["-c:a"] == "copy"


# --- gif -------------------------------------------------------------------
def test_gif_filtergraph_has_fps_and_scale():
    args = build_gif_args("in.mp4", "out.gif", fps=15, width=320)
    vf = _pairs(args)["-vf"]
    assert "fps=15" in vf
    assert "scale=320:-1" in vf
    assert args[-1] == "out.gif"
    assert "-loop" in args


def test_gif_start_and_duration_flags():
    args = build_gif_args("in.mp4", "out.gif", start=2, duration=4)
    p = _pairs(args)
    assert p["-ss"] == "2"
    assert p["-t"] == "4"
    # -ss must precede -i for a fast seek
    assert args.index("-ss") < args.index("-i")


def test_gif_rejects_bad_numbers():
    with pytest.raises(MediaKitError):
        build_gif_args("in.mp4", "out.gif", fps=0)
    with pytest.raises(MediaKitError):
        build_gif_args("in.mp4", "out.gif", width=-10)


# --- subtitle burn ---------------------------------------------------------
def test_subtitle_burn_filter_and_audio_copy():
    args = build_subtitle_burn_args("in.mp4", "out.mp4", "subs.srt")
    vf = _pairs(args)["-vf"]
    assert vf.startswith("subtitles=")
    assert "subs.srt" in vf
    assert _pairs(args)["-c:a"] == "copy"
    assert args[-1] == "out.mp4"


def test_subtitle_burn_escapes_windows_path():
    args = build_subtitle_burn_args("in.mp4", "out.mp4", r"C:\subs\a.srt")
    vf = _pairs(args)["-vf"]
    # the drive colon and backslashes must be escaped for the filtergraph parser
    assert "C\\:" in vf


def test_subtitle_burn_requires_subs():
    with pytest.raises(MediaKitError):
        build_subtitle_burn_args("in.mp4", "out.mp4", "")


# --- thumbnail -------------------------------------------------------------
def test_thumbnail_single_frame():
    args = build_thumbnail_args("in.mp4", "frame.png", at=3, width=240)
    p = _pairs(args)
    assert p["-frames:v"] == "1"
    assert p["-ss"] == "3"
    assert "scale=240:-1" in p["-vf"]
    assert args[-1] == "frame.png"


# --- progress parsing (pure) ----------------------------------------------
def test_parse_time_from_progress_ms():
    assert ff.parse_time_seconds("out_time_ms=1500000") == pytest.approx(1.5)


def test_parse_time_from_progress_timestamp():
    assert ff.parse_time_seconds("out_time=00:00:02.500000") == pytest.approx(2.5)


def test_parse_time_from_stderr_line():
    line = "frame=  30 fps=0.0 q=-1.0 size=0kB time=00:00:01.00 bitrate=..."
    assert ff.parse_time_seconds(line) == pytest.approx(1.0)


def test_parse_time_none_when_absent():
    assert ff.parse_time_seconds("frame=1 fps=0") is None


def test_progress_fraction_clamped():
    assert ff.progress_fraction(5, 10) == pytest.approx(0.5)
    assert ff.progress_fraction(20, 10) == 1.0
    assert ff.progress_fraction(0, 10) == 0.0
    assert ff.progress_fraction(5, None) is None
    assert ff.progress_fraction(5, 0) is None
