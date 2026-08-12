"""Format/codec conversion.

The container format is inferred from the output extension; ffmpeg picks a
sensible default encoder for that container unless one is given explicitly.
:func:`build_convert_args` is a pure function (no subprocess) so it can be unit
tested; :func:`convert` runs it.
"""

from __future__ import annotations

import os

from .errors import MediaKitError
from . import ffmpeg


def build_convert_args(inp, out, vcodec=None, acodec=None, crf=None,
                       preset=None, extra=None):
    """Build the ffmpeg argument list for a straight convert.

    ``-i inp`` then any of ``-c:v``/``-c:a``/``-crf``/``-preset`` that were
    supplied, followed by *extra* (a list of raw flags) and finally *out*.  The
    output container is inferred by ffmpeg from *out*'s extension.
    """
    if not inp:
        raise MediaKitError("No input file given.")
    if not out:
        raise MediaKitError("No output file given.")
    args = ["-i", inp]
    if vcodec:
        args += ["-c:v", vcodec]
    if acodec:
        args += ["-c:a", acodec]
    if crf is not None:
        args += ["-crf", str(crf)]
    if preset:
        args += ["-preset", preset]
    if extra:
        args += list(extra)
    args.append(out)
    return args


def convert(inp, out, vcodec=None, acodec=None, crf=None, preset=None,
            extra=None, on_progress=None, cancel=None):
    """Convert *inp* to *out*, inferring the format from *out*'s extension."""
    if not os.path.isfile(inp):
        raise MediaKitError(f"Input file not found: {inp!r}")
    args = build_convert_args(inp, out, vcodec=vcodec, acodec=acodec, crf=crf,
                              preset=preset, extra=extra)
    ffmpeg.run_ffmpeg(args, on_progress=on_progress, input_path=inp,
                      cancel=cancel)
    return out
