"""Locate and drive an external FFmpeg/ffprobe binary.

FFmpeg is an *external* program (LGPL/GPL) that this app invokes as a
subprocess -- it is never bundled inside the Python code.  The Windows
installer ships an unmodified ``ffmpeg.exe`` next to the app; this module finds
it there, or falls back to ``PATH`` and the usual install locations.

The heavy lifting is split into small **pure** helpers that only *build* the
argument list (``base_args``, ``progress_args``, ``probe_args`` and the
builders in :mod:`mediakit.convert`/:mod:`mediakit.edit`).  Those are trivially
unit-testable without FFmpeg present.  Only :func:`run_ffmpeg` / :func:`probe`
actually spawn a process.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

from .errors import MediaKitError

# ---------------------------------------------------------------------------
# Binary location
# ---------------------------------------------------------------------------
# Common install dirs to consult after the app dir and PATH have been tried.
_COMMON_DIRS = [
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    r"C:\Program Files (x86)\ffmpeg\bin",
    r"C:\ProgramData\chocolatey\bin",
    "/usr/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/opt/local/bin",
    "/snap/bin",
]


def _exe_name(tool):
    """``ffmpeg`` -> ``ffmpeg.exe`` on Windows, unchanged elsewhere."""
    return tool + ".exe" if os.name == "nt" else tool


def _app_dirs():
    """Directories to search *before* PATH, most-specific first.

    For a frozen build we look next to the executable (where the installer drops
    ``ffmpeg.exe``) and at ``sys._MEIPASS``.  From source we consult the package
    directory and its parent (the repo root).
    """
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        dirs.append(here)
        dirs.append(os.path.dirname(here))
    return dirs


def find_binary(tool):
    """Return an absolute path to *tool* (``ffmpeg``/``ffprobe``) or ``None``.

    Search order: an explicit ``MEDIAKIT_FFMPEG_DIR`` / ``IMAGEMAGICK``-style env
    override, then next-to-exe / app dir, then ``PATH`` (``shutil.which``), then
    the common install directories.
    """
    name = _exe_name(tool)

    # 1. explicit override (handy for tests and unusual installs)
    override = os.environ.get("MEDIAKIT_FFMPEG_DIR")
    if override:
        cand = os.path.join(override, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return os.path.abspath(cand)

    # 2. next to the app / bundled
    for d in _app_dirs():
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return os.path.abspath(cand)

    # 3. PATH
    found = shutil.which(tool)
    if found:
        return found

    # 4. common install locations
    for d in _COMMON_DIRS:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return os.path.abspath(cand)

    return None


_MISSING_HELP = (
    "Could not find {tool}. Media Toolkit drives an external FFmpeg. "
    "The Windows installer bundles it next to the app automatically; if you are "
    "running from source, install FFmpeg and either put ffmpeg.exe next to the "
    "app or add it to your PATH (https://ffmpeg.org/download.html). You can also "
    "set MEDIAKIT_FFMPEG_DIR to the folder containing the binaries."
)


def ffmpeg_path():
    """Absolute path to the ffmpeg binary, or raise :class:`MediaKitError`."""
    p = find_binary("ffmpeg")
    if not p:
        raise MediaKitError(_MISSING_HELP.format(tool="ffmpeg"))
    return p


def ffprobe_path():
    """Absolute path to the ffprobe binary, or raise :class:`MediaKitError`."""
    p = find_binary("ffprobe")
    if not p:
        raise MediaKitError(_MISSING_HELP.format(tool="ffprobe"))
    return p


def is_available():
    """True when both ffmpeg and ffprobe can be located (never raises)."""
    return bool(find_binary("ffmpeg") and find_binary("ffprobe"))


# ---------------------------------------------------------------------------
# Pure argument builders (no subprocess -- unit-testable)
# ---------------------------------------------------------------------------
def base_args(overwrite=True, hide_banner=True):
    """Leading flags common to every ffmpeg invocation this app makes."""
    args = []
    if hide_banner:
        args += ["-hide_banner", "-loglevel", "error"]
    args.append("-y" if overwrite else "-n")
    return args


def progress_args():
    """Flags that make ffmpeg emit machine-readable progress on stdout."""
    return ["-progress", "pipe:1", "-nostats"]


def probe_args(path):
    """Argument list (sans the ffprobe binary) for a full JSON probe."""
    return [
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-of", "json",
        path,
    ]


# ---------------------------------------------------------------------------
# stderr / -progress parsing helpers (pure)
# ---------------------------------------------------------------------------
# ffmpeg stderr carries lines like:  frame=  10 fps=0 ... time=00:00:01.50 ...
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
# -progress key=value lines:  out_time_ms=1500000  /  out_time=00:00:01.500000
_OUT_TIME_MS_RE = re.compile(r"out_time_ms=(\d+)")
_OUT_TIME_RE = re.compile(r"out_time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _hms_to_seconds(h, m, s):
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_time_seconds(line):
    """Extract the processed-time in seconds from an ffmpeg output line.

    Understands both ``-progress`` (``out_time_ms`` / ``out_time``) and the
    classic stderr ``time=HH:MM:SS.xx`` field.  Returns ``None`` when the line
    carries no time.
    """
    if not line:
        return None
    m = _OUT_TIME_MS_RE.search(line)
    if m:
        return int(m.group(1)) / 1_000_000.0
    m = _OUT_TIME_RE.search(line)
    if m:
        return _hms_to_seconds(*m.groups())
    m = _TIME_RE.search(line)
    if m:
        return _hms_to_seconds(*m.groups())
    return None


def progress_fraction(processed_seconds, total_seconds):
    """Clamp ``processed/total`` to ``[0.0, 1.0]`` (``None`` if total unknown)."""
    if not total_seconds or total_seconds <= 0:
        return None
    if processed_seconds is None:
        return None
    return max(0.0, min(1.0, processed_seconds / total_seconds))


# ---------------------------------------------------------------------------
# Actual process execution
# ---------------------------------------------------------------------------
def _no_window_kwargs():
    """Suppress the console window that would otherwise flash on Windows."""
    if os.name == "nt":
        try:
            return {"creationflags": subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]
        except Exception:
            return {}
    return {}


def probe(path):
    """Run ffprobe on *path* and return a normalised info dict.

    Keys: ``duration`` (float seconds or None), ``bitrate`` (int bps or None),
    ``format_name``, ``size`` (bytes or None), ``streams`` (raw list),
    ``video``/``audio`` (first stream of each, or None), ``width``/``height``,
    ``vcodec``/``acodec`` and ``resolution`` ("WxH" or None).  Raises
    :class:`MediaKitError` on a missing binary or an unreadable file.
    """
    if not path or not os.path.isfile(path):
        raise MediaKitError(f"Input file not found: {path!r}")
    exe = ffprobe_path()
    cmd = [exe] + probe_args(path)
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, **_no_window_kwargs())
    except OSError as ex:
        raise MediaKitError(f"Could not run ffprobe: {ex}")
    if proc.returncode != 0:
        msg = (proc.stderr or "").strip() or "ffprobe failed"
        raise MediaKitError(_clean_ffmpeg_error(msg))
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError as ex:
        raise MediaKitError(f"Could not parse ffprobe output: {ex}")
    return _normalise_probe(data)


def _normalise_probe(data):
    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    def _i(x):
        try:
            return int(float(x))
        except (TypeError, ValueError):
            return None

    duration = _f(fmt.get("duration"))
    if duration is None and video:
        duration = _f(video.get("duration"))
    width = _i(video.get("width")) if video else None
    height = _i(video.get("height")) if video else None
    return {
        "duration": duration,
        "bitrate": _i(fmt.get("bit_rate")),
        "format_name": fmt.get("format_name"),
        "size": _i(fmt.get("size")),
        "streams": streams,
        "video": video,
        "audio": audio,
        "width": width,
        "height": height,
        "vcodec": video.get("codec_name") if video else None,
        "acodec": audio.get("codec_name") if audio else None,
        "resolution": (f"{width}x{height}" if width and height else None),
    }


def probe_duration(path):
    """Best-effort duration in seconds (``None`` if unknown/unavailable)."""
    try:
        return probe(path).get("duration")
    except MediaKitError:
        return None


def _clean_ffmpeg_error(text):
    """Return the last meaningful line of ffmpeg's stderr (no traceback noise)."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return "ffmpeg failed"
    return lines[-1]


