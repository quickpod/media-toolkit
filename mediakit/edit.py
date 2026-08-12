"""Editing operations: trim, compress, extract-audio, gif, subtitle burn.

Every operation is split into a pure ``build_*_args`` function (returns the
ffmpeg argument list, no subprocess -- unit-testable) and a thin ``*_run``
wrapper that hands the list to :func:`mediakit.ffmpeg.run_ffmpeg`.
"""

from __future__ import annotations

import os

from .errors import MediaKitError
from . import ffmpeg

# --- compression presets ----------------------------------------------------
# CRF is the quality knob for x264/x265: lower = better/larger.  "high"
# compression == smaller file == a higher CRF.
COMPRESS_LEVELS = {
    "low": {"crf": 20, "preset": "slow", "audio_bitrate": "192k"},
    "medium": {"crf": 26, "preset": "medium", "audio_bitrate": "128k"},
    "high": {"crf": 32, "preset": "faster", "audio_bitrate": "96k"},
}

# Default audio codec per common audio extension for extract-audio.
_AUDIO_CODEC_BY_EXT = {
    ".mp3": "libmp3lame",
    ".m4a": "aac",
    ".aac": "aac",
    ".ogg": "libvorbis",
    ".opus": "libopus",
    ".flac": "flac",
    ".wav": "pcm_s16le",
}


