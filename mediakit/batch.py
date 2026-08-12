"""Apply one operation across many inputs into an output directory.

Each item is processed independently; a failure on one file is captured and the
rest continue.  Returns a list of ``(input_path, output_path_or_None, error_or_None)``
result tuples so callers can report a per-file summary.
"""

from __future__ import annotations

import os

from .errors import MediaKitError
from . import convert as _convert
from . import edit as _edit

# op name -> (default output extension, worker)
_OPS = ("convert", "compress", "extract-audio", "gif")


def _out_path(inp, out_dir, ext):
    stem = os.path.splitext(os.path.basename(inp))[0]
    return os.path.join(out_dir, stem + ext)


def batch_apply(op, inputs, out_dir, out_ext=None, level="medium",
                acodec=None, fps=12, width=480, vcodec=None,
                on_progress=None, on_item=None, cancel=None):
    """Run *op* over *inputs*, writing results into *out_dir*.

    *op* is one of ``convert``, ``compress``, ``extract-audio``, ``gif``.
    *on_progress* (fraction of the whole batch) and *on_item* (called with
    ``(index, total, input_path)`` before each file) drive UI feedback.
    """
    if op not in _OPS:
        raise MediaKitError(f"Unknown batch op {op!r}; choose {', '.join(_OPS)}.")
    if not inputs:
        raise MediaKitError("No input files given.")
    if not out_dir:
        raise MediaKitError("No output directory given.")
    os.makedirs(out_dir, exist_ok=True)

    # Choose the default output extension per op when none was supplied.
    default_ext = {
        "convert": out_ext or ".mp4",
        "compress": out_ext or ".mp4",
        "extract-audio": out_ext or ".mp3",
        "gif": ".gif",
    }[op]

    results = []
    total = len(inputs)
    for idx, inp in enumerate(inputs):
        if cancel is not None and cancel():
            raise MediaKitError("Cancelled.")
        if on_item is not None:
            try:
                on_item(idx, total, inp)
            except Exception:
                pass
        out = _out_path(inp, out_dir, default_ext)

        def _item_progress(frac, _idx=idx):
            if on_progress is not None:
                try:
                    on_progress((_idx + frac) / total)
                except Exception:
                    pass

        try:
            if op == "convert":
                _convert.convert(inp, out, vcodec=vcodec, acodec=acodec,
                                 on_progress=_item_progress, cancel=cancel)
            elif op == "compress":
                _edit.compress_run(inp, out, level=level,
                                   on_progress=_item_progress, cancel=cancel)
            elif op == "extract-audio":
                _edit.extract_audio_run(inp, out, acodec=acodec,
                                        on_progress=_item_progress, cancel=cancel)
            elif op == "gif":
                _edit.gif_run(inp, out, fps=fps, width=width,
                              on_progress=_item_progress, cancel=cancel)
            results.append((inp, out, None))
        except MediaKitError as ex:
            results.append((inp, None, str(ex)))
        if on_progress is not None:
            try:
                on_progress((idx + 1) / total)
            except Exception:
                pass
    return results
