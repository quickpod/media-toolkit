"""mediakit -- an offline audio/video toolkit that drives an external FFmpeg.

FFmpeg (LGPL/GPL) is *not* part of this package; it is located on disk and run
as a subprocess.  Public helpers raise :class:`MediaKitError` (and only that) on
failure, including when the FFmpeg binary cannot be found.

    from mediakit import convert, probe
    info = probe("clip.mkv")
    convert("clip.mkv", "clip.mp4")

The GUI (:mod:`mediakit.gui`) and CLI (:mod:`mediakit.__main__`) build on this
module.
"""

from __future__ import annotations

from .errors import MediaKitError
from .ffmpeg import (
    ffmpeg_path,
    ffprobe_path,
    find_binary,
    is_available,
    probe,
    probe_duration,
    run_ffmpeg,
)
from .convert import build_convert_args, convert
from .edit import (
    COMPRESS_LEVELS,
    build_compress_args,
    build_extract_audio_args,
    build_gif_args,
    build_subtitle_burn_args,
    build_thumbnail_args,
    build_trim_args,
    compress_run,
    extract_audio_run,
    gif_run,
    subtitle_burn_run,
    thumbnail_run,
    trim_run,
)
from .batch import batch_apply

__version__ = "1.0.0"

__all__ = [
    "MediaKitError",
    "ffmpeg_path",
    "ffprobe_path",
    "find_binary",
    "is_available",
    "probe",
    "probe_duration",
    "run_ffmpeg",
    "build_convert_args",
    "convert",
    "COMPRESS_LEVELS",
    "build_trim_args",
    "build_compress_args",
    "build_extract_audio_args",
    "build_gif_args",
    "build_subtitle_burn_args",
    "build_thumbnail_args",
    "trim_run",
    "compress_run",
    "extract_audio_run",
    "gif_run",
    "subtitle_burn_run",
    "thumbnail_run",
    "batch_apply",
]
