"""Command-line interface: ``python -m mediakit <command> ...``.

Commands: ``probe``, ``convert``, ``trim``, ``compress``, ``extract-audio``,
``gif``, ``subtitles``, ``batch``.  Every failure -- including a missing FFmpeg
binary -- surfaces as a clean ``error: ...`` line and a non-zero exit, never a
traceback.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import (
    MediaKitError,
    batch_apply,
    compress_run,
    convert,
    extract_audio_run,
    gif_run,
    probe,
    subtitle_burn_run,
    trim_run,
)
from .edit import COMPRESS_LEVELS


def _progress_printer(stream=None):
    """Return an ``on_progress`` callback that draws a one-line percentage bar."""
    stream = stream or sys.stderr
    state = {"last": -1}

    def cb(frac):
        pct = int(frac * 100)
        if pct != state["last"]:
            state["last"] = pct
            bar = "#" * (pct // 5)
            stream.write(f"\r  [{bar:<20}] {pct:3d}%")
            stream.flush()
            if pct >= 100:
                stream.write("\n")
                stream.flush()

    return cb


# --- command handlers -------------------------------------------------------
def cmd_probe(a):
    info = probe(a.input)
    if a.json:
        print(json.dumps(info, indent=2, default=str))
        return
    dur = info.get("duration")
    print(f"File:       {a.input}")
    print(f"Format:     {info.get('format_name') or '?'}")
    print(f"Duration:   {dur:.3f}s" if dur is not None else "Duration:   ?")
    print(f"Resolution: {info.get('resolution') or '-'}")
    print(f"Video:      {info.get('vcodec') or '-'}")
    print(f"Audio:      {info.get('acodec') or '-'}")
    br = info.get("bitrate")
    print(f"Bitrate:    {br} bps" if br else "Bitrate:    -")


def cmd_convert(a):
    prog = None if a.quiet else _progress_printer()
    convert(a.input, a.output, vcodec=a.vcodec, acodec=a.acodec,
            crf=a.crf, preset=a.preset, on_progress=prog)
    print(f"Converted -> {a.output}")


def cmd_trim(a):
    prog = None if a.quiet else _progress_printer()
    trim_run(a.input, a.output, a.start, a.end, reencode=a.reencode,
             on_progress=prog)
    print(f"Trimmed -> {a.output}")


def cmd_compress(a):
    prog = None if a.quiet else _progress_printer()
    compress_run(a.input, a.output, level=a.level, on_progress=prog)
    print(f"Compressed ({a.level}) -> {a.output}")


def cmd_extract_audio(a):
    prog = None if a.quiet else _progress_printer()
    extract_audio_run(a.input, a.output, acodec=a.acodec, on_progress=prog)
    print(f"Audio extracted -> {a.output}")


def cmd_gif(a):
    prog = None if a.quiet else _progress_printer()
    gif_run(a.input, a.output, fps=a.fps, width=a.width, start=a.start,
            duration=a.duration, on_progress=prog)
    print(f"GIF -> {a.output}")


def cmd_subtitles(a):
    prog = None if a.quiet else _progress_printer()
    subtitle_burn_run(a.input, a.output, a.subs, on_progress=prog)
    print(f"Subtitles burned -> {a.output}")


def cmd_batch(a):
    prog = None if a.quiet else _progress_printer()

    def on_item(idx, total, path):
        if not a.quiet:
            sys.stderr.write(f"\n[{idx + 1}/{total}] {path}\n")

    results = batch_apply(
        a.op, a.inputs, a.out_dir, out_ext=a.ext, level=a.level,
        acodec=a.acodec, fps=a.fps, width=a.width, vcodec=a.vcodec,
        on_progress=prog, on_item=on_item)
    ok = sum(1 for _i, o, _e in results if o)
    fail = len(results) - ok
    print(f"\nBatch {a.op}: {ok} succeeded, {fail} failed.")
    for inp, out, err in results:
        if err:
            print(f"  FAIL {inp}: {err}")
        else:
            print(f"  ok   {inp} -> {out}")
    if fail:
        return 1
    return 0


# --- parser -----------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="mediakit",
        description="Offline audio/video toolkit driving an external FFmpeg.")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    def add(name, handler, help):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(func=handler)
        sp.add_argument("-q", "--quiet", action="store_true",
                        help="suppress the progress bar")
        return sp

    p = add("probe", cmd_probe, "Show format/stream info for a media file")
    p.add_argument("input")
    p.add_argument("--json", action="store_true", help="emit raw JSON")

    p = add("convert", cmd_convert, "Convert to another format/codec")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--vcodec", help="video codec (e.g. libx264)")
    p.add_argument("--acodec", help="audio codec (e.g. aac)")
    p.add_argument("--crf", type=int, help="quality (lower=better, x264/x265)")
    p.add_argument("--preset", help="encoder preset (e.g. medium)")

    p = add("trim", cmd_trim, "Cut a [start,end] span out of a clip")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--start", help="start time (seconds or HH:MM:SS)")
    p.add_argument("--end", help="end time (seconds or HH:MM:SS)")
    p.add_argument("--reencode", action="store_true",
                   help="re-encode for frame-accurate cuts (default: stream-copy)")

    p = add("compress", cmd_compress, "Re-encode smaller (low/medium/high)")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--level", choices=sorted(COMPRESS_LEVELS), default="medium")

    p = add("extract-audio", cmd_extract_audio, "Pull the audio track out")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--acodec", help="audio codec (default: inferred from ext)")

    p = add("gif", cmd_gif, "Make a GIF from a video span")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--fps", type=int, default=12)
    p.add_argument("--width", type=int, default=480)
    p.add_argument("--start", help="start time (seconds or HH:MM:SS)")
    p.add_argument("--duration", help="duration (seconds or HH:MM:SS)")

    p = add("subtitles", cmd_subtitles, "Burn a .srt/.ass into the video")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("subs", help="path to the .srt/.ass subtitle file")

    p = add("batch", cmd_batch, "Apply an op across many files into a folder")
    p.add_argument("op", choices=["convert", "compress", "extract-audio", "gif"])
    p.add_argument("out_dir")
    p.add_argument("inputs", nargs="+")
    p.add_argument("--ext", help="output extension (e.g. .mp4, .mp3)")
    p.add_argument("--level", choices=sorted(COMPRESS_LEVELS), default="medium")
    p.add_argument("--acodec")
    p.add_argument("--vcodec")
    p.add_argument("--fps", type=int, default=12)
    p.add_argument("--width", type=int, default=480)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        rc = args.func(args)
    except MediaKitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