def run_ffmpeg(args, on_progress=None, total_seconds=None, input_path=None,
               cancel=None):
    """Run ``ffmpeg <args>`` to completion, or raise :class:`MediaKitError`.

    *args* is the argument list **without** the ffmpeg binary and without the
    leading ``-hide_banner``/``-y`` flags (those are prepended here) -- pass the
    output of a ``build_*_args`` helper.  Progress flags are injected so that
    *on_progress* (a callable taking a float fraction in ``[0,1]``) can be driven
    from the ``-progress`` stream.  When *total_seconds* is not given but
    *input_path* is, the duration is probed automatically.

    *cancel*, if given, is a callable returning True to abort; the process is
    terminated and a :class:`MediaKitError` is raised.
    """
    exe = ffmpeg_path()

    if total_seconds is None and input_path and on_progress is not None:
        total_seconds = probe_duration(input_path)

    cmd = [exe] + base_args()
    if on_progress is not None:
        cmd += progress_args()
    cmd += list(args)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, universal_newlines=True,
            **_no_window_kwargs())
    except OSError as ex:
        raise MediaKitError(f"Could not run ffmpeg: {ex}")

    last_fraction = 0.0
    try:
        # Read the -progress stream line-by-line for progress callbacks.
        if proc.stdout is not None:
            for line in proc.stdout:
                if cancel is not None and cancel():
                    proc.kill()
                    proc.wait()
                    raise MediaKitError("Cancelled.")
                secs = parse_time_seconds(line)
                if secs is not None and on_progress is not None:
                    frac = progress_fraction(secs, total_seconds)
                    if frac is not None and frac >= last_fraction:
                        last_fraction = frac
                        try:
                            on_progress(frac)
                        except Exception:
                            pass
        stderr = proc.stderr.read() if proc.stderr is not None else ""
        proc.wait()
    finally:
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    if proc.returncode != 0:
        raise MediaKitError(_clean_ffmpeg_error(stderr))
    if on_progress is not None:
        try:
            on_progress(1.0)
        except Exception:
            pass
    return True
