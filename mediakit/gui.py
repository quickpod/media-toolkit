#!/usr/bin/env python3
r"""Media Toolkit -- a pure-stdlib tkinter GUI on top of the ``mediakit`` API.

A single main window: a left sidebar of tools (Convert, Trim, Compress, Extract
Audio, GIF, Subtitles, Batch) and a main panel that swaps to the selected tool.
Every operation calls the tested core library, runs on a background thread so the
UI stays responsive, drives a live progress bar from FFmpeg's ``-progress``
stream, and can be cancelled.  Results are reported inline -- an output path plus
an "Open folder" button on success, or the :class:`MediaKitError` message (never
a raw traceback) on failure.

Design goals mirror the sibling PDF Toolkit:
  * pure standard-library tkinter/ttk plus Pillow (only for the GIF preview).
    NO third-party GUI deps.  Dark mode is a ttk-style + palette swap.
  * Importing this module does nothing.  Only :func:`main` builds a root window,
    and it degrades gracefully (prints a note, returns 0) with no display.
  * Frozen-exe safe: bundled assets resolved via ``sys._MEIPASS`` / the exe dir.
  * FFmpeg is external.  If it can't be found, a banner explains how to install
    it, but the UI stays usable.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading

# tkinter is imported lazily inside main()/build_app so that merely importing
# this module (e.g. on a headless CI box) never fails.

APP_NAME = "Media Toolkit"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "Media Toolkit — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"

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

# (tool_id, label) -- tool_id maps to a _panel_<id> method.
TOOL_LIST = [
    ("convert", "Convert"),
    ("trim", "Trim / Cut"),
    ("compress", "Compress"),
    ("extractaudio", "Extract Audio"),
    ("gif", "GIF"),
    ("subtitles", "Subtitles"),
    ("batch", "Batch"),
]

TOOL_DESCRIPTIONS = {
    "convert": "Open a media file, inspect it, then convert to another "
               "container/codec with a quality knob.",
    "trim": "Cut a [start, end] span out. Stream-copy for an instant cut, or "
            "re-encode for a frame-accurate one.",
    "compress": "Re-encode smaller with a low / medium / high preset.",
    "extractaudio": "Pull the audio track out to MP3, M4A, WAV and friends.",
    "gif": "Turn a span of video into a GIF (fps / width / start / duration), "
           "with a first-frame preview.",
    "subtitles": "Burn a .srt/.ass subtitle file permanently into the video.",
    "batch": "Apply convert / compress / extract-audio / gif across many files "
             "into an output folder.",
}

# ---- colour palettes (mirror the QuickOpen palette) -------------------------
PALETTES = {
    "light": {
        "bg": "#f5f7fa", "surface": "#ffffff", "text": "#141820",
        "muted": "#5b6472", "primary": "#2f5fe0", "primary_hi": "#2450c8",
        "entry": "#ffffff", "border": "#d5dae2", "sel": "#2f5fe0",
        "sel_fg": "#ffffff", "trough": "#e2e7ef", "ok": "#1f7a3d",
        "err": "#c0392b", "warn": "#8a6d00", "warnbg": "#fff5d6",
    },
    "dark": {
        "bg": "#0f1115", "surface": "#1a1e24", "text": "#f1f3f7",
        "muted": "#9aa4b2", "primary": "#5b86f7", "primary_hi": "#7098ff",
        "entry": "#1a1e24", "border": "#2a2f38", "sel": "#5b86f7",
        "sel_fg": "#0f1115", "trough": "#2a2f38", "ok": "#5bd68a",
        "err": "#ff6b5e", "warn": "#ffd166", "warnbg": "#2a2410",
    },
}


# ---------------------------------------------------------------------------
# Asset / frozen handling
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
# The app (built lazily; tkinter imported only inside build_app/main)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to a live tkinter import."""
    import tkinter as tk
    from tkinter import ttk, filedialog

    from . import guiconfig
    from .errors import MediaKitError
    from . import ffmpeg as ffmpeg_mod
    from . import convert as convert_mod
    from . import edit as edit_mod
    from . import batch as batch_mod
    from .edit import COMPRESS_LEVELS

    FONT = "Segoe UI"

    # -- small reusable widgets ------------------------------------------
    class FileRow(ttk.Frame):
        """A labelled path field + Browse button. ``mode`` picks the dialog."""

        def __init__(self, master, app, label, mode="open", filetypes=None,
                     on_change=None, defaultext=None):
            super().__init__(master, style="TFrame")
            self.app = app
            self.mode = mode
            self.filetypes = filetypes or VIDEO_TYPES
            self.defaultext = defaultext
            self.var = tk.StringVar()
            ttk.Label(self, text=label, width=14, anchor="w").pack(side="left")
            ent = ttk.Entry(self, textvariable=self.var)
            ent.pack(side="left", fill="x", expand=True, padx=(0, 6))
            ttk.Button(self, text="Browse…", command=self._browse,
                       width=10).pack(side="left")
            if on_change:
                self.var.trace_add("write", lambda *_: on_change(self.var.get()))

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
                self.var.set(p)

        def get(self):
            return self.var.get().strip()

        def set(self, value):
            self.var.set(value or "")

    class FileList(ttk.Frame):
        """A multi-file listbox with Add / Remove / Up / Down / Clear."""

        def __init__(self, master, app, filetypes=None):
            super().__init__(master, style="TFrame")
            self.app = app
            self.filetypes = filetypes or VIDEO_TYPES
            box = ttk.Frame(self, style="TFrame")
            box.pack(fill="both", expand=True)
            self.listbox = tk.Listbox(box, height=8, activestyle="none",
                                      selectmode="extended", exportselection=False)
            sb = ttk.Scrollbar(box, orient="vertical", command=self.listbox.yview)
            self.listbox.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.listbox.pack(side="left", fill="both", expand=True)
            app.track(self.listbox, "listbox")

            btns = ttk.Frame(self, style="TFrame")
            btns.pack(fill="x", pady=(6, 0))
            ttk.Button(btns, text="Add files…", command=self.add,
                       style="Accent.TButton").pack(side="left")
            ttk.Button(btns, text="Remove", command=self.remove).pack(
                side="left", padx=4)
            ttk.Button(btns, text="Clear", command=self.clear).pack(side="left")

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
    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(WINDOW_TITLE)
            self.geometry("1040x680")
            self.minsize(860, 560)

            self.theme = guiconfig.get_theme()
            self._busy = False
            self._cancel_event = None
            self._panels = {}
            self._current = None
            self._tracked = []
            self._img_refs = []
            self._history = []
            self._last_output_dir = None
            self._tmpdir = tempfile.mkdtemp(prefix="mediatk_gui_")

            self._set_icon()
            self._build_menu()
            self._build_layout()
            self._apply_theme()
            self._refresh_ffmpeg_banner()
            self.protocol("WM_DELETE_WINDOW", self._on_close)
            self.after(50, self._select_first_tool)

        # ---- assets / icon
        def _set_icon(self):
            try:
                ico = asset_path("media-toolkit.ico")
                if ico:
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("media-toolkit.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass

        # ---- theming
        def track(self, widget, role):
            self._tracked.append((widget, role))

        def _pal(self):
            return PALETTES[self.theme]

        def _apply_theme(self):
            p = self._pal()
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            self.configure(bg=p["bg"])
            style.configure(".", background=p["bg"], foreground=p["text"],
                            fieldbackground=p["entry"], bordercolor=p["border"],
                            font=(FONT, 10))
            style.configure("TFrame", background=p["bg"])
            style.configure("Sidebar.TFrame", background=p["surface"])
            style.configure("Card.TFrame", background=p["surface"])
            style.configure("TLabel", background=p["bg"], foreground=p["text"])
            style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
            style.configure("Header.TLabel", background=p["bg"], foreground=p["text"],
                            font=(FONT, 15, "bold"))
            style.configure("Sub.TLabel", background=p["bg"], foreground=p["muted"],
                            font=(FONT, 10))
            style.configure("Brand.TLabel", background=p["surface"],
                            foreground=p["text"], font=(FONT, 12, "bold"))
            style.configure("Ok.TLabel", background=p["bg"], foreground=p["ok"])
            style.configure("Err.TLabel", background=p["bg"], foreground=p["err"])
            style.configure("Status.TLabel", background=p["surface"],
                            foreground=p["muted"])
            style.configure("Warn.TLabel", background=p["warnbg"],
                            foreground=p["warn"], padding=(10, 6))
            style.configure("Info.TLabel", background=p["surface"],
                            foreground=p["text"], font=("Consolas", 10))
            style.configure("TButton", background=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], focuscolor=p["surface"],
                            padding=(10, 5))
            style.map("TButton",
                      background=[("active", p["trough"]), ("disabled", p["bg"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Accent.TButton", background=p["primary"],
                            foreground="#ffffff", padding=(12, 6))
            style.map("Accent.TButton",
                      background=[("active", p["primary_hi"]),
                                  ("disabled", p["border"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Toggle.TButton", background=p["surface"],
                            foreground=p["text"], padding=(8, 4))
            style.configure("Nav.TButton", background=p["surface"],
                            foreground=p["text"], anchor="w", padding=(12, 8),
                            font=(FONT, 11))
            style.map("Nav.TButton",
                      background=[("active", p["trough"])])
            style.configure("NavActive.TButton", background=p["primary"],
                            foreground="#ffffff", anchor="w", padding=(12, 8),
                            font=(FONT, 11, "bold"))
            style.map("NavActive.TButton", background=[("active", p["primary_hi"])])
            for name in ("TEntry", "TSpinbox"):
                style.configure(name, fieldbackground=p["entry"], foreground=p["text"],
                                insertcolor=p["text"], bordercolor=p["border"])
            style.configure("TCombobox", fieldbackground=p["entry"],
                            foreground=p["text"], background=p["surface"],
                            arrowcolor=p["text"])
            style.map("TCombobox",
                      fieldbackground=[("readonly", p["entry"])],
                      foreground=[("readonly", p["text"])])
            style.configure("TCheckbutton", background=p["bg"], foreground=p["text"])
            style.map("TCheckbutton", background=[("active", p["bg"])])
            style.configure("TRadiobutton", background=p["bg"], foreground=p["text"])
            style.map("TRadiobutton", background=[("active", p["bg"])])
            style.configure("TLabelframe", background=p["bg"], foreground=p["text"],
                            bordercolor=p["border"])
            style.configure("TLabelframe.Label", background=p["bg"],
                            foreground=p["muted"])
            style.configure("TScrollbar", background=p["surface"],
                            troughcolor=p["bg"], bordercolor=p["border"],
                            arrowcolor=p["text"])
            style.configure("TSeparator", background=p["border"])
            style.configure("Horizontal.TProgressbar", background=p["primary"],
                            troughcolor=p["trough"], bordercolor=p["border"])

            for widget, role in list(self._tracked):
                try:
                    if role == "listbox":
                        widget.configure(bg=p["surface"], fg=p["text"],
                                         selectbackground=p["primary"],
                                         selectforeground=p["sel_fg"],
                                         highlightthickness=1,
                                         highlightbackground=p["border"],
                                         borderwidth=0)
                    elif role == "text":
                        widget.configure(bg=p["surface"], fg=p["text"],
                                         insertbackground=p["text"],
                                         selectbackground=p["primary"],
                                         selectforeground=p["sel_fg"],
                                         highlightthickness=1,
                                         highlightbackground=p["border"],
                                         borderwidth=0)
                    elif role == "canvas":
                        widget.configure(bg=p["surface"], highlightthickness=1,
                                         highlightbackground=p["border"])
                except Exception:
                    pass
            # re-tint nav buttons
            if hasattr(self, "_nav_btns"):
                self._restyle_nav()

        def toggle_theme(self):
            self.theme = "dark" if self.theme == "light" else "light"
            guiconfig.set_theme(self.theme)
            self._apply_theme()
            self._theme_btn.configure(
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")

        # ---- menu
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
            viewm.add_command(label="Toggle dark mode", command=self.toggle_theme)
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="FFmpeg status…", command=self._show_ffmpeg_status)
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
                self._select_tool("convert")
                panel = self._panels.get("convert")
                if panel and hasattr(panel, "load_path"):
                    panel.load_path(p)

        # ---- layout
        def _build_layout(self):
            top = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 8))
            top.pack(fill="x", side="top")
            ttk.Label(top, text="Media Toolkit", style="Brand.TLabel").pack(side="left")
            ttk.Label(top, style="Status.TLabel",
                      text="  offline · open source · by QuickOpen").pack(side="left")
            self._theme_btn = ttk.Button(
                top, style="Toggle.TButton", command=self.toggle_theme,
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")
            self._theme_btn.pack(side="right")

            # ffmpeg-missing banner (packed/hidden on demand)
            self.banner = ttk.Label(self, style="Warn.TLabel", wraplength=1000,
                                    justify="left", text="")

            body = ttk.Frame(self, style="TFrame")
            body.pack(fill="both", expand=True)
            self._body_frame = body

            side = ttk.Frame(body, style="Sidebar.TFrame", width=200)
            side.pack(side="left", fill="y")
            side.pack_propagate(False)
            self._nav_btns = {}
            for tid, label in TOOL_LIST:
                b = ttk.Button(side, text=label, style="Nav.TButton",
                               command=lambda t=tid: self._select_tool(t))
                b.pack(fill="x", padx=6, pady=2)
                self._nav_btns[tid] = b

            main = ttk.Frame(body, style="TFrame", padding=(16, 12))
            main.pack(side="left", fill="both", expand=True)

            head = ttk.Frame(main, style="TFrame")
            head.pack(fill="x")
            self.title_lbl = ttk.Label(head, text="Welcome", style="Header.TLabel")
            self.title_lbl.pack(anchor="w")
            self.desc_lbl = ttk.Label(head, text="", style="Sub.TLabel",
                                      wraplength=720, justify="left")
            self.desc_lbl.pack(anchor="w", pady=(2, 8))
            ttk.Separator(main).pack(fill="x")

            self.container = ttk.Frame(main, style="TFrame")
            self.container.pack(fill="both", expand=True, pady=(10, 8))

            # progress + result bar
            bar = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 6))
            bar.pack(fill="x", side="bottom")
            self.status_lbl = ttk.Label(bar, text="Ready", style="Status.TLabel",
                                        width=14, anchor="w")
            self.status_lbl.pack(side="left")
            self.progress = ttk.Progressbar(bar, mode="determinate", length=180,
                                             maximum=1000)
            self.cancel_btn = ttk.Button(bar, text="Cancel", command=self._request_cancel)
            self.openfolder_btn = ttk.Button(bar, text="Open folder",
                                             command=self._open_last_folder)
            self.result_lbl = ttk.Label(bar, text="", style="Status.TLabel",
                                        anchor="w", wraplength=560, justify="left")
            self.result_lbl.pack(side="left", fill="x", expand=True, padx=8)

        def _restyle_nav(self):
            for tid, b in self._nav_btns.items():
                active = (self._current_tool == tid) if hasattr(self, "_current_tool") else False
                b.configure(style="NavActive.TButton" if active else "Nav.TButton")

        def _select_first_tool(self):
            self._select_tool(TOOL_LIST[0][0])

        def _select_tool(self, tool_id):
            self._current_tool = tool_id
            self._show_tool(tool_id)
            self._restyle_nav()

        def _show_tool(self, tool_id):
            if self._current is not None:
                self._current.pack_forget()
            panel = self._panels.get(tool_id)
            if panel is None:
                panel = ttk.Frame(self.container, style="TFrame")
                builder = getattr(self, "_panel_" + tool_id, None)
                if builder:
                    builder(panel)
                else:
                    ttk.Label(panel, text="Not implemented.").pack()
                self._panels[tool_id] = panel
                self._apply_theme()
            panel.pack(fill="both", expand=True)
            self._current = panel
            title, desc = self._tool_meta(tool_id)
            self.title_lbl.configure(text=title)
            self.desc_lbl.configure(text=desc)
            self._clear_result()

        def _tool_meta(self, tool_id):
            for tid, label in TOOL_LIST:
                if tid == tool_id:
                    return label, TOOL_DESCRIPTIONS.get(tid, "")
            return tool_id, ""

        # ---- ffmpeg banner
        def _refresh_ffmpeg_banner(self):
            if ffmpeg_mod.is_available():
                try:
                    self.banner.pack_forget()
                except Exception:
                    pass
            else:
                self.banner.configure(
                    text="⚠ FFmpeg was not found. The Windows installer bundles "
                         "ffmpeg.exe next to the app automatically. Running from "
                         "source? Install FFmpeg from ffmpeg.org and put ffmpeg.exe "
                         "next to the app or on your PATH, then reopen. You can "
                         "browse and set up operations meanwhile — they will run "
                         "once FFmpeg is available.")
                try:
                    self.banner.pack(fill="x", side="top", before=self._body_frame)
                except Exception:
                    self.banner.pack(fill="x", side="top")

        def _show_ffmpeg_status(self):
            from tkinter import messagebox
            fp = ffmpeg_mod.find_binary("ffmpeg")
            pp = ffmpeg_mod.find_binary("ffprobe")
            msg = (f"ffmpeg:  {fp or 'NOT FOUND'}\n"
                   f"ffprobe: {pp or 'NOT FOUND'}\n\n"
                   "Search order: MEDIAKIT_FFMPEG_DIR, next to the app / bundled, "
                   "PATH, then common install directories.")
            messagebox.showinfo("FFmpeg status", msg)

        # ---- background operation runner (with progress + cancel)
        def _bg(self, work, on_ok, button=None, busy="Working…", progressive=True):
            if self._busy:
                self._show_error("Please wait — an operation is already running.")
                return
            if not ffmpeg_mod.is_available():
                self._show_error(
                    "FFmpeg was not found. See the banner / Help ▸ FFmpeg status "
                    "for how to install it.")
                self._refresh_ffmpeg_banner()
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
                self.progress.configure(value=0)
                self.progress.pack(side="left", padx=(0, 6))
                self.cancel_btn.pack(side="left", padx=(0, 6))

            def report(frac):
                # marshalled onto the UI thread
                self.after(0, lambda: self.progress.configure(value=int(frac * 1000)))

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
                self.progress.pack_forget()
                self.cancel_btn.pack_forget()
                if button is not None:
                    try:
                        button.state(["!disabled"])
                    except Exception:
                        pass
                if err is not None:
                    low = err.lower()
                    if "cancel" in low:
                        self._set_status("cancelled", kind="idle")
                        self._show_error("Cancelled.")
                    else:
                        self._set_status("error", kind="err")
                        self._show_error(err)
                    return
                self._set_status("done", kind="ok")
                try:
                    on_ok(res)
                except Exception as ex:
                    self._show_error(f"Post-processing error: {ex}")

            threading.Thread(target=run, daemon=True).start()

        def _request_cancel(self):
            if self._cancel_event is not None:
                self._cancel_event.set()
                self._set_status("cancelling…", kind="working")

        # ---- result bar helpers
        def _set_status(self, text, kind="idle"):
            p = self._pal()
            color = {"working": p["primary"], "ok": p["ok"], "err": p["err"]}.get(
                kind, p["muted"])
            self.status_lbl.configure(text=text, foreground=color)

        def _clear_result(self, keep_status=False):
            self.result_lbl.configure(text="")
            self.openfolder_btn.pack_forget()
            if not keep_status:
                self._set_status("Ready")

        def _show_error(self, message):
            self.result_lbl.configure(text="✕ " + message,
                                      foreground=self._pal()["err"])
            self.openfolder_btn.pack_forget()

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
                self.openfolder_btn.pack(side="right")
            self.result_lbl.configure(text="✓ " + message,
                                      foreground=self._pal()["ok"])
            self._set_status("done", kind="ok")

        def _open_last_folder(self):
            if self._last_output_dir:
                open_in_file_manager(self._last_output_dir)

        def remember_input(self, path):
            if path:
                guiconfig.add_recent(path)
                self._fill_recent_menu()

        def _about(self):
            from tkinter import messagebox
            messagebox.showinfo(
                "About Media Toolkit",
                f"{APP_NAME} {APP_VERSION}\n\nAn offline audio/video toolkit that "
                "drives an external FFmpeg (LGPL/GPL), invoked as a subprocess.\n\n"
                "100% AI-built, open source (Apache-2.0), published on QuickOpen.\n"
                "quickopen.ai")

        # =================================================================
        # Panels
        # =================================================================
        def _panel_convert(self, parent):
            src = FileRow(parent, self, "Input", on_change=self._on_convert_src)
            src.pack(fill="x", pady=4)

            info = ttk.Label(parent, style="Info.TLabel", justify="left",
                             text="(open a file to see its details)")
            info.pack(fill="x", pady=(2, 8))

            fmtbox = ttk.Labelframe(parent, text="Target", padding=8)
            fmtbox.pack(fill="x", pady=6)
            ttk.Label(fmtbox, text="Format").grid(row=0, column=0, sticky="w", padx=4)
            fmt = tk.StringVar(value="mp4")
            ttk.Combobox(fmtbox, textvariable=fmt, width=8, state="readonly",
                         values=["mp4", "mkv", "mov", "webm", "avi",
                                 "mp3", "m4a", "wav", "flac"]).grid(
                row=0, column=1, sticky="w", padx=4)
            ttk.Label(fmtbox, text="Video codec").grid(row=0, column=2, sticky="w", padx=4)
            vcodec = tk.StringVar(value="(auto)")
            ttk.Combobox(fmtbox, textvariable=vcodec, width=12, state="readonly",
                         values=["(auto)", "libx264", "libx265", "vp9", "copy"]).grid(
                row=0, column=3, sticky="w", padx=4)
            ttk.Label(fmtbox, text="Audio codec").grid(row=1, column=0, sticky="w", padx=4)
            acodec = tk.StringVar(value="(auto)")
            ttk.Combobox(fmtbox, textvariable=acodec, width=12, state="readonly",
                         values=["(auto)", "aac", "libmp3lame", "libopus", "flac",
                                 "copy"]).grid(row=1, column=1, sticky="w", padx=4)
            ttk.Label(fmtbox, text="CRF (quality)").grid(row=1, column=2, sticky="w", padx=4)
            crf = tk.StringVar(value="")
            ttk.Spinbox(fmtbox, from_=0, to=51, textvariable=crf, width=6).grid(
                row=1, column=3, sticky="w", padx=4)

            out = FileRow(parent, self, "Save as", mode="save",
                          filetypes=VIDEO_TYPES)
            out.pack(fill="x", pady=4)

            def sync_out(*_):
                inp = src.get()
                if inp:
                    out.set(_suggest_out(inp, "_converted", "." + fmt.get()))
            fmt.trace_add("write", sync_out)

            run = ttk.Button(parent, text="Convert", style="Accent.TButton")
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
                    lambda rep, cx: convert_mod.convert(
                        inp, dest, vcodec=vc, acodec=ac, crf=crf_val,
                        on_progress=rep, cancel=cx.is_set),
                    lambda r: self.report_success(f"Converted → {dest}", [dest]),
                    button=run, busy="Converting…")

            run.configure(command=go)

            def load_path(path):
                src.set(path)
            parent.load_path = load_path
            parent._convert_info = info
            parent._convert_out = out
            parent._convert_fmt = fmt

        def _on_convert_src(self, value):
            if value:
                self.remember_input(value)
            panel = self._panels.get("convert")
            if not panel:
                return
            info = getattr(panel, "_convert_info", None)
            out = getattr(panel, "_convert_out", None)
            fmt = getattr(panel, "_convert_fmt", None)
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

        def _panel_trim(self, parent):
            src = FileRow(parent, self, "Input", on_change=lambda v: (
                self.remember_input(v),
                out.set(_suggest_out(v, "_trim"))))
            src.pack(fill="x", pady=4)
            grid = ttk.Frame(parent, style="TFrame")
            grid.pack(fill="x", pady=4)
            ttk.Label(grid, text="Start", width=8, anchor="w").grid(row=0, column=0)
            start = tk.StringVar()
            ttk.Entry(grid, textvariable=start, width=16).grid(row=0, column=1, padx=4)
            ttk.Label(grid, text="End", width=6, anchor="w").grid(row=0, column=2)
            end = tk.StringVar()
            ttk.Entry(grid, textvariable=end, width=16).grid(row=0, column=3, padx=4)
            ttk.Label(parent, style="Muted.TLabel",
                      text="Times are seconds (12.5) or HH:MM:SS (00:01:30). "
                           "Leave one blank to trim only from the other end.").pack(
                anchor="w")
            reenc = tk.BooleanVar(value=False)
            ttk.Checkbutton(parent, variable=reenc,
                            text="Re-encode (frame-accurate; slower). Off = "
                                 "stream-copy (instant, cuts on keyframes).").pack(
                anchor="w", pady=6)
            out = FileRow(parent, self, "Save as", mode="save")
            out.pack(fill="x", pady=4)
            run = ttk.Button(parent, text="Trim", style="Accent.TButton")
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

        def _panel_compress(self, parent):
            src = FileRow(parent, self, "Input", on_change=lambda v: (
                self.remember_input(v),
                out.set(_suggest_out(v, "_compressed", ".mp4"))))
            src.pack(fill="x", pady=4)
            box = ttk.Labelframe(parent, text="Level", padding=8)
            box.pack(fill="x", pady=6)
            level = tk.StringVar(value="medium")
            for lv, txt in (("low", "Low (best quality, larger)"),
                            ("medium", "Medium"),
                            ("high", "High (smallest, lower quality)")):
                ttk.Radiobutton(box, text=txt, value=lv, variable=level).pack(
                    anchor="w", padx=6, pady=1)
            out = FileRow(parent, self, "Save as", mode="save", defaultext=".mp4")
            out.pack(fill="x", pady=4)
            run = ttk.Button(parent, text="Compress", style="Accent.TButton")
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

        def _panel_extractaudio(self, parent):
            src = FileRow(parent, self, "Input", on_change=lambda v: (
                self.remember_input(v),
                out.set(_suggest_out(v, "", ".mp3"))))
            src.pack(fill="x", pady=4)
            fbox = ttk.Frame(parent, style="TFrame")
            fbox.pack(fill="x", pady=4)
            ttk.Label(fbox, text="Codec", width=8, anchor="w").pack(side="left")
            acodec = tk.StringVar(value="(auto from extension)")
            ttk.Combobox(fbox, textvariable=acodec, width=24, state="readonly",
                         values=["(auto from extension)", "libmp3lame", "aac",
                                 "libopus", "libvorbis", "flac", "pcm_s16le",
                                 "copy"]).pack(side="left")
            out = FileRow(parent, self, "Save as", mode="save",
                          filetypes=AUDIO_OUT_TYPES, defaultext=".mp3")
            out.pack(fill="x", pady=4)
            run = ttk.Button(parent, text="Extract audio", style="Accent.TButton")
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

        def _panel_gif(self, parent):
            src = FileRow(parent, self, "Input", on_change=lambda v: (
                self.remember_input(v),
                out.set(_suggest_out(v, "", ".gif"))))
            src.pack(fill="x", pady=4)
            grid = ttk.Frame(parent, style="TFrame")
            grid.pack(fill="x", pady=4)
            ttk.Label(grid, text="FPS").grid(row=0, column=0, padx=4)
            fps = tk.StringVar(value="12")
            ttk.Spinbox(grid, from_=1, to=50, textvariable=fps, width=6).grid(
                row=0, column=1, padx=4)
            ttk.Label(grid, text="Width").grid(row=0, column=2, padx=4)
            width = tk.StringVar(value="480")
            ttk.Spinbox(grid, from_=16, to=1920, increment=16, textvariable=width,
                        width=8).grid(row=0, column=3, padx=4)
            ttk.Label(grid, text="Start").grid(row=1, column=0, padx=4)
            start = tk.StringVar()
            ttk.Entry(grid, textvariable=start, width=8).grid(row=1, column=1, padx=4)
            ttk.Label(grid, text="Duration").grid(row=1, column=2, padx=4)
            duration = tk.StringVar()
            ttk.Entry(grid, textvariable=duration, width=8).grid(row=1, column=3, padx=4)

            prevrow = ttk.Frame(parent, style="TFrame")
            prevrow.pack(fill="x", pady=6)
            ttk.Button(prevrow, text="Preview first frame",
                       command=lambda: self._gif_preview(src.get(), start.get(),
                                                         preview)).pack(side="left")
            preview = ttk.Label(prevrow, style="Muted.TLabel",
                                text="(no preview yet)")
            preview.pack(side="left", padx=12)

            out = FileRow(parent, self, "Save as", mode="save",
                          filetypes=[("GIF", "*.gif")], defaultext=".gif")
            out.pack(fill="x", pady=4)
            run = ttk.Button(parent, text="Make GIF", style="Accent.TButton")
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
                    self._img_refs.append(photo)
                    label.configure(image=photo, text="")
                except Exception as ex:
                    label.configure(text=f"(preview failed: {ex})")

            self._bg(work, ok, progressive=False)

        def _panel_subtitles(self, parent):
            src = FileRow(parent, self, "Input video", on_change=lambda v: (
                self.remember_input(v),
                out.set(_suggest_out(v, "_subbed"))))
            src.pack(fill="x", pady=4)
            subs = FileRow(parent, self, "Subtitles", mode="open",
                           filetypes=SUB_TYPES)
            subs.pack(fill="x", pady=4)
            ttk.Label(parent, style="Muted.TLabel",
                      text="Burns the .srt/.ass permanently into the picture "
                           "(re-encodes the video; audio is copied).").pack(anchor="w")
            out = FileRow(parent, self, "Save as", mode="save")
            out.pack(fill="x", pady=4)
            run = ttk.Button(parent, text="Burn subtitles", style="Accent.TButton")
            run.pack(anchor="w", pady=8)

            def go():
                inp, dest, sp = src.get(), out.get(), subs.get()
                if not inp or not dest or not sp:
                    self._show_error("Choose a video, a subtitle file and an output.")
                    return
                self._bg(
                    lambda rep, cx: edit_mod.subtitle_burn_run(
                        inp, dest, sp, on_progress=rep, cancel=cx.is_set),
                    lambda r: self.report_success(f"Subtitles burned → {dest}", [dest]),
                    button=run, busy="Burning subtitles…")

            run.configure(command=go)

        def _panel_batch(self, parent):
            ttk.Label(parent, text="Files", style="Sub.TLabel").pack(anchor="w")
            flist = FileList(parent, self)
            flist.pack(fill="both", expand=True, pady=(2, 8))

            opts = ttk.Frame(parent, style="TFrame")
            opts.pack(fill="x", pady=4)
            ttk.Label(opts, text="Operation").grid(row=0, column=0, sticky="w", padx=4)
            op = tk.StringVar(value="compress")
            ttk.Combobox(opts, textvariable=op, width=16, state="readonly",
                         values=["convert", "compress", "extract-audio", "gif"]).grid(
                row=0, column=1, sticky="w", padx=4)
            ttk.Label(opts, text="Level").grid(row=0, column=2, sticky="w", padx=4)
            level = tk.StringVar(value="medium")
            ttk.Combobox(opts, textvariable=level, width=10, state="readonly",
                         values=sorted(COMPRESS_LEVELS)).grid(
                row=0, column=3, sticky="w", padx=4)
            ttk.Label(opts, text="Output ext").grid(row=1, column=0, sticky="w", padx=4)
            ext = tk.StringVar(value="")
            ttk.Entry(opts, textvariable=ext, width=10).grid(
                row=1, column=1, sticky="w", padx=4)
            ttk.Label(opts, style="Muted.TLabel",
                      text="(blank = sensible default per op)").grid(
                row=1, column=2, columnspan=2, sticky="w", padx=4)

            out = FileRow(parent, self, "Output folder", mode="dir")
            out.pack(fill="x", pady=4)
            run = ttk.Button(parent, text="Run batch", style="Accent.TButton")
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
    With no display it prints a friendly note and returns 0 instead of raising.
    """
    try:
        import tkinter as tk
    except Exception as exc:
        print(f"{APP_NAME}: a graphical environment with tkinter is required "
              f"to run the GUI ({exc}).")
        return 0

    try:
        App = build_app()
        app = App()
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
