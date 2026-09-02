#!/usr/bin/env python3
r"""Media Toolkit -- an Aura (QuickOpen design system) GUI on the ``mediakit`` API.

A single Aura window: a left sidebar of tool sections (Convert, Trim, Compress,
Extract Audio, GIF, Subtitles, Batch) and a swappable content area.  Every
operation calls the tested core library, runs on a background thread so the UI
stays responsive, drives a live progress bar from FFmpeg's ``-progress`` stream,
and can be cancelled.  Results are reported inline in the Aura status bar -- an
"Open folder" button on success, or the :class:`MediaKitError` message (never a
raw traceback) on failure.

Design goals baked in here (mirrors the QuickOpen house style):
  * built on the vendored ``mediakit/aura.py`` design system, which layers the
    quickopen.ai look (deep space + light) over CustomTkinter.  Runtime deps:
    ``customtkinter`` (+ ``darkdetect``) — declared in requirements.txt; the
    PyInstaller build adds ``--collect-all customtkinter``.
  * Importing this module does nothing.  Only :func:`main` builds a root window,
    and it degrades gracefully (prints a note, returns 0) with no display or
    with customtkinter missing.
  * Frozen-exe safe: bundled assets resolved via ``sys._MEIPASS`` / the exe dir.
  * FFmpeg is external.  If it can't be found, a header note explains it, but
    the UI stays usable and every operation reports the problem inline.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading

# tkinter/customtkinter are imported lazily inside main()/build_app so that
# merely importing this module (e.g. on a headless CI box) never fails.

APP_NAME = "Media Toolkit"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "Media Toolkit — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
ACCENT = "#cf2d3a"      # publish/specs/media-toolkit.json "accent": [207, 45, 58]

VIDEO_TYPES = [
    ("Video/audio", "*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v "
                    "*.mp3 *.wav *.m4a *.aac *.ogg *.opus *.flac"),
    ("All files", "*.*"),
]
AUDIO_OUT_TYPES = [
    ("Audio", "*.mp3 *.m4a *.aac *.ogg *.opus *.flac *.wav"),
    ("All files", "*.*"),
]
SUB_TYPES = [("Subtitles", "*.srt *.ass *.ssa *.vtt"), ("All files", "*.*")]

# (tool_id, label, glyph, description).  glyph -> DejaVu-safe nav icon;
# builder is ``_build_<tool_id>``.  A module-level list so the GUI crawler can
# enumerate the sections (paired with the ``_show_tool`` alias below).
TOOLS = [
    ("convert", "Convert", "⇄",
     "Open a media file, inspect it, then convert to another container/codec "
     "with a quality knob."),
    ("trim", "Trim / Cut", "◈",
     "Cut a [start, end] span out. Stream-copy for an instant cut, or "
     "re-encode for a frame-accurate one."),
    ("compress", "Compress", "⊙",
     "Re-encode smaller with a low / medium / high preset."),
    ("extractaudio", "Extract Audio", "◉",
     "Pull the audio track out to MP3, M4A, WAV and friends."),
    ("gif", "GIF", "✳",
     "Turn a span of video into a GIF (fps / width / start / duration), with a "
     "first-frame preview."),
    ("subtitles", "Subtitles", "✎",
     "Burn a .srt/.ass subtitle file permanently into the video."),
    ("batch", "Batch", "▤",
     "Apply convert / compress / extract-audio / gif across many files into an "
     "output folder."),
]


# ---------------------------------------------------------------------------
# Asset / frozen handling  +  small OS helpers
# ---------------------------------------------------------------------------
def asset_path(name):
    """Locate a bundled asset from source OR a PyInstaller one-file build."""
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots += [here, os.path.dirname(here), os.getcwd()]
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            return candidate
    return None


def human_size(num_bytes):
    """Human-readable byte size."""
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}TB"


def human_duration(seconds):
    if seconds is None:
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def open_in_file_manager(path):
    """Best-effort 'reveal in file manager', guarded on every platform."""
    try:
        folder = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
        if hasattr(os, "startfile"):
            os.startfile(folder)  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", folder])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])
        return True
    except Exception:
        return False


def open_with_default_app(path):
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def _suggest_out(inp, suffix, new_ext=None):
    """Suggest ``name<suffix>.ext`` next to *inp* (optionally changing ext)."""
    if not inp:
        return ""
    root, ext = os.path.splitext(inp)
    if new_ext:
        ext = new_ext if new_ext.startswith(".") else "." + new_ext
    return root + suffix + ext


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter/customtkinter imported only inside build_app)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to live GUI imports.

    Kept inside a function so this module imports cleanly without a display
    (and without customtkinter installed).
    """
    import tkinter as tk
    from tkinter import filedialog, ttk
    from .aura import filedialog  # noqa: F811 - Aura kdialog-native pickers
    import customtkinter as ctk

    from . import aura, guiconfig
    from .errors import MediaKitError
    from . import ffmpeg as ffmpeg_mod
    from . import edit as edit_mod
    from . import batch as batch_mod
    from .edit import COMPRESS_LEVELS
    # NOTE: mediakit/__init__ rebinds the package attribute 'convert' to the
    # convert() FUNCTION, so 'from . import convert' would NOT get the module.
    # Import the function directly (edit/batch/ffmpeg are NOT shadowed).
    from .convert import convert as run_convert

    # -- small reusable widgets ------------------------------------------
    class FileRow(ctk.CTkFrame):
        """A labelled path field + Browse button. ``mode`` picks the dialog."""

        def __init__(self, master, app, label, mode="open", filetypes=None,
                     on_change=None, defaultext=None):
            super().__init__(master, fg_color="transparent")
            self.app = app
            self.mode = mode
            self.filetypes = filetypes or VIDEO_TYPES
            self.defaultext = defaultext
            self._on_change = on_change
            ctk.CTkLabel(self, text=label, width=96, anchor="w",
                         font=aura.font()).pack(side="left")
            # No textvariable: CTkEntry placeholders only work without one.
            self.entry = aura.AuraEntry(self, placeholder="Path…")
            self.entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
            self.entry.bind("<KeyRelease>", lambda _e: self._fire())
            aura.AuraButton(self, "Browse…", kind="secondary",
                            command=self._browse).pack(side="left")

        def _fire(self):
            if self._on_change:
                self._on_change(self.get())

        def _browse(self):
            if self.mode == "dir":
                p = filedialog.askdirectory(title="Choose a folder")
            elif self.mode == "save":
                p = filedialog.asksaveasfilename(
                    title="Save as", defaultextension=self.defaultext,
                    filetypes=self.filetypes)
            else:
                p = filedialog.askopenfilename(title="Choose a file",
                                               filetypes=self.filetypes)
            if p:
                self.set(p)

        def get(self):
            return self.entry.get().strip()

        def set(self, value):
            self.entry.delete(0, "end")
            if value:
                self.entry.insert(0, value)
            self._fire()

    class FileList(ctk.CTkFrame):
        """A multi-file listbox with Add / Remove / Clear."""

        def __init__(self, master, app, filetypes=None):
            super().__init__(master, fg_color="transparent")
            self.app = app
            self.filetypes = filetypes or VIDEO_TYPES
            box = ctk.CTkFrame(self, fg_color="transparent")
            box.pack(fill="both", expand=True)
            self.listbox = tk.Listbox(box, height=8, activestyle="none",
                                      selectmode="extended",
                                      exportselection=False,
                                      highlightthickness=0, borderwidth=0)
            sb = ttk.Scrollbar(box, orient="vertical", command=self.listbox.yview)
            self.listbox.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.listbox.pack(side="left", fill="both", expand=True)
            aura.track(self.listbox, "listbox")

            btns = ctk.CTkFrame(self, fg_color="transparent")
            btns.pack(fill="x", pady=(8, 0))
            aura.AuraButton(btns, "Add files…", command=self.add).pack(side="left")
            aura.AuraButton(btns, "Remove", kind="secondary",
                            command=self.remove).pack(side="left", padx=8)
            aura.AuraButton(btns, "Clear", kind="secondary",
                            command=self.clear).pack(side="left")

        def add(self):
            paths = filedialog.askopenfilenames(title="Add files",
                                                filetypes=self.filetypes)
            for p in paths:
                self.listbox.insert("end", p)
            if paths:
                self.app.remember_input(paths[0])

        def remove(self):
            for i in reversed(self.listbox.curselection()):
                self.listbox.delete(i)

        def clear(self):
            self.listbox.delete(0, "end")

        def items(self):
            return list(self.listbox.get(0, "end"))

    # -- the main window --------------------------------------------------
    class App(aura.AuraApp):
        def __init__(self):
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=guiconfig.get_theme(),
                icon_png=asset_path("media-toolkit.png"), version=APP_VERSION,
                tagline="offline · FFmpeg",
                on_theme_change=guiconfig.set_theme,
                size=(1080, 700), min_size=(920, 600))

            self._busy = False
            self._cancel_event = None
            self._img_refs_gui = []
            self._history = []
            self._last_output_dir = None
            self._tmpdir = tempfile.mkdtemp(prefix="mediatk_gui_")
            # per-panel widget handles, filled by the builders
            self._convert = {}

            # FFmpeg-missing note lives in the (shared) header action area.
            self._ffmpeg_note = ctk.CTkLabel(
                self.header_actions, text="", anchor="e", font=aura.font(),
                text_color=aura.P("warn"))
            self._ffmpeg_note.pack(side="right")

            # progress + cancel + open-folder controls in the status bar.
            self._prog = aura.ProgressBar(self.statusbar.actions, width=180)
            self._cancel_btn = aura.AuraButton(
                self.statusbar.actions, "Cancel", kind="secondary", height=30,
                command=self._request_cancel)
            self._openfolder_btn = aura.AuraButton(
                self.statusbar.actions, "Open folder", kind="secondary",
                height=30, command=self._open_last_folder)

            self._set_icon()
            self._build_menu()
            for tid, label, glyph, _desc in TOOLS:
                self.add_section(tid, label, glyph, getattr(self, "_build_" + tid))
            self.show(TOOLS[0][0])
            self._refresh_ffmpeg_note()
            self.set_status("Ready")
            self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ---- assets / icon
        def _set_icon(self):
            try:
                ico = asset_path("media-toolkit.ico")
                if ico and os.name == "nt":
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("media-toolkit.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs_gui.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- crawler / navigation alias (AuraApp.show does the real work)
        def _show_tool(self, tool_id):
            self.show(tool_id)

        # ---- menu (native menus stay; theme lives in the sidebar toggle too)
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Open…", accelerator="Ctrl+O",
                              command=self._open_file)
            self._recent_menu = tk.Menu(filem, tearoff=0)
            filem.add_cascade(label="Open Recent", menu=self._recent_menu)
            self._fill_recent_menu()
            filem.add_separator()
            filem.add_command(label="Exit", command=self._on_close)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="FFmpeg status…",
                              command=self._show_ffmpeg_status)
            helpm.add_command(label="About", command=self._about)
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)
            self.bind_all("<Control-o>", lambda e: self._open_file())

        def _fill_recent_menu(self):
            self._recent_menu.delete(0, "end")
            recent = guiconfig.get_recent()
            if not recent:
                self._recent_menu.add_command(label="(none)", state="disabled")
                return
            for path in recent:
                exists = os.path.exists(path)
                label = path if exists else path + "   (missing)"
                self._recent_menu.add_command(
                    label=label, state="normal" if exists else "disabled",
                    command=(lambda pp=path: open_with_default_app(pp)))
            self._recent_menu.add_separator()
            self._recent_menu.add_command(label="Clear list",
                                          command=self._clear_recent)

        def _clear_recent(self):
            guiconfig.clear_recent()
            self._fill_recent_menu()

        def _open_file(self):
            p = filedialog.askopenfilename(title="Open media", filetypes=VIDEO_TYPES)
            if p:
                self.remember_input(p)
                self.show("convert")
                load = self._convert.get("load")
                if load:
                    load(p)

        # ---- ffmpeg presence note (header) + status dialog
        def _refresh_ffmpeg_note(self):
            if ffmpeg_mod.is_available():
                self._ffmpeg_note.configure(text="")
            else:
                self._ffmpeg_note.configure(
                    text="FFmpeg not found — install it, then reopen "
                         "(Help ▸ FFmpeg status).")

        def _show_ffmpeg_status(self):
            fp = ffmpeg_mod.find_binary("ffmpeg")
            pp = ffmpeg_mod.find_binary("ffprobe")
            if fp and pp:
                self.set_success(f"ffmpeg: {fp}   ·   ffprobe: {pp}")
            else:
                self.set_error(
                    f"ffmpeg: {fp or 'NOT FOUND'}   ·   "
                    f"ffprobe: {pp or 'NOT FOUND'}. Search order: "
                    "MEDIAKIT_FFMPEG_DIR, next to the app / bundled, PATH, then "
                    "common install directories.")
            self._refresh_ffmpeg_note()

        # ---- background operation runner (with progress + cancel)
        def _bg(self, work, on_ok, button=None, busy="Working…", progressive=True):
            if self._busy:
                self._show_error("Please wait — an operation is already running.")
                return
            if not ffmpeg_mod.is_available():
                self._show_error(
                    "FFmpeg was not found. See Help ▸ FFmpeg status for how to "
                    "install it.")
                self._refresh_ffmpeg_note()
                return
            self._busy = True
            self._cancel_event = threading.Event()
            if button is not None:
                try:
                    button.state(["disabled"])
                except Exception:
                    pass
            self._set_status(busy, kind="working")
            self._clear_result(keep_status=True)
            if progressive:
                self._prog.set(0)
                self._prog.pack(side="left", padx=(0, 8))
                self._cancel_btn.pack(side="left", padx=(0, 8))

            def report(frac):
                # marshalled onto the UI thread
                self.after(0, lambda: self._prog.set(max(0.0, min(1.0, frac))))

            def run():
                try:
                    res, err = work(report, self._cancel_event), None
                except MediaKitError as ex:
                    res, err = None, str(ex)
                except Exception as ex:
                    res, err = None, f"Unexpected error: {ex}"
                self.after(0, lambda: finish(res, err))

            def finish(res, err):
                self._busy = False
                self._cancel_event = None
                self._prog.pack_forget()
                self._cancel_btn.pack_forget()
                if button is not None:
                    try:
                        button.state(["!disabled"])
                    except Exception:
                        pass
                if err is not None:
                    low = err.lower()
                    if "cancel" in low:
                        self._set_status("Cancelled.", kind="idle")
                    else:
                        self._show_error(err)
                    return
                try:
                    on_ok(res)
                except Exception as ex:
                    self._show_error(f"Post-processing error: {ex}")

            threading.Thread(target=run, daemon=True).start()

        def _request_cancel(self):
            if self._cancel_event is not None:
                self._cancel_event.set()
                self._set_status("Cancelling…", kind="working")

        # ---- status / result helpers
        def _set_status(self, text, kind="idle"):
            self.set_status(text, kind)

        def _clear_result(self, keep_status=False):
            self._openfolder_btn.pack_forget()
            if not keep_status:
                self.set_status("Ready")

        def _show_error(self, message):
            self._openfolder_btn.pack_forget()
            self.set_error(message)

        def report_success(self, message, outputs=None):
            outputs = outputs or []
            for o in outputs:
                if o:
                    self._history.append(o)
                    guiconfig.add_recent(o)
            self._fill_recent_menu()
            if outputs:
                first = outputs[0]
                self._last_output_dir = (
                    first if os.path.isdir(first)
                    else os.path.dirname(os.path.abspath(first)))
                self._openfolder_btn.pack(side="left", padx=(8, 0))
            self.set_success(message)

        def _open_last_folder(self):
            if self._last_output_dir:
                open_in_file_manager(self._last_output_dir)

        def remember_input(self, path):
            if path:
                guiconfig.add_recent(path)
                self._fill_recent_menu()

        def _about(self):
            self.set_status(
                f"{APP_NAME} {APP_VERSION} — offline audio/video toolkit driving "
                "an external FFmpeg (LGPL/GPL). Apache-2.0. quickopen.ai")

        # ---- shared panel header (description caption)
        def _panel_desc(self, parent, tool_id):
            desc = next((d for t, _l, _g, d in TOOLS if t == tool_id), "")
            if desc:
                aura.Caption(parent, desc, wraplength=680,
                             justify="left").pack(anchor="w", pady=(0, 12))

        # =================================================================
        # Panels
        # =================================================================
        def _build_convert(self, parent):
            self._panel_desc(parent, "convert")
            src = FileRow(parent, self, "Input", on_change=self._on_convert_src)
            src.pack(fill="x", pady=4)

            info = ctk.CTkLabel(parent, justify="left", anchor="w",
                                font=aura.font(), text_color=aura.P("muted"),
                                text="(open a file to see its details)")
            info.pack(fill="x", pady=(2, 8), anchor="w")

            fmtbox = aura.Card(parent, title="Target")
            fmtbox.pack(fill="x", pady=6)
            grid = fmtbox.body
            ctk.CTkLabel(grid, text="Format", font=aura.font()).grid(
                row=0, column=0, sticky="w", padx=4, pady=4)
            fmt = tk.StringVar(value="mp4")
            aura.AuraCombo(grid, variable=fmt, width=110, state="readonly",
                           values=["mp4", "mkv", "mov", "webm", "avi",
                                   "mp3", "m4a", "wav", "flac"],
                           command=lambda _v: sync_out()).grid(
                row=0, column=1, sticky="w", padx=4, pady=4)
            ctk.CTkLabel(grid, text="Video codec", font=aura.font()).grid(
                row=0, column=2, sticky="w", padx=4, pady=4)
            vcodec = tk.StringVar(value="(auto)")
            aura.AuraCombo(grid, variable=vcodec, width=140, state="readonly",
                           values=["(auto)", "libx264", "libx265", "vp9",
                                   "copy"]).grid(
                row=0, column=3, sticky="w", padx=4, pady=4)
            ctk.CTkLabel(grid, text="Audio codec", font=aura.font()).grid(
                row=1, column=0, sticky="w", padx=4, pady=4)
            acodec = tk.StringVar(value="(auto)")
            aura.AuraCombo(grid, variable=acodec, width=140, state="readonly",
                           values=["(auto)", "aac", "libmp3lame", "libopus",
                                   "flac", "copy"]).grid(
                row=1, column=1, sticky="w", padx=4, pady=4)
            ctk.CTkLabel(grid, text="CRF (quality)", font=aura.font()).grid(
                row=1, column=2, sticky="w", padx=4, pady=4)
            crf = tk.StringVar(value="")
            ttk.Spinbox(grid, from_=0, to=51, textvariable=crf, width=6).grid(
                row=1, column=3, sticky="w", padx=4, pady=4)

            out = FileRow(parent, self, "Save as", mode="save",
                          filetypes=VIDEO_TYPES)
            out.pack(fill="x", pady=4)

            def sync_out(*_):
                inp = src.get()
                if inp:
                    out.set(_suggest_out(inp, "_converted", "." + fmt.get()))

            run = aura.AuraButton(parent, "Convert")
            run.pack(anchor="w", pady=8)

            def go():
                inp, dest = src.get(), out.get()
                if not inp or not dest:
                    self._show_error("Choose an input and an output file.")
                    return
                vc = None if vcodec.get() == "(auto)" else vcodec.get()
                ac = None if acodec.get() == "(auto)" else acodec.get()
                crf_val = None
                if crf.get().strip():
                    try:
                        crf_val = int(crf.get().strip())
                    except ValueError:
                        self._show_error("CRF must be a whole number.")
                        return
                self._bg(
                    lambda rep, cx: run_convert(
                        inp, dest, vcodec=vc, acodec=ac, crf=crf_val,
                        on_progress=rep, cancel=cx.is_set),
                    lambda r: self.report_success(f"Converted → {dest}", [dest]),
                    button=run, busy="Converting…")

            run.configure(command=go)

            # handles for _open_file / _on_convert_src
            self._convert = {"src": src, "info": info, "out": out, "fmt": fmt,
                             "load": src.set}

        def _on_convert_src(self, value):
            if value:
                self.remember_input(value)
            info = self._convert.get("info")
            out = self._convert.get("out")
            fmt = self._convert.get("fmt")
            if out and fmt and value:
                out.set(_suggest_out(value, "_converted", "." + fmt.get()))
            if info is None or not value:
                return
            if not os.path.isfile(value) or not ffmpeg_mod.is_available():
                info.configure(text="(open a file to see its details)")
                return

            def work(rep, cx):
                return ffmpeg_mod.probe(value)

            def ok(pr):
                dur = human_duration(pr.get("duration"))
                res = pr.get("resolution") or "-"
                size = human_size(pr.get("size")) if pr.get("size") else "?"
                info.configure(
                    text=(f"Duration {dur}   Resolution {res}   Size {size}\n"
                          f"Video {pr.get('vcodec') or '-'}   "
                          f"Audio {pr.get('acodec') or '-'}   "
                          f"Format {pr.get('format_name') or '-'}"))
            # probe quietly without the progress bar
            self._bg(work, ok, progressive=False)

        def _build_trim(self, parent):
            self._panel_desc(parent, "trim")
            src = FileRow(parent, self, "Input", on_change=lambda v: (
                self.remember_input(v),
                out.set(_suggest_out(v, "_trim"))))
            src.pack(fill="x", pady=4)
            grid = ctk.CTkFrame(parent, fg_color="transparent")
            grid.pack(fill="x", pady=4)
            ctk.CTkLabel(grid, text="Start", width=64, anchor="w",
                         font=aura.font()).grid(row=0, column=0)
            start = tk.StringVar()
            aura.AuraEntry(grid, textvariable=start, width=140).grid(
                row=0, column=1, padx=4)
            ctk.CTkLabel(grid, text="End", width=48, anchor="w",
                         font=aura.font()).grid(row=0, column=2, padx=(14, 0))
            end = tk.StringVar()
            aura.AuraEntry(grid, textvariable=end, width=140).grid(
                row=0, column=3, padx=4)
            aura.Caption(
                parent,
                "Times are seconds (12.5) or HH:MM:SS (00:01:30). Leave one "
                "blank to trim only from the other end.", wraplength=680,
                justify="left").pack(anchor="w")
            reenc = tk.BooleanVar(value=False)
            ctk.CTkCheckBox(
                parent, variable=reenc, font=aura.font(),
                text="Re-encode (frame-accurate; slower). Off = stream-copy "
                     "(instant, cuts on keyframes).").pack(anchor="w", pady=8)
            out = FileRow(parent, self, "Save as", mode="save")
            out.pack(fill="x", pady=4)
            run = aura.AuraButton(parent, "Trim")
            run.pack(anchor="w", pady=8)

            def go():
                inp, dest = src.get(), out.get()
                if not inp or not dest:
                    self._show_error("Choose an input and an output file.")
                    return
                s = start.get().strip() or None
                e = end.get().strip() or None
                if not s and not e:
                    self._show_error("Enter a start and/or an end time.")
                    return
                self._bg(
                    lambda rep, cx: edit_mod.trim_run(
                        inp, dest, s, e, reencode=reenc.get(),
                        on_progress=rep, cancel=cx.is_set),
                    lambda r: self.report_success(f"Trimmed → {dest}", [dest]),
                    button=run, busy="Trimming…")

            run.configure(command=go)

        def _build_compress(self, parent):
            self._panel_desc(parent, "compress")
            src = FileRow(parent, self, "Input", on_change=lambda v: (
                self.remember_input(v),
                out.set(_suggest_out(v, "_compressed", ".mp4"))))
            src.pack(fill="x", pady=4)
            box = aura.Card(parent, title="Level")
            box.pack(fill="x", pady=6)
            level = tk.StringVar(value="medium")
            for lv, txt in (("low", "Low (best quality, larger)"),
                            ("medium", "Medium"),
                            ("high", "High (smallest, lower quality)")):
                ctk.CTkRadioButton(box.body, text=txt, value=lv, variable=level,
                                   font=aura.font()).pack(anchor="w", pady=3)
            out = FileRow(parent, self, "Save as", mode="save", defaultext=".mp4")
            out.pack(fill="x", pady=4)
            run = aura.AuraButton(parent, "Compress")
            run.pack(anchor="w", pady=8)

            def go():
                inp, dest = src.get(), out.get()
                if not inp or not dest:
                    self._show_error("Choose an input and an output file.")
                    return
                self._bg(
                    lambda rep, cx: edit_mod.compress_run(
                        inp, dest, level=level.get(),
                        on_progress=rep, cancel=cx.is_set),
                    lambda r: self.report_success(
                        f"Compressed ({level.get()}) → {dest}", [dest]),
                    button=run, busy="Compressing…")

            run.configure(command=go)

        def _build_extractaudio(self, parent):
            self._panel_desc(parent, "extractaudio")
            src = FileRow(parent, self, "Input", on_change=lambda v: (
                self.remember_input(v),
                out.set(_suggest_out(v, "", ".mp3"))))
            src.pack(fill="x", pady=4)
            fbox = ctk.CTkFrame(parent, fg_color="transparent")
            fbox.pack(fill="x", pady=4)
            ctk.CTkLabel(fbox, text="Codec", width=64, anchor="w",
                         font=aura.font()).pack(side="left")
            acodec = tk.StringVar(value="(auto from extension)")
            aura.AuraCombo(fbox, variable=acodec, width=240, state="readonly",
                           values=["(auto from extension)", "libmp3lame", "aac",
                                   "libopus", "libvorbis", "flac", "pcm_s16le",
                                   "copy"]).pack(side="left")
            out = FileRow(parent, self, "Save as", mode="save",
                          filetypes=AUDIO_OUT_TYPES, defaultext=".mp3")
            out.pack(fill="x", pady=4)
            run = aura.AuraButton(parent, "Extract audio")
            run.pack(anchor="w", pady=8)

            def go():
                inp, dest = src.get(), out.get()
                if not inp or not dest:
                    self._show_error("Choose an input and an output file.")
                    return
                ac = acodec.get()
                ac = None if ac.startswith("(auto") else ac
                self._bg(
                    lambda rep, cx: edit_mod.extract_audio_run(
                        inp, dest, acodec=ac, on_progress=rep, cancel=cx.is_set),
                    lambda r: self.report_success(f"Audio → {dest}", [dest]),
                    button=run, busy="Extracting…")

            run.configure(command=go)

        def _build_gif(self, parent):
            self._panel_desc(parent, "gif")
            src = FileRow(parent, self, "Input", on_change=lambda v: (
                self.remember_input(v),
                out.set(_suggest_out(v, "", ".gif"))))
            src.pack(fill="x", pady=4)
            grid = ctk.CTkFrame(parent, fg_color="transparent")
            grid.pack(fill="x", pady=4)
            ctk.CTkLabel(grid, text="FPS", font=aura.font()).grid(
                row=0, column=0, padx=4, pady=4, sticky="w")
            fps = tk.StringVar(value="12")
            ttk.Spinbox(grid, from_=1, to=50, textvariable=fps, width=6).grid(
                row=0, column=1, padx=4, pady=4, sticky="w")
            ctk.CTkLabel(grid, text="Width", font=aura.font()).grid(
                row=0, column=2, padx=(14, 4), pady=4, sticky="w")
            width = tk.StringVar(value="480")
            ttk.Spinbox(grid, from_=16, to=1920, increment=16, textvariable=width,
                        width=8).grid(row=0, column=3, padx=4, pady=4, sticky="w")
            ctk.CTkLabel(grid, text="Start", font=aura.font()).grid(
                row=1, column=0, padx=4, pady=4, sticky="w")
            start = tk.StringVar()
            aura.AuraEntry(grid, textvariable=start, width=90).grid(
                row=1, column=1, padx=4, pady=4, sticky="w")
            ctk.CTkLabel(grid, text="Duration", font=aura.font()).grid(
                row=1, column=2, padx=(14, 4), pady=4, sticky="w")
            duration = tk.StringVar()
            aura.AuraEntry(grid, textvariable=duration, width=90).grid(
                row=1, column=3, padx=4, pady=4, sticky="w")

            prevrow = ctk.CTkFrame(parent, fg_color="transparent")
            prevrow.pack(fill="x", pady=6)
            aura.AuraButton(
                prevrow, "Preview first frame", kind="secondary",
                command=lambda: self._gif_preview(src.get(), start.get(),
                                                  preview)).pack(side="left")
            preview = ctk.CTkLabel(prevrow, text="(no preview yet)",
                                   font=aura.font(),
                                   text_color=aura.P("muted"))
            preview.pack(side="left", padx=12)

            out = FileRow(parent, self, "Save as", mode="save",
                          filetypes=[("GIF", "*.gif")], defaultext=".gif")
            out.pack(fill="x", pady=4)
            run = aura.AuraButton(parent, "Make GIF")
            run.pack(anchor="w", pady=8)

            def go():
                inp, dest = src.get(), out.get()
                if not inp or not dest:
                    self._show_error("Choose an input and an output file.")
                    return
                try:
                    fps_v = int(fps.get())
                    width_v = int(width.get())
                except ValueError:
                    self._show_error("FPS and width must be whole numbers.")
                    return
                s = start.get().strip() or None
                d = duration.get().strip() or None
                self._bg(
                    lambda rep, cx: edit_mod.gif_run(
                        inp, dest, fps=fps_v, width=width_v, start=s, duration=d,
                        on_progress=rep, cancel=cx.is_set),
                    lambda r: self.report_success(f"GIF → {dest}", [dest]),
                    button=run, busy="Rendering GIF…")

            run.configure(command=go)

        def _gif_preview(self, inp, start, label):
            if not inp or not os.path.isfile(inp):
                self._show_error("Choose an input file first.")
                return
            if not ffmpeg_mod.is_available():
                self._show_error("FFmpeg not found — cannot render a preview.")
                return
            try:
                from PIL import Image, ImageTk
            except Exception:
                label.configure(text="(Pillow not available for preview)")
                return
            png = os.path.join(self._tmpdir, "preview.png")

            def work(rep, cx):
                edit_mod.thumbnail_run(inp, png, at=(start.strip() or None),
                                       width=240)
                return png

            def ok(path):
                try:
                    img = Image.open(path)
                    img.thumbnail((240, 160))
                    photo = ImageTk.PhotoImage(img)
                    self._img_refs_gui.append(photo)
                    label.configure(image=photo, text="")
                except Exception as ex:
                    label.configure(text=f"(preview failed: {ex})")

            self._bg(work, ok, progressive=False)

        def _build_subtitles(self, parent):
            self._panel_desc(parent, "subtitles")
            src = FileRow(parent, self, "Input video", on_change=lambda v: (
                self.remember_input(v),
                out.set(_suggest_out(v, "_subbed"))))
            src.pack(fill="x", pady=4)
            subs = FileRow(parent, self, "Subtitles", mode="open",
                           filetypes=SUB_TYPES)
            subs.pack(fill="x", pady=4)
            aura.Caption(
                parent,
                "Burns the .srt/.ass permanently into the picture (re-encodes "
                "the video; audio is copied).", wraplength=680,
                justify="left").pack(anchor="w")
            out = FileRow(parent, self, "Save as", mode="save")
            out.pack(fill="x", pady=4)
            run = aura.AuraButton(parent, "Burn subtitles")
            run.pack(anchor="w", pady=8)

            def go():
                inp, dest, sp = src.get(), out.get(), subs.get()
                if not inp or not dest or not sp:
                    self._show_error("Choose a video, a subtitle file and an output.")
                    return
                self._bg(
                    lambda rep, cx: edit_mod.subtitle_burn_run(
                        inp, dest, sp, on_progress=rep, cancel=cx.is_set),
                    lambda r: self.report_success(f"Subtitles burned → {dest}",
                                                  [dest]),
                    button=run, busy="Burning subtitles…")

            run.configure(command=go)

        def _build_batch(self, parent):
            self._panel_desc(parent, "batch")
            aura.SectionLabel(parent, "Files").pack(anchor="w")
            flist = FileList(parent, self)
            flist.pack(fill="both", expand=True, pady=(4, 10))

            opts = aura.Card(parent, title="Options")
            opts.pack(fill="x", pady=4)
            grid = opts.body
            ctk.CTkLabel(grid, text="Operation", font=aura.font()).grid(
                row=0, column=0, sticky="w", padx=4, pady=4)
            op = tk.StringVar(value="compress")
            aura.AuraCombo(grid, variable=op, width=160, state="readonly",
                           values=["convert", "compress", "extract-audio",
                                   "gif"]).grid(
                row=0, column=1, sticky="w", padx=4, pady=4)
            ctk.CTkLabel(grid, text="Level", font=aura.font()).grid(
                row=0, column=2, sticky="w", padx=(14, 4), pady=4)
            level = tk.StringVar(value="medium")
            aura.AuraCombo(grid, variable=level, width=120, state="readonly",
                           values=sorted(COMPRESS_LEVELS)).grid(
                row=0, column=3, sticky="w", padx=4, pady=4)
            ctk.CTkLabel(grid, text="Output ext", font=aura.font()).grid(
                row=1, column=0, sticky="w", padx=4, pady=4)
            ext = tk.StringVar(value="")
            aura.AuraEntry(grid, textvariable=ext, width=120).grid(
                row=1, column=1, sticky="w", padx=4, pady=4)
            aura.Caption(grid, "(blank = sensible default per op)").grid(
                row=1, column=2, columnspan=2, sticky="w", padx=(14, 4), pady=4)

            out = FileRow(parent, self, "Output folder", mode="dir")
            out.pack(fill="x", pady=4)
            run = aura.AuraButton(parent, "Run batch")
            run.pack(anchor="w", pady=8)

            def go():
                inputs = flist.items()
                out_dir = out.get()
                if not inputs:
                    self._show_error("Add at least one input file.")
                    return
                if not out_dir:
                    self._show_error("Choose an output folder.")
                    return
                ex = ext.get().strip() or None
                if ex and not ex.startswith("."):
                    ex = "." + ex

                def on_item(idx, total, path):
                    self.after(0, lambda: self._set_status(
                        f"{idx + 1}/{total}…", kind="working"))

                def work(rep, cx):
                    return batch_mod.batch_apply(
                        op.get(), inputs, out_dir, out_ext=ex, level=level.get(),
                        on_progress=rep, on_item=on_item, cancel=cx.is_set)

                def ok(results):
                    good = sum(1 for _i, o, _e in results if o)
                    fail = len(results) - good
                    msg = f"{good} succeeded, {fail} failed → {out_dir}"
                    if fail:
                        first_err = next((e for _i, _o, e in results if e), "")
                        msg += f"  (e.g. {first_err})"
                    self.report_success(msg, [out_dir])

                self._bg(work, ok, button=run, busy="Batch running…")

            run.configure(command=go)

        # ---- shutdown
        def _on_close(self):
            try:
                import shutil
                shutil.rmtree(self._tmpdir, ignore_errors=True)
            except Exception:
                pass
            self.destroy()

    return App


def main():
    """Entry point: build the root window and run.  Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (e.g. a server) or without customtkinter installed, it
    prints a friendly note and returns 0 instead of raising.
    """
    try:
        import tkinter as tk
    except Exception as exc:  # tkinter missing entirely
        print(f"{APP_NAME}: a graphical environment with tkinter is required "
              f"to run the GUI ({exc}).")
        return 0

    try:
        App = build_app()
        app = App()
    except ImportError as exc:
        print(f"{APP_NAME}: the GUI needs the 'customtkinter' package "
              f"({exc}). Install it with:  pip install customtkinter")
        return 0
    except tk.TclError as exc:
        print(f"{APP_NAME}: no graphical display available — cannot start the "
              f"GUI here ({exc}). This app is intended for the desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