def _format_timestamp(value):
    """Accept seconds (int/float) or an ``HH:MM:SS[.ms]`` string; return a str.

    ffmpeg accepts both a plain seconds count and a timestamp for ``-ss``/``-to``
    so this just validates/normalises without imposing a format.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value < 0:
            raise MediaKitError("Time cannot be negative.")
        return str(value)
    text = str(value).strip()
    if not text:
        return None
    return text


# ---------------------------------------------------------------------------
# Trim / cut
# ---------------------------------------------------------------------------
def build_trim_args(inp, out, start, end, reencode=False):
    """Cut the ``[start, end]`` span out of *inp*.

    ``-ss start`` / ``-to end`` bracket the input.  When *reencode* is False we
    stream-copy (``-c copy``) -- instant, but cuts land on keyframes; when True
    we re-encode for frame-accurate cuts.  Either *start* or *end* may be None.
    """
    if not inp or not out:
        raise MediaKitError("Trim needs both an input and an output path.")
    start_s = _format_timestamp(start)
    end_s = _format_timestamp(end)
    if start_s is None and end_s is None:
        raise MediaKitError("Trim needs a start and/or an end time.")
    args = []
    if start_s is not None:
        args += ["-ss", start_s]
    args += ["-i", inp]
    if end_s is not None:
        # -to is measured against the (already -ss-shifted) input timeline.
        args += ["-to", end_s]
    if reencode:
        args += ["-c:v", "libx264", "-c:a", "aac"]
    else:
        args += ["-c", "copy"]
    args.append(out)
    return args


def trim_run(inp, out, start, end, reencode=False, on_progress=None,
             cancel=None):
    if not os.path.isfile(inp):
        raise MediaKitError(f"Input file not found: {inp!r}")
    args = build_trim_args(inp, out, start, end, reencode=reencode)
    ffmpeg.run_ffmpeg(args, on_progress=on_progress, input_path=inp,
                      cancel=cancel)
    return out


# ---------------------------------------------------------------------------
# Compress
# ---------------------------------------------------------------------------
def build_compress_args(inp, out, level="medium"):
    """Re-encode *inp* smaller using a named preset (low/medium/high)."""
    if not inp or not out:
        raise MediaKitError("Compress needs both an input and an output path.")
    if level not in COMPRESS_LEVELS:
        raise MediaKitError(
            f"Unknown compression level {level!r}; choose "
            f"{', '.join(COMPRESS_LEVELS)}.")
    cfg = COMPRESS_LEVELS[level]
    return [
        "-i", inp,
        "-c:v", "libx264",
        "-crf", str(cfg["crf"]),
        "-preset", cfg["preset"],
        "-c:a", "aac",
        "-b:a", cfg["audio_bitrate"],
        out,
    ]


def compress_run(inp, out, level="medium", on_progress=None, cancel=None):
    if not os.path.isfile(inp):
        raise MediaKitError(f"Input file not found: {inp!r}")
    args = build_compress_args(inp, out, level=level)
    ffmpeg.run_ffmpeg(args, on_progress=on_progress, input_path=inp,
                      cancel=cancel)
    return out


# ---------------------------------------------------------------------------
# Extract audio
# ---------------------------------------------------------------------------
def build_extract_audio_args(inp, out, acodec=None):
    """Pull the audio track out of *inp* into *out*.

    ``-vn`` drops video.  With no *acodec* we stream-copy when possible... but a
    plain copy fails if the container can't hold the source codec, so we pick a
    sensible encoder from the output extension instead (``libmp3lame`` for .mp3,
    ``aac`` for .m4a, etc.); pass ``acodec="copy"`` to force a raw copy.
    """
    if not inp or not out:
        raise MediaKitError("Extract-audio needs an input and an output path.")
    if acodec is None:
        ext = os.path.splitext(out)[1].lower()
        acodec = _AUDIO_CODEC_BY_EXT.get(ext, "aac")
    return ["-i", inp, "-vn", "-c:a", acodec, out]


def extract_audio_run(inp, out, acodec=None, on_progress=None, cancel=None):
    if not os.path.isfile(inp):
        raise MediaKitError(f"Input file not found: {inp!r}")
    args = build_extract_audio_args(inp, out, acodec=acodec)
    ffmpeg.run_ffmpeg(args, on_progress=on_progress, input_path=inp,
                      cancel=cancel)
    return out


# ---------------------------------------------------------------------------
# GIF
# ---------------------------------------------------------------------------
def build_gif_args(inp, out, fps=12, width=480, start=None, duration=None):
    """Build a single-pass GIF export (scaled, fps-limited).

    Single-pass keeps the code simple and still looks good: we scale to *width*
    (``-1`` height keeps aspect) and cap the frame rate with an ``fps`` filter.
    *start*/*duration* bracket the source span (``-ss``/``-t``).
    """
    if not inp or not out:
        raise MediaKitError("GIF needs both an input and an output path.")
    try:
        fps = int(fps)
        width = int(width)
    except (TypeError, ValueError):
        raise MediaKitError("GIF fps and width must be numbers.")
    if fps <= 0:
        raise MediaKitError("GIF fps must be positive.")
    if width <= 0:
        raise MediaKitError("GIF width must be positive.")
    args = []
    start_s = _format_timestamp(start)
    if start_s is not None:
        args += ["-ss", start_s]
    args += ["-i", inp]
    dur_s = _format_timestamp(duration)
    if dur_s is not None:
        args += ["-t", dur_s]
    vf = f"fps={fps},scale={width}:-1:flags=lanczos"
    args += ["-vf", vf, "-loop", "0", out]
    return args


def gif_run(inp, out, fps=12, width=480, start=None, duration=None,
            on_progress=None, cancel=None):
    if not os.path.isfile(inp):
        raise MediaKitError(f"Input file not found: {inp!r}")
    args = build_gif_args(inp, out, fps=fps, width=width, start=start,
                          duration=duration)
    ffmpeg.run_ffmpeg(args, on_progress=on_progress, input_path=inp,
                      cancel=cancel)
    return out


# ---------------------------------------------------------------------------
# Subtitle burn-in
# ---------------------------------------------------------------------------
def _escape_subs_path(path):
    """Escape a path for use inside the ffmpeg ``subtitles=`` filter string.

    The filtergraph parser treats ``\\``, ``:`` and ``'`` specially, so on
    Windows ``C:\\a.srt`` must become ``C\\:\\\\a.srt``.
    """
    p = path.replace("\\", "\\\\")
    p = p.replace(":", "\\:")
    p = p.replace("'", "\\'")
    return p


def build_subtitle_burn_args(inp, out, subs_path):
    """Burn *subs_path* (.srt/.ass) permanently into the video via ``subtitles``."""
    if not inp or not out:
        raise MediaKitError("Subtitle burn needs an input and an output path.")
    if not subs_path:
        raise MediaKitError("No subtitle file given.")
    vf = "subtitles=" + _escape_subs_path(subs_path)
    return ["-i", inp, "-vf", vf, "-c:a", "copy", out]


def subtitle_burn_run(inp, out, subs_path, on_progress=None, cancel=None):
    if not os.path.isfile(inp):
        raise MediaKitError(f"Input file not found: {inp!r}")
    if not os.path.isfile(subs_path):
        raise MediaKitError(f"Subtitle file not found: {subs_path!r}")
    args = build_subtitle_burn_args(inp, out, subs_path)
    ffmpeg.run_ffmpeg(args, on_progress=on_progress, input_path=inp,
                      cancel=cancel)
    return out


# ---------------------------------------------------------------------------
# First-frame extraction (used by the GUI GIF preview)
# ---------------------------------------------------------------------------
def build_thumbnail_args(inp, out, at=None, width=None):
    """Grab a single frame (``-frames:v 1``) at time *at* into an image *out*."""
    if not inp or not out:
        raise MediaKitError("Thumbnail needs an input and an output path.")
    args = []
    at_s = _format_timestamp(at)
    if at_s is not None:
        args += ["-ss", at_s]
    args += ["-i", inp, "-frames:v", "1"]
    if width:
        args += ["-vf", f"scale={int(width)}:-1"]
    args.append(out)
    return args


def thumbnail_run(inp, out, at=None, width=None, cancel=None):
    if not os.path.isfile(inp):
        raise MediaKitError(f"Input file not found: {inp!r}")
    args = build_thumbnail_args(inp, out, at=at, width=width)
    ffmpeg.run_ffmpeg(args, cancel=cancel)
    return out
