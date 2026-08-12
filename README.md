# Media Toolkit

A fast, **offline**, **100% open-source** audio/video toolkit for Windows. Nothing is uploaded anywhere. Built entirely by AI with human testing and guidance, and published on [QuickOpen](https://quickopen.ai/projects/media-toolkit).

> **100% AI-built and open source.** Apache-2.0.

## What it does

Convert between common audio and video formats, trim and cut clips, compress with quality presets, extract audio from video, create GIFs from video, and burn subtitles in — all by driving a bundled FFmpeg. Batch-friendly with progress. Runs entirely on your machine.

## Install

Download **`MediaToolkit-Setup.exe`** from the [QuickOpen page](https://quickopen.ai/projects/media-toolkit) or the [GitHub release](https://github.com/quickpod/media-toolkit/releases/latest) and double-click it. It installs per-user, adds Desktop and Start Menu shortcuts, and can optionally trust the QuickOpen Root CA. Authenticode-signed by the QuickOpen Code Signing CA — verify at [quickopen.ai/trust](https://quickopen.ai/trust).

## Run from source

```sh
pip install -r requirements.txt
python media_app.py          # GUI
python -m mediakit --help    # CLI
```

## Features

- **Convert** — remux/transcode between common containers (mp4, mkv, mov, webm, avi) and audio formats (mp3, m4a, wav, flac); pick video/audio codecs and a CRF quality knob. Format is inferred from the output extension.
- **Trim / cut** — extract a `[start, end]` span; stream-copy for an instant, lossless cut, or re-encode for a frame-accurate one.
- **Compress** — re-encode smaller with `low` / `medium` / `high` presets (x264 CRF + audio bitrate).
- **Extract audio** — pull the audio track out to MP3, M4A, WAV, FLAC, Opus… (codec inferred from the extension).
- **GIF** — turn a span of video into a scaled, fps-limited GIF, with a first-frame preview in the GUI.
- **Subtitles** — burn a `.srt`/`.ass` file permanently into the picture.
- **Batch** — apply convert / compress / extract-audio / gif across many files into an output folder.
- Every operation shows **live progress** (parsed from FFmpeg) and can be **cancelled**; errors are shown inline, never as a traceback. Dark mode, recent files, and a fully offline workflow.

## CLI examples

```sh
python -m mediakit probe input.mkv                 # duration, codecs, resolution, bitrate
python -m mediakit probe input.mkv --json          # raw ffprobe JSON

python -m mediakit convert in.mkv out.mp4          # container/codec inferred from .mp4
python -m mediakit convert in.mov out.webm --vcodec vp9 --acodec libopus --crf 30

python -m mediakit trim in.mp4 clip.mp4 --start 00:00:05 --end 00:00:20     # stream-copy (instant)
python -m mediakit trim in.mp4 clip.mp4 --start 5 --end 20 --reencode       # frame-accurate

python -m mediakit compress in.mp4 small.mp4 --level high
python -m mediakit extract-audio in.mp4 track.mp3
python -m mediakit gif in.mp4 out.gif --fps 12 --width 480 --start 3 --duration 4
python -m mediakit subtitles in.mp4 out.mp4 captions.srt

python -m mediakit batch compress ./out ./a.mp4 ./b.mkv --level medium
python -m mediakit batch extract-audio ./out *.mp4 --ext .mp3
```

Add `-q`/`--quiet` to any command to suppress the progress bar. Every command exits non-zero with a clear `error: …` message on failure — including a message explaining how to install FFmpeg if the binary can't be found.

## FFmpeg

This app drives **FFmpeg**, which is licensed under the LGPL/GPL. The Windows installer bundles an unmodified `ffmpeg.exe` next to the app and Media Toolkit **finds it automatically** — you don't have to configure anything. Running from source (or on another OS), the app looks next to the executable, then on your `PATH`, then in the usual install locations; so you can simply put `ffmpeg`/`ffprobe` on your `PATH` (or set `MEDIAKIT_FFMPEG_DIR` to the folder that holds them). FFmpeg is invoked as a separate subprocess and is never modified or statically linked; see `ffmpeg-LICENSE.txt` and https://ffmpeg.org. Media Toolkit's own code is Apache-2.0.


## License

Apache-2.0 — see [LICENSE](LICENSE). A 100% AI-built project published on QuickOpen.
