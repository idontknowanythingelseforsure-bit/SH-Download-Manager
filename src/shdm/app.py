from __future__ import annotations

import os
import math
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from urllib.parse import unquote, urlparse

from PIL import Image, ImageTk

from . import grabber, ytdl
from .api import BrowserAPI
from .engine import DownloadManager, DownloadTask
from .settings import APP_NAME, CATEGORY_NAMES, VERSION, Settings, category_for
from .util import (autostart_enabled, create_desktop_shortcut,
                   create_start_menu_shortcut, desktop_shortcut_path,
                   format_timestamp, human_size, human_speed, open_path,
                   pin_to_start, resource_path, reveal_path,
                   sanitize_filename, set_autostart, start_menu_shortcut_path)

try:  # optional: lets the user drag a link from the browser onto the window
    from tkinterdnd2 import DND_FILES, DND_TEXT, TkinterDnD

    _DND_READY = True
except Exception:  # pragma: no cover - tkdnd binaries not available
    DND_FILES = DND_TEXT = None
    _DND_READY = False

BG = "#1e1e1e"
PANEL = "#252526"
TOOLBAR = "#1b1b1b"
INPUT = "#1b1b1b"
TEXT = "#e0e0e0"
MUTED = "#a0a0a0"
SUBTLE = "#707070"
BORDER = "#3c3c3c"
ACCENT = "#007acc"
ACCENT_DARK = "#005a9e"
GREEN = "#6aa84f"
SUCCESS = "#4ec26a"
RED = "#f44747"
AMBER = "#d4af37"
ROW_ALT = "#2a2a2a"
HOVER = "#3e3e42"
STATUSBAR = "#181818"

STATUS_LABELS = {
    "queued": "Queued",
    "connecting": "Connecting",
    "downloading": "Downloading",
    "pausing": "Pausing",
    "paused": "Paused",
    "completed": "Complete",
    "error": "Error",
    "canceled": "Canceled",
}
STATUS_COLORS = {
    "queued": MUTED,
    "connecting": ACCENT,
    "downloading": ACCENT,
    "pausing": AMBER,
    "paused": AMBER,
    "completed": GREEN,
    "error": RED,
    "canceled": MUTED,
}
CATEGORY_COLORS = {
    "Compressed": "#d4a017",
    "Documents": "#569cd6",
    "Music": "#ce9178",
    "Video": "#b5cea8",
    "Programs": "#4ec9b0",
    "Images": "#d16969",
    "Other": "#9cdcfe",
}
CATEGORY_CHOICES = [*CATEGORY_NAMES, "Other"]


def progress_text(progress, width=9):
    if progress is None:
        return "-"
    progress = max(0.0, min(1.0, progress))
    filled = int(round(progress * width))
    bar = "#" * filled + "." * (width - filled)
    return "%s %3.0f%%" % (bar, progress * 100)


def expand_ranges(text, limit=500):
    """Expand '[1-10]' or '[001-010]' patterns into one link per value."""
    pattern = re.compile(r"\[(\d+)-(\d+)\]")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        variants = [line]
        for match in pattern.finditer(line):
            start_s, end_s = match.group(1), match.group(2)
            start, end = int(start_s), int(end_s)
            if end < start:
                start, end = end, start
            width = len(start_s) if start_s.startswith("0") else 0
            token = match.group(0)
            expanded = []
            for value in range(start, end + 1):
                piece = str(value).zfill(width) if width else str(value)
                for current in variants:
                    expanded.append(current.replace(token, piece, 1))
            variants = expanded
            if len(variants) >= limit:
                variants = variants[:limit]
                break
        out.extend(variants)
        if len(out) >= limit:
            return out[:limit]
    return out


def flash_window(root):
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        ctypes.windll.user32.FlashWindow(hwnd, True)
    except Exception:
        pass


def app_command():
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    script = Path(__file__).resolve().parents[1] / "run.py"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    return '"%s" "%s"' % (interpreter, script)


def apply_dark_frame(window):
    """Force a dark native title bar and frame on Windows 10/11."""
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT
        dwm = ctypes.windll.dwmapi
        enabled = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20 new, 19 old)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(enabled),
                                      ctypes.sizeof(enabled))
        caption = ctypes.c_int(0x001E1E1E)  # COLORREF 0x00BBGGRR
        text = ctypes.c_int(0x00E0E0E0)
        border = ctypes.c_int(0x003C3C3C)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
        # Do not force SWP_FRAMECHANGED here: on Windows 10 it makes the caption
        # be repainted with the light theme even though the attribute is set.
        window.update_idletasks()
    except Exception:
        pass


def _split_drop_items(data):
    """Split tkdnd drop data into items, keeping Windows backslashes intact.

    Tcl's splitlist unescapes ``\\`` sequences, which corrupts paths such as
    ``C:\\Users\\me\\file.url``; this parser only honours braces and quotes.
    """
    items = []
    buf = []
    depth = 0
    quoted = False
    for ch in data:
        if quoted:
            if ch == '"':
                quoted = False
            else:
                buf.append(ch)
            continue
        if ch == '"' and not buf and depth == 0:
            quoted = True
            continue
        if ch == "{" and depth == 0 and not buf:
            depth = 1
            continue
        if ch == "}" and depth:
            depth = 0
            items.append("".join(buf))
            buf = []
            continue
        if ch.isspace() and depth == 0:
            if buf:
                items.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        items.append("".join(buf))
    return [item for item in items if item]


class SHApp(tk.Tk):
    def __init__(self, urls=None, minimized=False):
        super().__init__()
        self._dnd_ready = False
        if _DND_READY:
            try:
                TkinterDnD._require(self)
                self._dnd_ready = True
            except Exception:
                self._dnd_ready = False
        self.settings = Settings()
        self.manager = DownloadManager(self.settings)
        self.api = None
        self._add_open = False
        self._grabber_open = False
        self._settings_open = False
        self._batch_open = False
        self._clip_last = ""
        self._visible_ids = []
        self._snap_by_id = {}
        self._all_snaps = []
        self._prev_status = {}
        self._message_job = None
        self._refresh_job = None
        self._filter_mode = "all"
        self._sidebar_rows = []
        self._pending_select = None
        self._tip = None
        self._tray = None
        self._completion_dialog = None
        self._completion_queue = []
        self._quitting = False

        self.title(APP_NAME)
        self.minsize(760, 480)
        self.resizable(True, True)
        self.configure(bg=BG)
        self._set_icon()
        self._configure_style()
        self._build_menu()
        self._build_toolbar()
        self._build_status()
        self._build_body()

        geometry = str(self.settings.get("window_geometry") or "")
        if geometry and int(self.settings.get("geometry_version", 0) or 0) >= 2:
            self.geometry(self._clamped_geometry(geometry))
        else:
            self.geometry(self._centered_geometry(900, 560))
            self.settings.set("geometry_version", 2)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._start_hidden = bool(minimized or self.settings.get("start_minimized"))
        self.after(60, self._reveal)
        self.bind("<Map>", lambda _e: self._apply_dark_frame())
        self.bind("<Control-n>", lambda _e: self.open_add_dialog())
        self.bind("<Control-B>", lambda _e: self.open_batch_dialog())
        self.bind("<Control-Shift-B>", lambda _e: self.open_batch_dialog())
        self.bind("<Control-g>", lambda _e: self.open_grabber())
        self.bind("<Control-comma>", lambda _e: self.open_settings())
        self.bind("<Control-q>", lambda _e: self.quit_app())
        self.bind("<F5>", lambda _e: self.refresh())

        self._drop_targets = []
        self.after(120, self._enable_drag_drop)

        self.after(150, self._ensure_extension_copy)
        self.after(900, self._ensure_start_menu_entry)
        self.after(700, self._start_background_mode)
        self.after(400, self.start_api)
        self.after(500, self.refresh)
        self.after(900, self._poll_clipboard)
        if urls:
            self.after(400, lambda: self.add_urls(urls, start_now=True))

    # ----- chrome -------------------------------------------------------
    def _reveal(self):
        if self._start_hidden:
            self.withdraw()
            return
        self.update()
        self._apply_dark_frame()

    # ----- background / tray -------------------------------------------
    def _start_background_mode(self):
        """Create the tray icon and honour the autostart setting."""
        try:
            if self.settings.get("launch_on_start") and not autostart_enabled(APP_NAME):
                set_autostart(True, APP_NAME)
            elif not self.settings.get("launch_on_start") and autostart_enabled(APP_NAME):
                set_autostart(False, APP_NAME)
        except Exception:
            pass
        try:
            from .tray import TrayIcon

            tray = TrayIcon(
                resource_path("assets/icon.ico"),
                "SH Download Manager - running in background",
                lambda: self.after(0, self._show_from_tray),
                lambda: self.after(0, self.quit_app),
                lambda: self.after(0, self.pause_all),
            )
            if tray.start():
                self._tray = tray
        except Exception:
            self._tray = None

    def _show_from_tray(self):
        try:
            self.deiconify()
            self.state("normal")
            self.bring_to_front()
        except Exception:
            pass

    def hide_to_tray(self):
        if self._tray is None:
            self.quit_app()
            return
        try:
            self.withdraw()
        except Exception:
            pass
        self._tray.notify(APP_NAME, "Still running in the background - "
                                   "the browser extension keeps working.")

    def quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        self._completion_queue.clear()
        if self._completion_dialog is not None:
            try:
                self._completion_dialog.destroy()
            except Exception:
                pass
            self._completion_dialog = None
        try:
            geometry = self.geometry()
            if self.winfo_viewable():
                self.settings.set("window_geometry", geometry)
            self.settings.save()
        except Exception:
            pass
        if self.settings.get("confirm_exit") and not self.winfo_viewable():
            active = [s for s in self._all_snaps
                      if s["status"] in ("downloading", "connecting", "queued", "pausing")]
            if active:
                try:
                    self.deiconify()
                except Exception:
                    pass
                answer = messagebox.askyesno(
                    APP_NAME,
                    "%d download(s) are still active.\n\n"
                    "Pause them and exit?" % len(active), parent=self)
                if not answer:
                    self._quitting = False
                    return
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
            self._tray = None
        try:
            if self.api is not None:
                self.api.stop()
        except Exception:
            pass
        try:
            self.manager.shutdown()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def _native_hwnd(self):
        import ctypes

        user32 = ctypes.windll.user32
        return user32.GetAncestor(self.winfo_id(), 2)  # GA_ROOT

    def _apply_dark_frame(self):
        apply_dark_frame(self)

    def _set_icon(self):
        """Set the window icon, overriding the executable's default.

        Running from pythonw.exe (source mode) leaves a Python icon on the
        taskbar; the frozen exe already carries the right icon, but setting it
        explicitly here keeps the taskbar, title bar and tray consistent.
        """
        try:
            path = resource_path("assets/icon.ico")
            if not path.is_file():
                return
            self.iconbitmap(default=str(path))
            self._set_window_icon(path)
        except Exception:
            pass

    def _set_window_icon(self, path):
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            GDI32 = ctypes.windll.gdi32

            user32.LoadImageW.restype = wintypes.HANDLE
            user32.LoadImageW.argtypes = [
                wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.c_uint,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint,
            ]
            user32.SendMessageW.restype = ctypes.c_size_t
            user32.SendMessageW.argtypes = [
                wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM,
            ]
            user32.GetAncestor.restype = wintypes.HWND
            user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]

            hwnd = user32.GetAncestor(self.winfo_id(), 2)  # GA_ROOT
            if not hwnd:
                return

            for size in (32, 48, 256):
                hicon = user32.LoadImageW(
                    None, str(path), 1, size, size,
                    0x0010 | 0x0040 | 0x1000)  # LR_LOADFROMFILE | LR_DEFAULTSIZE | LR_DEFAULTCOLOR
                if hicon:
                    user32.SendMessageW(hwnd, 0x0080, hicon, 0x0080)  # WM_SETICON, ICON_BIG
                    user32.SendMessageW(hwnd, 0x0080, hicon, 0x0000)  # ICON_SMALL
                    try:
                        user32.DestroyIcon(hicon)
                    except Exception:
                        pass
                    break
        except Exception:
            pass

    def _work_area(self):
        try:
            import ctypes
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                            ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

            rect = RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def _centered_geometry(self, width, height):
        left, top, right, bottom = self._work_area()
        width = min(width, right - left - 20)
        height = min(height, bottom - top - 75)
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height - 60) // 2)
        return "%dx%d+%d+%d" % (width, height, x, y)

    def _clamped_geometry(self, geometry):
        match = re.match(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
        if not match:
            return self._centered_geometry(900, 560)
        width = int(match.group(1))
        height = int(match.group(2))
        left, top, right, bottom = self._work_area()
        width = min(width, right - left - 20)
        height = min(height, bottom - top - 75)
        x = max(left, min(int(match.group(3)), right - width - 10))
        y = max(top, min(int(match.group(4)), bottom - height - 60))
        return "%dx%d+%d+%d" % (width, height, x, y)

    def _configure_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        tkfont.nametofont("TkDefaultFont").configure(family="Segoe UI", size=9)
        tkfont.nametofont("TkTextFont").configure(family="Segoe UI", size=9)
        style.configure(".", font=("Segoe UI", 9), background=BG, foreground=TEXT)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("Toolbar.TFrame", background=TOOLBAR)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Card.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 8))
        style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED,
                        font=("Segoe UI", 8))
        style.configure("Title.TLabel", background=PANEL, foreground=TEXT,
                        font=("Segoe UI Semibold", 12))
        style.configure("Tool.TButton", background=TOOLBAR, foreground=TEXT,
                        padding=(10, 6), relief="flat", borderwidth=0)
        style.map("Tool.TButton",
                  background=[("active", HOVER), ("pressed", ACCENT_DARK),
                              ("disabled", TOOLBAR)],
                  foreground=[("disabled", SUBTLE)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                        padding=(12, 6), relief="flat", borderwidth=0,
                        font=("Segoe UI Semibold", 9))
        style.map("Accent.TButton",
                  background=[("active", ACCENT_DARK), ("pressed", "#063f85"),
                              ("disabled", "#2a4a66")],
                  foreground=[("disabled", "#9fb6c8")])
        style.configure("Small.TButton", background=PANEL, foreground=TEXT,
                        padding=(8, 4), relief="flat", borderwidth=0)
        style.map("Small.TButton", background=[("active", HOVER)],
                  foreground=[("disabled", SUBTLE)])
        style.configure("Card.TButton", background="#2d2d30", foreground=TEXT,
                        padding=(12, 5), relief="flat", borderwidth=1,
                        bordercolor=BORDER, lightcolor="#2d2d30",
                        darkcolor="#2d2d30", font=("Segoe UI", 9))
        style.map("Card.TButton",
                  background=[("active", HOVER), ("pressed", ACCENT_DARK)],
                  bordercolor=[("active", "#5a5a5a")],
                  foreground=[("active", "#ffffff")])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=TEXT, rowheight=26, borderwidth=0, relief="flat",
                        font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#353535", foreground="#c0c0c0",
                        relief="flat", borderwidth=0, padding=(6, 5),
                        font=("Segoe UI Semibold", 8))
        style.map("Treeview.Heading", background=[("active", "#404040")])
        style.map("Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("Download.Horizontal.TProgressbar", background=ACCENT,
                        troughcolor="#353535", bordercolor="#353535",
                        lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)
        style.configure("TEntry", fieldbackground="#1b1b1b", foreground=TEXT,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        insertcolor=TEXT, padding=(5, 4))
        style.configure("TCombobox", fieldbackground="#1b1b1b", foreground=TEXT,
                        background=TOOLBAR, arrowcolor=TEXT, padding=(4, 3))
        style.map("TCombobox", fieldbackground=[("readonly", "#1b1b1b")],
                  foreground=[("readonly", TEXT)])
        style.configure("TSpinbox", fieldbackground="#1b1b1b", foreground=TEXT,
                        arrowcolor=TEXT, padding=(4, 3))
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", PANEL)],
                  foreground=[("active", TEXT)])
        style.configure("Dialog.TCheckbutton", background=BG, foreground=TEXT)
        style.map("Dialog.TCheckbutton", background=[("active", BG)],
                  foreground=[("active", TEXT)])
        style.configure("Dialog.TCombobox", fieldbackground=INPUT, background=TOOLBAR,
                        foreground=TEXT, arrowcolor=TEXT, padding=(5, 4),
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.map("Dialog.TCombobox", fieldbackground=[("readonly", INPUT)],
                  foreground=[("readonly", TEXT)],
                  selectbackground=[("readonly", INPUT)],
                  selectforeground=[("readonly", TEXT)])
        self.option_add("*TCombobox*Listbox.background", INPUT)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style.configure("TNotebook", background=PANEL, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 7), background="#353535",
                        foreground=MUTED)
        style.map("TNotebook.Tab", background=[("selected", PANEL)],
                  foreground=[("selected", TEXT)])
        style.configure("Vertical.TScrollbar", background="#4a4a4a", troughcolor=PANEL,
                        bordercolor=PANEL, arrowcolor=MUTED, relief="flat")
        style.configure("Horizontal.TScrollbar", background="#4a4a4a", troughcolor=PANEL,
                        bordercolor=PANEL, arrowcolor=MUTED, relief="flat")
        style.configure("TSeparator", background=BORDER)

    # ----- menu / toolbar ----------------------------------------------
    def _build_menu(self):
        menu = tk.Menu(self, tearoff=False, bg=PANEL, fg=TEXT,
                       activebackground=ACCENT, activeforeground="#ffffff",
                       borderwidth=0)

        # ----- File -----
        file_menu = tk.Menu(menu, tearoff=False, bg=PANEL, fg=TEXT,
                            activebackground=ACCENT, activeforeground="#ffffff")
        file_menu.add_command(label="New Download", accelerator="Ctrl+N",
                              command=self.open_add_dialog)
        file_menu.add_command(label="Import From Clipboard", accelerator="Ctrl+V",
                              command=self.import_clipboard)
        file_menu.add_command(label="Batch Download", accelerator="Ctrl+Shift+B",
                              command=self.open_batch_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", accelerator="Ctrl+Q",
                              command=self.quit_app)
        menu.add_cascade(label="File", menu=file_menu)

        # ----- Downloads (Tasks) -----
        dl_menu = tk.Menu(menu, tearoff=False, bg=PANEL, fg=TEXT,
                          activebackground=ACCENT, activeforeground="#ffffff")
        dl_menu.add_command(label="Start Queue", command=self.start_all)
        dl_menu.add_command(label="Stop Queue", command=self.stop_all)
        dl_menu.add_separator()
        dl_menu.add_command(label="Stop All", command=self.pause_all)
        dl_menu.add_command(label="Delete", command=self.remove_selected)
        menu.add_cascade(label="Downloads", menu=dl_menu)

        # ----- Tools -----
        tools = tk.Menu(menu, tearoff=False, bg=PANEL, fg=TEXT,
                        activebackground=ACCENT, activeforeground="#ffffff")
        tools.add_command(label="Download Browser Integration >",
                          command=lambda: None)
        tools.add_separator()
        tools.add_command(label="Per Host Settings", command=self.open_per_host)
        tools.add_command(label="Settings", accelerator="Ctrl+Alt+S",
                          command=self.open_settings)
        menu.add_cascade(label="Tools", menu=tools)

        # ----- Help -----
        help_menu = tk.Menu(menu, tearoff=False, bg=PANEL, fg=TEXT,
                            activebackground=ACCENT, activeforeground="#ffffff")
        help_menu.add_command(label="View the Open-Source in git",
                              command=self.open_git_repo)
        help_menu.add_command(label="Check for Update",
                              command=self.check_update)
        help_menu.add_separator()
        help_menu.add_command(label="About", command=self.show_about)
        menu.add_cascade(label="Help", menu=help_menu)

        # Black menubar row (labels post their menu explicitly: Menubutton
        # click handling is unreliable on some Windows/Tk builds)
        bar = tk.Frame(self, bg="#141414", height=26)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        for label, sub in (("File", file_menu), ("Downloads", dl_menu),
                           ("Tools", tools), ("Help", help_menu)):
            item = tk.Label(bar, text=label, bg="#141414", fg=TEXT,
                            padx=12, pady=3, font=("Segoe UI", 9),
                            cursor="hand2")
            item.pack(side="left", fill="y")
            item.bind("<Enter>", lambda _e, w=item: w.config(bg=HOVER))
            item.bind("<Leave>", lambda _e, w=item: w.config(bg="#141414"))
            item.bind("<Button-1>", self._menu_click_handler(sub))

    def _menu_click_handler(self, submenu):
        def handler(event):
            widget = event.widget
            try:
                submenu.tk_popup(widget.winfo_rootx(),
                                 widget.winfo_rooty() + widget.winfo_height())
            finally:
                submenu.grab_release()
        return handler

    def import_clipboard(self):
        self.set_message("Import From Clipboard — not yet implemented")

    def start_all(self):
        self.start_selected()

    def stop_all(self):
        self.pause_selected()

    def open_per_host(self):
        self.set_message("Per Host Settings — not yet implemented")

    def open_git_repo(self):
        import webbrowser
        webbrowser.open("https://github.com/idontknowanythingelseforsure-bit/YT-Audio-Player")

    def check_update(self):
        self.set_message("Check for Update — not yet implemented")

    def _add_tooltip(self, widget, text):
        def enter(_event=None):
            self._hide_tooltip()
            tip = tk.Toplevel(self)
            tip.wm_overrideredirect(True)
            tip.configure(bg="#3c3c3c")
            tk.Label(tip, text=text, bg="#3c3c3c", fg=TEXT,
                     font=("Segoe UI", 8), padx=6, pady=3).pack()
            x = widget.winfo_rootx() + widget.winfo_width() // 2 - 30
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tip.geometry("+%d+%d" % (x, y))
            self._tip = tip

        def leave(_event=None):
            self._hide_tooltip()

        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")

    def _hide_tooltip(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

    def _tool_button(self, parent, icon, tooltip, command):
        button = tk.Button(parent, text=icon, command=command, bg=TOOLBAR, fg=TEXT,
                           font=("Segoe UI", 13), relief="flat", bd=0, padx=9, pady=3,
                           activebackground=HOVER, activeforeground=ACCENT,
                           highlightthickness=0, cursor="hand2")
        button.pack(side="left", padx=1)
        self._add_tooltip(button, tooltip)
        return button

    def _icon_button(self, parent, kind, tooltip, command):
        """Canvas-drawn toolbar icon (plus / trash) to match the reference art."""
        canvas = tk.Canvas(parent, width=22, height=22, bg=TOOLBAR,
                           highlightthickness=0, bd=0, cursor="hand2")
        canvas.pack(side="left", padx=4)
        items = self._draw_icon(canvas, kind, TEXT)

        def hover(_event=None):
            canvas.configure(bg=HOVER)
            self._recolor_icon(canvas, items, "#ffffff")

        def leave(_event=None):
            canvas.configure(bg=TOOLBAR)
            self._recolor_icon(canvas, items, TEXT)

        canvas.bind("<Enter>", hover, add="+")
        canvas.bind("<Leave>", leave, add="+")
        canvas.bind("<Button-1>", lambda _e: command())
        self._add_tooltip(canvas, tooltip)
        return canvas

    def _recolor_icon(self, canvas, items, color):
        for item in items:
            kind = canvas.type(item)
            if kind in ("rectangle", "polygon"):
                canvas.itemconfigure(item, fill=color, outline=color)
            elif kind == "arc":
                canvas.itemconfigure(item, outline=color)
            else:
                canvas.itemconfigure(item, fill=color)

    def _draw_icon(self, canvas, kind, color):
        items = []
        if kind == "add":
            # Bold plus with square ends (reference image 2)
            items.append(canvas.create_rectangle(2.6, 9.7, 19.4, 12.6,
                                                 fill=color, outline=color))
            items.append(canvas.create_rectangle(9.7, 2.6, 12.6, 19.4,
                                                 fill=color, outline=color))
            return items

        if kind == "trash":
            # Handle above the lid
            items.append(canvas.create_arc(7.4, 1.2, 14.6, 8.0, start=0, extent=180,
                                           style="arc", outline=color, width=1.8))
            # Lid
            items.append(canvas.create_line(2.2, 6.4, 19.8, 6.4, fill=color,
                                            width=2.3, capstyle="round"))
            # Body with softly rounded bottom
            items.append(canvas.create_line(
                4.3, 8.5, 3.9, 18.2, 5.5, 20.9, 16.5, 20.9, 18.1, 18.2, 17.7, 8.5,
                fill=color, width=1.9, capstyle="round", joinstyle="round",
                smooth=True, splinesteps=32))
            # Inner ribs
            for x in (8.0, 11.0, 14.0):
                items.append(canvas.create_line(x, 10.6, x, 18.4, fill=color,
                                                width=1.6, capstyle="round"))
            return items

        return items

    def _build_toolbar(self):
        bar = tk.Frame(self, bg=TOOLBAR, height=54, highlightbackground=BORDER,
                       highlightthickness=1)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=TOOLBAR)
        left.pack(side="left", padx=(6, 0), pady=6)
        self._icon_button(left, "add", "New Download (Ctrl+N)", self.open_add_dialog)
        self._tool_button(left, "\u25b6", "Resume", self.start_selected)
        self._tool_button(left, "\u23f8", "Pause", self.pause_selected)
        self._tool_button(left, "\u25a0", "Stop Queue", self.stop_selected)
        self._icon_button(left, "trash", "Delete", self.remove_selected)
        self._tool_button(left, "\u21bb", "Refresh (F5)", self.refresh)
        self._tool_button(left, "\u2630", "Queues", self.open_grabber)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8, pady=8)

        right = tk.Frame(bar, bg=TOOLBAR)
        right.pack(side="right", padx=(0, 8), pady=6)
        self._tool_button(right, "\u2699", "Settings (Ctrl+,)", self.open_settings)
        self._tool_button(right, "\u2630", "Media Grabber (Ctrl+G)", self.open_grabber)

        ttk.Separator(bar, orient="vertical").pack(side="right", fill="y", padx=8, pady=8)

        search_box = tk.Frame(bar, bg=TOOLBAR)
        search_box.pack(side="right", pady=8)
        tk.Label(search_box, text="Search", bg=TOOLBAR, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=(0, 6))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_a: self.refresh())
        search = ttk.Entry(search_box, textvariable=self.search_var, width=24)
        search.pack(side="left")
        search.bind("<Escape>", lambda _e: self.search_var.set(""))

    def _build_status(self):
        bar = tk.Frame(self, bg=STATUSBAR, height=26, highlightbackground=BORDER,
                       highlightthickness=1)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.status_left = tk.Label(bar, text="", bg=STATUSBAR, fg=MUTED,
                                    font=("Segoe UI", 8), padx=10)
        self.status_left.pack(side="left")
        self.status_msg = tk.Label(bar, text="", bg=STATUSBAR, fg="#7dd3fc",
                                   font=("Segoe UI", 8), padx=6)
        self.status_msg.pack(side="left")
        self.status_right = tk.Label(bar, text="v%s" % VERSION, bg=STATUSBAR,
                                     fg=SUBTLE, font=("Segoe UI", 8), padx=10)
        self.status_right.pack(side="right")
        self.status_speed = tk.Label(bar, text="", bg=STATUSBAR, fg=MUTED,
                                     font=("Segoe UI", 8), padx=10)
        self.status_speed.pack(side="right")

    # ----- body ---------------------------------------------------------
    def _build_body(self):
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        paned = ttk.Panedwindow(body, orient="horizontal")
        paned.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(paned, bg=PANEL, width=200, highlightbackground=BORDER,
                                highlightthickness=1)
        self.sidebar.pack_propagate(False)
        paned.add(self.sidebar, weight=0)

        content = tk.Frame(paned, bg=BG)
        paned.add(content, weight=1)

        header = tk.Frame(content, bg=BG)
        header.pack(fill="x", pady=(0, 6))
        tk.Label(header, text="Downloads", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 12)).pack(side="left")
        self.count_label = tk.Label(header, text="", bg=BG, fg=MUTED,
                                    font=("Segoe UI", 8))
        self.count_label.pack(side="left", padx=(10, 0), pady=(3, 0))

        tree_card = tk.Frame(content, bg=PANEL, highlightbackground=BORDER,
                             highlightthickness=1)
        tree_card.pack(fill="both", expand=True)

        columns = ("name", "size", "status", "progress", "speed", "eta", "added")
        self.tree = ttk.Treeview(tree_card, columns=columns, show="headings", height=7,
                                 selectmode="extended")
        headings = {
            "name": ("File Name", 240, "w"),
            "size": ("Size", 88, "e"),
            "status": ("Status", 92, "w"),
            "progress": ("Progress", 132, "w"),
            "speed": ("Speed", 82, "e"),
            "eta": ("Time Left", 72, "e"),
            "added": ("Added", 112, "w"),
        }
        for column, (label, width, anchor) in headings.items():
            self.tree.heading(column, text=label, anchor=anchor,
                              command=lambda c=column: self.sort_by(c))
            self.tree.column(column, width=width, minwidth=55, anchor=anchor,
                             stretch=(column == "name"))
        self.tree.tag_configure("completed", foreground=SUBTLE)
        self.tree.tag_configure("error", foreground=RED)
        self.tree.tag_configure("paused", foreground=AMBER)
        self.tree.tag_configure("queued", foreground=MUTED)
        self.tree.tag_configure("odd", background=ROW_ALT)
        self.tree.tag_configure("even", background=PANEL)

        yscroll = ttk.Scrollbar(tree_card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        yscroll.pack(side="right", fill="y", pady=1, padx=(0, 1))

        self.empty_label = tk.Label(
            tree_card,
            text="No downloads yet\n\nClick + Add URL to start a download,\n"
                 "press Ctrl+N, or drop a link onto this window.",
            bg=PANEL, fg=SUBTLE, font=("Segoe UI", 10), justify="center")

        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_details())
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Delete>", lambda _e: self.remove_selected())
        self.tree.bind("<space>", self._toggle_selected)
        self.tree.bind("<Return>", lambda _e: self.open_selected_file())
        self.tree.bind("<Control-a>", self._select_all)

        self.context_menu = tk.Menu(self, tearoff=False, bg=PANEL, fg=TEXT,
                                    activebackground=ACCENT, activeforeground="#ffffff")
        self.context_menu.add_command(label="Start / Resume", command=self.start_selected)
        self.context_menu.add_command(label="Pause", command=self.pause_selected)
        self.context_menu.add_command(label="Remove", command=self.remove_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Open file", command=self.open_selected_file)
        self.context_menu.add_command(label="Open folder", command=self.open_selected_folder)
        self.context_menu.add_command(label="Copy URL", command=self.copy_selected_url)

        self._build_sidebar()

    def _build_sidebar(self):
        header = tk.Frame(self.sidebar, bg=PANEL)
        header.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(header, text="CATEGORIES", bg=PANEL, fg=SUBTLE,
                 font=("Segoe UI Semibold", 8)).pack(anchor="w")

        # Connections / speed info at the top of the sidebar (image request)
        summary = tk.Frame(self.sidebar, bg="#1b1b1b")
        summary.pack(fill="x", side="top", padx=6, pady=(6, 4))
        self.summary_label = tk.Label(
            summary, text="", bg="#1b1b1b", fg=MUTED, font=("Segoe UI", 8),
            justify="left", anchor="w", padx=8, pady=4)
        self.summary_label.pack(fill="x")
        self._update_summary()

        tree_holder = tk.Frame(self.sidebar, bg=PANEL)
        tree_holder.pack(fill="both", expand=True, padx=6, pady=(4, 6))
        self.sidebar_tree = ttk.Treeview(tree_holder, show="tree",
                                         selectmode="browse", height=10)
        self.sidebar_tree.column("#0", width=150, minwidth=90, stretch=True)
        self.sidebar_tree.tag_configure("sep", foreground=BORDER)
        for name, color in CATEGORY_COLORS.items():
            self.sidebar_tree.tag_configure("cat_%s" % name, foreground=color)
        self.sidebar_tree.bind("<<TreeviewSelect>>", self._on_sidebar_select)
        side_scroll = ttk.Scrollbar(tree_holder, orient="vertical",
                                    command=self.sidebar_tree.yview)
        self.sidebar_tree.configure(yscrollcommand=side_scroll.set)
        side_scroll.pack(side="right", fill="y")
        self.sidebar_tree.pack(side="left", fill="both", expand=True)

        self._rebuild_sidebar()

    def _build_details(self, parent):
        card = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(6, 0))
        inner = tk.Frame(card, bg=PANEL)
        inner.pack(fill="x", padx=12, pady=8)

        top = tk.Frame(inner, bg=PANEL)
        top.pack(fill="x")
        self.detail_name = tk.Label(top, text="No download selected", bg=PANEL, fg=TEXT,
                                    font=("Segoe UI Semibold", 10), anchor="w")
        self.detail_name.pack(side="left", fill="x", expand=True)
        self.detail_status = tk.Label(top, text="", bg=PANEL, fg=MUTED,
                                      font=("Segoe UI Semibold", 9))
        self.detail_status.pack(side="right")

        bar_row = tk.Frame(inner, bg=PANEL)
        bar_row.pack(fill="x", pady=(6, 2))
        self.detail_bar = ttk.Progressbar(bar_row, style="Download.Horizontal.TProgressbar",
                                          mode="determinate", maximum=100)
        self.detail_bar.pack(side="left", fill="x", expand=True)
        self.detail_pct = tk.Label(bar_row, text="-", bg=PANEL, fg=MUTED, width=8,
                                   font=("Segoe UI", 9), anchor="e")
        self.detail_pct.pack(side="right", padx=(8, 0))

        self.detail_meta = tk.Label(inner, text="Select a download to see details.",
                                    bg=PANEL, fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self.detail_meta.pack(fill="x", pady=(2, 0))
        self.detail_path = tk.Label(inner, text="", bg=PANEL, fg=MUTED,
                                    font=("Segoe UI", 8), anchor="w")
        self.detail_path.pack(fill="x")
        self.detail_url = tk.Label(inner, text="", bg=PANEL, fg=SUBTLE,
                                   font=("Segoe UI", 8), anchor="w")
        self.detail_url.pack(fill="x")

        actions = tk.Frame(inner, bg=PANEL)
        actions.pack(fill="x", pady=(4, 0))
        self.open_file_btn = ttk.Button(actions, text="Open file", style="Small.TButton",
                                        command=self.open_selected_file, state="disabled")
        self.open_file_btn.pack(side="left")
        self.open_folder_btn = ttk.Button(actions, text="Open folder", style="Small.TButton",
                                          command=self.open_selected_folder, state="disabled")
        self.open_folder_btn.pack(side="left", padx=4)
        self.copy_url_btn = ttk.Button(actions, text="Copy URL", style="Small.TButton",
                                       command=self.copy_selected_url, state="disabled")
        self.copy_url_btn.pack(side="left")

    # ----- sidebar ------------------------------------------------------
    def _rebuild_sidebar(self):
        for child in self.sidebar_tree.get_children():
            self.sidebar_tree.delete(child)
        self._sidebar_rows = []
        entries = [("all", "All Downloads"), ("active", "Downloading"),
                   ("queued", "Queued"), ("finished", "Finished"),
                   ("unfinished", "Unfinished"), (None, "")]
        entries += [(name, name) for name in [*CATEGORY_NAMES, "Other"]]
        for key, label in entries:
            if key is None:
                iid = "side_sep"
                self.sidebar_tree.insert("", "end", iid=iid, text=" ", tags=("sep",))
                self._sidebar_rows.append((iid, "", ""))
                continue
            iid = "side_%s" % key
            tag = "cat_%s" % key if key in CATEGORY_COLORS else ""
            self.sidebar_tree.insert("", "end", iid=iid, text=label,
                                     tags=(tag,) if tag else ())
            self._sidebar_rows.append((iid, key, label))
        for iid, key, _label in self._sidebar_rows:
            if key == self._filter_mode:
                self.sidebar_tree.selection_set(iid)
                self.sidebar_tree.see(iid)
                break

    def _update_sidebar_counts(self, snapshots):
        counts = {"all": len(snapshots), "active": 0, "queued": 0,
                  "finished": 0, "unfinished": 0}
        for snap in snapshots:
            status = snap["status"]
            if status in ("connecting", "downloading", "pausing"):
                counts["active"] += 1
            if status == "queued":
                counts["queued"] += 1
            if status == "completed":
                counts["finished"] += 1
            else:
                counts["unfinished"] += 1
            counts[snap["category"]] = counts.get(snap["category"], 0) + 1
        for iid, key, label in self._sidebar_rows:
            if not key:
                continue
            count = counts.get(key, 0)
            text = "%s   %d" % (label, count) if count else label
            if key == self._filter_mode:
                text = "> " + text
            self.sidebar_tree.item(iid, text=text)

    def _on_sidebar_select(self, _event=None):
        selection = self.sidebar_tree.selection()
        if not selection:
            return
        iid = selection[0]
        for row_iid, key, _label in self._sidebar_rows:
            if row_iid == iid and key:
                self._filter_mode = key
                self._visible_ids = []
                self.refresh()
                return

    # ----- refresh ------------------------------------------------------
    def refresh(self):
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
            self._refresh_job = None
        try:
            snapshots = self.manager.snapshots()
            stats = self.manager.stats()
        except Exception:
            self._refresh_job = self.after(800, self.refresh)
            return
        self._all_snaps = snapshots
        self._snap_by_id = {s["id"]: s for s in snapshots}
        visible = self._filtered(snapshots)
        self._render(visible)
        if self._pending_select and self.tree.exists(self._pending_select):
            self.tree.selection_set(self._pending_select)
            self.tree.see(self._pending_select)
            self._pending_select = None
        self._update_sidebar_counts(snapshots)
        self._update_status(stats)
        self._update_details()
        self._check_completions(snapshots)
        self._refresh_job = self.after(600, self.refresh)

    def _filtered(self, snapshots):
        mode = self._filter_mode
        result = []
        for snap in snapshots:
            status = snap["status"]
            if mode == "active" and status not in ("connecting", "downloading", "pausing"):
                continue
            if mode == "queued" and status != "queued":
                continue
            if mode == "finished" and status != "completed":
                continue
            if mode == "unfinished" and status == "completed":
                continue
            if mode in CATEGORY_NAMES and snap["category"] != mode:
                continue
            if mode == "Other" and snap["category"] != "Other":
                continue
            result.append(snap)
        query = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        if query:
            result = [s for s in result
                      if query in s["file_name"].lower() or query in s["url"].lower()]
        column = str(self.settings.get("sort_column", "added"))
        reverse = bool(self.settings.get("sort_desc", True))
        keys = {
            "name": lambda s: s["file_name"].lower(),
            "size": lambda s: s["total_size"] or s["downloaded"] or 0,
            "status": lambda s: s["status"],
            "progress": lambda s: s["progress"] if s["progress"] is not None else -1,
            "speed": lambda s: s["speed"],
            "eta": lambda s: s["eta"] if s["eta"] is not None else 1e12,
            "added": lambda s: s["added_at"],
        }
        return sorted(result, key=keys.get(column, keys["added"]), reverse=reverse)

    def _render(self, visible):
        ids = [s["id"] for s in visible]
        selected = set(self.tree.selection())
        current = list(self.tree.get_children())
        if ids != current:
            if current:
                self.tree.delete(*current)
            for index, snap in enumerate(visible):
                self.tree.insert("", "end", iid=snap["id"],
                                 values=self._row_values(snap, index),
                                 tags=self._row_tags(snap, index))
            self._visible_ids = ids
            keep = [sid for sid in selected if sid in ids]
            if keep:
                self.tree.selection_set(keep)
                self.tree.see(keep[0])
        else:
            for index, snap in enumerate(visible):
                iid = snap["id"]
                self.tree.item(iid, values=self._row_values(snap, index),
                               tags=self._row_tags(snap, index))
        if visible:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.45, anchor="center")
        self.count_label.config(text="%d shown" % len(visible))

    def _row_values(self, snap, index):
        status = snap["status"]
        speed = snap["speed_text"] if status in ("downloading", "connecting", "pausing") else "-"
        eta = snap["eta_text"] if status == "downloading" else "-"
        size = human_size(snap["total_size"]) if snap["total_size"] else human_size(snap["downloaded"])
        return (snap["file_name"], size, STATUS_LABELS.get(status, status.title()),
                progress_text(snap["progress"]), speed, eta,
                format_timestamp(snap["added_at"]))

    def _row_tags(self, snap, index):
        tags = []
        status = snap["status"]
        if status in ("completed", "error", "paused", "queued"):
            tags.append(status)
        tags.append("odd" if index % 2 else "even")
        return tuple(tags)

    def _update_status(self, stats):
        active = stats["active"]
        queued = stats["queued"]
        self.status_left.config(
            text="%d active   -   %d queued   -   %d total"
                 % (active, queued, stats["total"]))
        speed = stats["speed"]
        self.status_speed.config(text="%s total" % (human_speed(speed) if speed > 0 else "0 B/s"))

    def _update_summary(self):
        if not hasattr(self, "summary_label"):
            return
        limit = int(self.settings.get("speed_limit_kbps", 0))
        limit_text = "Off" if limit <= 0 else "%d KB/s" % limit
        text = ("Connections / download: %s\nParallel downloads: %s\nSpeed limit: %s"
                % (self.settings.get("connections"),
                   self.settings.get("max_concurrent"), limit_text))
        self.summary_label.config(text=text)

    def _check_completions(self, snapshots):
        for snap in snapshots:
            old = self._prev_status.get(snap["id"])
            if old and old != "completed" and snap["status"] == "completed":
                self.set_message("Download complete: %s" % snap["file_name"], 6000)
                self.bell()
                flash_window(self)
                self.show_completion(snap)
        self._prev_status = {s["id"]: s["status"] for s in snapshots}

    # ----- completion window --------------------------------------------
    def show_completion(self, snap):
        """Show the "Download Completed" card, queued behind any open one."""
        if not self.settings.get("notify_on_complete", True):
            return
        info = {
            "file_name": snap.get("file_name") or "",
            "target_path": snap.get("target_path") or "",
            "category": snap.get("category") or category_for(snap.get("file_name") or ""),
            "size_text": snap.get("size_text") or human_size(snap.get("total_size")),
        }
        dialog = self._completion_dialog
        if dialog is not None and dialog.winfo_exists():
            self._completion_queue.append(info)
            return
        self._open_completion(info)

        # Balloon tip so completion is visible even when minimized to tray.
        try:
            if not self.winfo_viewable() and self._tray is not None:
                self._tray.notify(
                    "Download Completed",
                    "%s — %s" % (info["size_text"], info["file_name"]),
                )
        except Exception:
            pass

    def _open_completion(self, info):
        if self._quitting:
            return
        self._completion_dialog = CompletionDialog(self, info)

    def _completion_closed(self, dialog):
        if self._completion_dialog is dialog:
            self._completion_dialog = None
        if self._completion_queue and not self._quitting:
            info = self._completion_queue.pop(0)
            try:
                self.after(250, lambda: self._open_completion(info))
            except Exception:
                pass

    # ----- details panel ------------------------------------------------
    def _update_details(self):
        pass

    def _show_details_placeholder(self):
        pass


    def set_message(self, text, timeout=4000):
        self.status_msg.config(text=text)
        if self._message_job:
            try:
                self.after_cancel(self._message_job)
            except Exception:
                pass
        self._message_job = self.after(timeout, lambda: self.status_msg.config(text=""))

    # ----- selection / actions -----------------------------------------
    def _selected_tasks(self):
        tasks = []
        for iid in self.tree.selection():
            snap = self._snap_by_id.get(iid)
            if not snap:
                continue
            task = self.manager.get(snap["id"])
            if task is not None:
                tasks.append(task)
        return tasks

    def _select_all(self, _event=None):
        self.tree.selection_set(self.tree.get_children())
        return "break"

    def _toggle_selected(self, _event=None):
        for task in self._selected_tasks():
            if task.status in ("downloading", "connecting", "queued", "pausing"):
                self.manager.pause(task)
            elif task.status in ("paused", "error"):
                self.manager.start(task)
        return "break"

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        snap = self._snap_by_id.get(iid)
        if snap and snap["status"] == "completed":
            self.open_selected_file()
        else:
            self.open_selected_folder()

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def sort_by(self, column):
        if str(self.settings.get("sort_column")) == column:
            self.settings.set("sort_desc", not bool(self.settings.get("sort_desc")))
        else:
            self.settings.set("sort_column", column)
            self.settings.set("sort_desc", column == "added")
        self.settings.save()
        self._visible_ids = []
        self.refresh()

    def start_selected(self):
        tasks = self._selected_tasks()
        for task in tasks:
            if task.status in ("paused", "error"):
                self.manager.start(task)
        if tasks:
            self.set_message("Starting %d download(s)." % len(tasks))

    def pause_selected(self):
        tasks = self._selected_tasks()
        for task in tasks:
            if task.status in ("queued", "connecting", "downloading", "pausing"):
                self.manager.pause(task)
        if tasks:
            self.set_message("Pausing %d download(s)." % len(tasks))

    def stop_selected(self):
        tasks = self._selected_tasks()
        for task in tasks:
            if task.status in ("queued", "connecting", "downloading", "pausing"):
                self.manager.pause(task)
        if tasks:
            self.set_message("Stopped %d download(s)." % len(tasks))

    def remove_selected(self):
        tasks = self._selected_tasks()
        if not tasks:
            return
        message = ("Remove %d download(s)?\n\n"
                   "Partial data will be deleted. Completed files are kept." % len(tasks))
        if not messagebox.askyesno(APP_NAME, message, parent=self):
            return
        for task in tasks:
            self.manager.remove(task, delete_temp=True)
        self._visible_ids = []
        self.refresh()
        self.set_message("Removed %d download(s)." % len(tasks))

    def pause_all(self):
        self.manager.pause_all()
        self.set_message("Paused all active downloads.")

    def resume_all(self):
        self.manager.resume_all()
        self.set_message("Resumed all unfinished downloads.")

    def clear_completed(self):
        completed = [s for s in self._all_snaps if s["status"] == "completed"]
        if not completed:
            self.set_message("There are no completed downloads to clear.")
            return
        if not messagebox.askyesno(
                APP_NAME,
                "Remove %d completed item(s) from the list?\n\n"
                "The downloaded files will not be deleted." % len(completed),
                parent=self):
            return
        self.manager.clear_completed(delete_files=False)
        self._visible_ids = []
        self.set_message("Cleared %d completed item(s)." % len(completed))

    def open_selected_file(self):
        tasks = self._selected_tasks()
        if not tasks:
            return
        path = Path(tasks[0].target_path)
        if path.exists():
            try:
                open_path(path)
            except Exception as exc:
                messagebox.showerror(APP_NAME, "Could not open the file:\n%s" % exc, parent=self)
        else:
            self.set_message("The file is not available yet.")

    def open_selected_folder(self):
        tasks = self._selected_tasks()
        if not tasks:
            return
        try:
            reveal_path(tasks[0].target_path)
        except Exception:
            pass

    def copy_selected_url(self):
        tasks = self._selected_tasks()
        if not tasks:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(tasks[0].url)
            self.set_message("Copied the download URL.")
        except Exception:
            pass

    # ----- drag and drop ------------------------------------------------
    def _enable_drag_drop(self):
        """Make every visible widget accept a dropped link or shortcut file."""
        if not self._dnd_ready:
            return
        self._register_drop_targets(self)

    def _register_drop_targets(self, widget):
        try:
            widget.drop_target_register(DND_TEXT, DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_drop)
            widget.dnd_bind("<<DropEnter>>", self._on_drop_enter)
            widget.dnd_bind("<<DropLeave>>", self._on_drop_leave)
            if widget not in self._drop_targets:
                self._drop_targets.append(widget)
        except Exception:
            pass
        for child in widget.winfo_children():
            self._register_drop_targets(child)

    def _on_drop_enter(self, _event=None):
        try:
            self.status_msg.config(fg=ACCENT, text="Drop the link here to add a download.")
        except Exception:
            pass
        return "copy"

    def _on_drop_leave(self, _event=None):
        try:
            self.status_msg.config(fg=MUTED, text="")
        except Exception:
            pass
        return "copy"

    def _on_drop(self, event):
        urls = self._parse_drop(getattr(event, "data", "") or "")
        try:
            self.status_msg.config(fg=MUTED, text="")
        except Exception:
            pass
        if not urls:
            self.set_message("That drop did not contain an http:// or https:// link.", 6000)
            return "copy"
        was_hidden = False
        try:
            was_hidden = self.state() == "withdrawn"
            if was_hidden:
                self._show_from_tray()
        except Exception:
            pass
        if len(urls) == 1:
            if was_hidden:
                self.after(350, lambda: self.open_add_dialog(urls[0]))
            else:
                self.open_add_dialog(urls[0])
        else:
            self.add_urls(urls[:50], start_now=True)
            self.bring_to_front()
        return "copy"

    def _parse_drop(self, data):
        """Turn tkdnd drop data (URL text or files) into a list of links."""
        data = (data or "").replace("\x00", "").strip()
        if not data:
            return []
        items = _split_drop_items(data)
        urls = []
        for item in items:
            item = (item or "").strip()
            if not item:
                continue
            if re.match(r"^https?://", item, re.I):
                urls.append(item)
                continue
            path = None
            if item.lower().startswith("file://"):
                parsed = urlparse(item)
                path = unquote(parsed.path)
                if re.match(r"^/[a-z]:", path, re.I):
                    path = path[1:]
                path = path.replace("/", os.sep)
            elif re.match(r"^[a-z]:[\\/]", item, re.I) or item.startswith("\\\\"):
                path = item
            if not path:
                continue
            try:
                if path.lower().endswith(".url") or path.lower().endswith(".webloc"):
                    text = Path(path).read_text(encoding="utf-8", errors="ignore")
                    urls.extend(re.findall(r"(?im)^\s*(?:URL|url)\s*=\s*(\S+)", text))
                elif path.lower().endswith((".txt", ".htm", ".html")):
                    text = Path(path).read_text(encoding="utf-8", errors="ignore")
                    urls.extend(re.findall(r"https?://[^\s\"'<>]+", text))
            except OSError:
                continue
        unique, seen = [], set()
        for url in urls:
            url = url.strip().rstrip(".,;")
            if url and url not in seen:
                seen.add(url)
                unique.append(url)
        return unique

    # ----- add dialog ---------------------------------------------------
    def open_add_dialog(self, prefill=""):
        if getattr(self, "_add_open", False):
            for child in self.winfo_children():
                if isinstance(child, AddDownloadDialog):
                    if prefill:
                        child.set_url(prefill)
                    child.deiconify()
                    child.lift()
                    child.focus_force()
                    return
            return
        AddDownloadDialog(self, prefill)

    def open_batch_dialog(self):
        BatchDialog(self)

    def add_urls(self, urls, save_dir=None, file_name=None, referer="", cookies="",
                 connections=None, start_now=True):
        added = []
        failed = []
        for url in urls:
            try:
                if ytdl.available() and ytdl.can_handle(str(url)):
                    task, is_new = self.manager.add_ytdl(
                        str(url), "", kind="video", save_dir=save_dir,
                        referer=referer, cookie_header=cookies, start_now=start_now)
                else:
                    task, is_new = self.manager.add(
                        str(url), save_dir=save_dir, file_name=file_name, referer=referer,
                        cookie_header=cookies, connections=connections, start_now=start_now)
                added.append((task, is_new))
            except Exception as exc:
                failed.append("%s: %s" % (url, exc))
        if added:
            self._pending_select = added[0][0].id
            self._visible_ids = []
            self.refresh()
            if len(added) == 1:
                task, is_new = added[0]
                if is_new:
                    self.set_message("Added %s" % task.file_name)
                else:
                    self.set_message("%s is already in the list." % task.file_name)
            else:
                self.set_message("Added %d download(s)." % len(added))
        if failed:
            messagebox.showwarning(APP_NAME, "Some links could not be added:\n\n" +
                                   "\n".join(failed[:6]), parent=self)

    def import_urls(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Import URLs", parent=self,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                found = re.findall(r"https?://[^\s\"'<>]+", fh.read())
        except Exception as exc:
            messagebox.showerror(APP_NAME, "Could not read the file:\n%s" % exc, parent=self)
            return
        if found:
            self.add_urls(found, start_now=True)
        else:
            messagebox.showinfo(APP_NAME, "No http:// or https:// URLs were found.", parent=self)

    # ----- grabber dialog -----------------------------------------------
    def open_grabber(self):
        GrabberDialog(self)

    # ----- settings dialog ----------------------------------------------
    def open_settings(self):
        SettingsDialog(self)

    # ----- integration --------------------------------------------------
    def _ensure_extension_copy(self):
        try:
            source = resource_path("browser_extension")
            if source.exists():
                target = self.settings.base_dir / "browser_extension"
                shutil.copytree(source, target, dirs_exist_ok=True)
        except Exception:
            pass

    def _ensure_start_menu_entry(self):
        """Add the app to the Start Menu / Desktop once per installation.

        The installer already creates an all-users shortcut. Reuse it instead
        of adding a per-user duplicate, which would show twice in the Start
        Menu.
        """
        try:
            link = start_menu_shortcut_path(APP_NAME)
            if link is None:
                link = create_start_menu_shortcut(APP_NAME)
                if link:
                    pin_to_start(APP_NAME)
            if link:
                self.settings.set("start_menu_shortcut", str(link))
            if desktop_shortcut_path(APP_NAME) is None:
                create_desktop_shortcut(APP_NAME)
        except Exception:
            pass

    def start_api(self):
        if self.api is not None:
            self.api.stop()
            self.api = None
        if not self.settings.get("api_enabled"):
            return
        self.api = BrowserAPI(
            int(self.settings.get("api_port", 53991)),
            on_add=self.api_add,
            on_list=self.manager.snapshots,
            on_focus=lambda: self.after(0, self.bring_to_front),
            on_pause=self.api_pause,
            on_resume=self.api_resume,
            on_formats=self.api_formats,
        )
        ok, error = self.api.start()
        if not ok:
            self.set_message("Browser API unavailable: %s" % error, 8000)

    def api_add(self, payload):
        urls = payload.get("urls") or payload.get("url") or ""
        if isinstance(urls, str):
            urls = [urls]
        if not isinstance(urls, list) or not urls:
            raise ValueError("No URL supplied.")
        added = []
        selector = str(payload.get("format") or "")
        kind = str(payload.get("kind") or "video")
        for url in urls:
            if ytdl.available() and (selector or ytdl.can_handle(str(url))):
                task, is_new = self.manager.add_ytdl(
                    str(url),
                    selector,
                    kind=kind if kind in ("video", "audio") else "video",
                    save_dir=payload.get("save_dir") or None,
                    referer=payload.get("referer") or "",
                    cookie_header=payload.get("cookies") or "",
                    start_now=bool(payload.get("start", True)),
                )
            else:
                task, is_new = self.manager.add(
                    str(url),
                    save_dir=payload.get("save_dir") or None,
                    file_name=payload.get("file_name") or None,
                    referer=payload.get("referer") or "",
                    cookie_header=payload.get("cookies") or "",
                    start_now=bool(payload.get("start", True)),
                )
            added.append({"id": task.id, "file_name": task.file_name,
                          "duplicate": not is_new})
        if added:
            self._pending_select = added[0]["id"]
            self.after(0, self.refresh)
            self.after(0, self.bring_to_front)
        first = added[0]
        return {"downloads": added, "id": first["id"],
                "file_name": first["file_name"], "duplicate": first["duplicate"]}

    def api_pause(self, task_id):
        task = self.manager.get(task_id)
        if task is None:
            return False
        self.manager.pause(task)
        return True

    def api_resume(self, task_id):
        task = self.manager.get(task_id)
        if task is None:
            return False
        self.manager.resume(task)
        return True

    def api_formats(self, url):
        if not ytdl.available():
            raise ValueError("yt-dlp is not available in this build.")
        if not ytdl.can_handle(url):
            raise ValueError("This page is not a supported media page.")
        return ytdl.list_formats(url)

    def bring_to_front(self):
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.attributes("-topmost", True)
            self.after(300, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except tk.TclError:
            pass

    def _poll_clipboard(self):
        self.after(1200, self._poll_clipboard)
        if not self.settings.get("clipboard_monitor") or self._add_open:
            return
        try:
            text = self.clipboard_get().strip()
        except Exception:
            return
        if not text or text == self._clip_last:
            return
        self._clip_last = text
        if re.match(r"^https?://\S+$", text) and not self.manager.find_duplicate(text):
            self.set_message("Clipboard link detected. Press Ctrl+N to add it.")
            self._pending_clipboard = text

    def register_protocol(self):
        try:
            import winreg
            command = '%s "%%1"' % app_command()
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\shdm") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:SH Download Manager link")
                winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
                with winreg.CreateKey(key, r"shell\open\command") as cmd:
                    winreg.SetValueEx(cmd, "", 0, winreg.REG_SZ, command)
            self.set_message("Registered shdm:// links for the current user.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, "Could not register the link handler:\n%s" % exc,
                                 parent=self)

    def set_autostart(self, enabled):
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
                if enabled and getattr(sys, "frozen", False):
                    winreg.SetValueEx(key, "SHDownloadManager", 0, winreg.REG_SZ,
                                      "%s --minimized" % app_command())
                else:
                    try:
                        winreg.DeleteValue(key, "SHDownloadManager")
                    except FileNotFoundError:
                        pass
        except Exception:
            pass

    def open_extension_folder(self):
        self._ensure_extension_copy()
        path = self.settings.base_dir / "browser_extension"
        if path.exists():
            try:
                os.startfile(str(path))
                return
            except Exception:
                pass
        messagebox.showinfo(APP_NAME, "The extension files were not bundled with this build.",
                            parent=self)

    def open_extension_help(self):
        path = self.settings.base_dir / "browser_extension" / "README.md"
        if not path.exists():
            self._ensure_extension_copy()
        if path.exists():
            try:
                os.startfile(str(path))
                return
            except Exception:
                pass
        self.open_extension_folder()

    def open_readme(self):
        for candidate in (resource_path("README.md"), Path.cwd() / "README.md"):
            if candidate.exists():
                try:
                    os.startfile(str(candidate))
                    return
                except Exception:
                    break
        messagebox.showinfo(APP_NAME, "README.md was not bundled with this build.", parent=self)

    def show_about(self):
        AboutDialog(self)

    def on_close(self):
        """Window close button: keep running in the tray unless asked to quit."""
        if self.settings.get("close_to_tray", True) and self._tray is not None:
            self.hide_to_tray()
            return
        self.quit_app()


def _draw_file_glyph(canvas, category, color=None, box=34):
    """Draw a small file-type glyph on a canvas and return its item ids."""
    color = color or CATEGORY_COLORS.get(category, CATEGORY_COLORS["Other"])
    m = 2.0
    x0, y0, x1, y1 = m, m, box - m, box - m
    items = []

    def line(*pts, **kw):
        items.append(canvas.create_line(*pts, fill=color, capstyle="round",
                                        joinstyle="round", **kw))

    if category == "Video":
        items.append(canvas.create_rectangle(x0, y0 + 1, x1, y1 - 1, outline=color,
                                             width=1.7))
        for y in (y0 + 5, y0 + 11, y0 + 17):
            items.append(canvas.create_rectangle(x0 + 2, y, x0 + 4.4, y + 2.4,
                                                 fill=color, outline=color))
            items.append(canvas.create_rectangle(x1 - 4.4, y, x1 - 2, y + 2.4,
                                                 fill=color, outline=color))
        cx, cy = box / 2 + 1, box / 2
        items.append(canvas.create_polygon(cx - 3.6, cy - 5.4, cx - 3.6, cy + 5.4,
                                           cx + 5.8, cy, fill=color, outline=color))
        return items

    if category == "Music":
        items.append(canvas.create_oval(5, 22, 14, 29, fill=color, outline=color))
        items.append(canvas.create_oval(17, 19, 26, 26, fill=color, outline=color))
        line(12.4, 7, 12.4, 25.5, width=2.2)
        line(22.4, 4, 22.4, 22.5, width=2.2)
        line(12.4, 7, 22.4, 4, width=2.6)
        return items

    if category == "Images":
        items.append(canvas.create_rectangle(x0, y0 + 3, x1, y1 - 3,
                                             outline=color, width=1.7))
        items.append(canvas.create_oval(7, 9, 12, 14, outline=color, width=1.5))
        items.append(canvas.create_polygon(5, 27, 13, 16, 19, 23, 22, 19, 29, 27,
                                           fill=color, outline=color))
        return items

    if category == "Compressed":
        items.append(canvas.create_rectangle(x0 + 1, y0 + 4, x1 - 1, y1 - 2,
                                             outline=color, width=1.7))
        line(box / 2, y0 + 4, box / 2, y1 - 2, width=1.7, dash=(2, 2))
        items.append(canvas.create_rectangle(box / 2 - 3, y0 - 1, box / 2 + 3,
                                             y0 + 7, outline=color, width=1.6))
        return items

    if category == "Programs":
        items.append(canvas.create_rectangle(x0, y0 + 3, x1, y1 - 3,
                                             outline=color, width=1.7))
        line(x0, y0 + 10, x1, y0 + 10, width=1.5)
        for cx in (x0 + 4, x0 + 8):
            items.append(canvas.create_oval(cx - 1.4, y0 + 5.5, cx + 1.4, y0 + 8.3,
                                            fill=color, outline=color))
        return items

    # Documents and anything else: a page with a folded corner.
    fold = 9
    items.append(canvas.create_polygon(x0, y0, x1 - fold, y0, x1, y0 + fold,
                                       x1, y1, x0, y1, outline=color, fill="",
                                       width=1.7, joinstyle="round"))
    line(x1 - fold, y0, x1 - fold, y0 + fold, x1, y0 + fold, width=1.7)
    for index, y in enumerate((15, 20, 25)):
        line(x0 + 5, y, x1 - 5 - (4 if index == 2 else 0), y, width=1.7)
    return items


def _draw_check(canvas, color, size=18):
    """Draw a check mark scaled to the canvas."""
    canvas.create_line(size * 0.18, size * 0.53, size * 0.42, size * 0.78,
                       size * 0.84, size * 0.24, fill=color,
                       width=max(1.8, size / 8.0), capstyle="round",
                       joinstyle="round")


def _draw_gear(canvas, color, size=24):
    """Draw a gear glyph filling the canvas; returns its item ids."""
    cx = cy = size / 2.0
    r = size * 0.29
    items = []
    for step in range(8):
        angle = math.radians(step * 45 + 22.5)
        items.append(canvas.create_line(
            cx + math.cos(angle) * (r - 0.8), cy + math.sin(angle) * (r - 0.8),
            cx + math.cos(angle) * (r + size * 0.11),
            cy + math.sin(angle) * (r + size * 0.11),
            fill=color, width=max(1.8, size * 0.10), capstyle="round"))
    items.append(canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                    outline=color, width=max(1.3, size * 0.07)))
    items.append(canvas.create_oval(cx - r * 0.38, cy - r * 0.38,
                                    cx + r * 0.38, cy + r * 0.38,
                                    outline=color, width=max(1.3, size * 0.07)))
    return items


class _Dialog(tk.Toplevel):
    """Shared dark-themed modal frame with title bar and content area."""

    def __init__(self, app, title, width, height, modal=True):
        super().__init__(app)
        self.app = app
        self.modal = modal
        self.title(title)
        self.configure(bg=BG)
        self.resizable(False, False)
        if modal:
            self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        apply_dark_frame(self)
        self.bind("<Map>", lambda _e: apply_dark_frame(self), add="+")
        app.update_idletasks()
        x = app.winfo_rootx() + max(0, (app.winfo_width() - width) // 2)
        y = app.winfo_rooty() + max(0, (app.winfo_height() - height) // 2)
        self.geometry("%dx%d+%d+%d" % (width, height, x, y))
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=16, pady=12)
        try:
            self.iconbitmap(str(resource_path("assets/icon.ico")))
        except Exception:
            pass
        self.after(30, self._grab)

    def _grab(self):
        try:
            self.deiconify()
            self.lift()
            if self.modal:
                self.grab_set()
                self.focus_force()
        except tk.TclError:
            pass
        # Re-apply once the dialog is mapped: DWM can reset the attribute while
        # the window is being shown.
        self.after(200, lambda: apply_dark_frame(self))

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def heading(self, text):
        tk.Label(self.body, text=text, bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(0, 8))

    def hint(self, text):
        tk.Label(self.body, text=text, bg=BG, fg=MUTED,
                 font=("Segoe UI", 8), justify="left").pack(anchor="w", pady=(0, 8))

    def field(self, label, value="", width=48):
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=BG, fg=MUTED, width=18, anchor="w",
                 font=("Segoe UI", 9)).pack(side="left")
        entry = ttk.Entry(row, width=width)
        entry.insert(0, value)
        entry.pack(side="left", fill="x", expand=True)
        return entry

    def buttons(self, primary_text, primary_command, secondary_text="Cancel"):
        bar = tk.Frame(self.body, bg=BG)
        bar.pack(fill="x", pady=(14, 0))
        ttk.Button(bar, text=primary_text, style="Accent.TButton",
                   command=primary_command).pack(side="right")
        if secondary_text:
            ttk.Button(bar, text=secondary_text, style="Tool.TButton",
                       command=self.close).pack(side="right", padx=6)
        return bar


class AddDialog(_Dialog):
    """Add-URL dialog kept for compatibility with older call sites."""

    def __init__(self, app, prefill=""):
        AddDownloadDialog(app, prefill)


class CompletionDialog(_Dialog):
    """Non-modal "Download Completed" card with Open / Open Folder / Open with."""

    def __init__(self, app, info):
        self.info = dict(info or {})
        self.target = Path(self.info.get("target_path") or "")
        name = self.target.name or self.info.get("file_name") or "Download"
        super().__init__(app, name, 430, 150, modal=False)
        self._closing = False

        body = self.body
        body.grid_columnconfigure(0, weight=1)

        head = tk.Frame(body, bg=BG)
        head.grid(row=0, column=0, sticky="ew")

        icon = tk.Canvas(head, width=32, height=32, bg=BG, highlightthickness=0, bd=0)
        _draw_file_glyph(icon, self.info.get("category") or category_for(name), box=32)
        icon.pack(side="left")

        titles = tk.Frame(head, bg=BG)
        titles.pack(side="left", fill="x", expand=True, padx=(10, 0))

        title_row = tk.Frame(titles, bg=BG)
        title_row.pack(anchor="w")
        check = tk.Canvas(title_row, width=16, height=16, bg=BG,
                          highlightthickness=0, bd=0)
        _draw_check(check, SUCCESS, 16)
        check.pack(side="left")
        tk.Label(title_row, text="Download Completed", bg=BG, fg=SUCCESS,
                 font=("Segoe UI Semibold", 11)).pack(side="left", padx=(5, 0))

        meta = "%s   %s" % (self.info.get("size_text") or "Unknown", name)
        tk.Label(titles, text=meta, bg=BG, fg=MUTED, font=("Segoe UI", 8),
                 anchor="w", justify="left", wraplength=330).pack(anchor="w",
                                                                  pady=(2, 0))

        bar = tk.Frame(body, bg=BG)
        bar.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(bar, text="Open", style="Card.TButton",
                   command=self.open_file).pack(side="left")
        ttk.Button(bar, text="Open Folder", style="Card.TButton",
                   command=self.open_folder).pack(side="left", padx=(5, 0))
        self._gear_button(bar, "Settings for downloads and this window",
                          self.open_settings).pack(side="left", padx=(5, 0))
        ttk.Button(bar, text="Close", style="Card.TButton",
                   command=self.close).pack(side="right")

        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<Return>", lambda _e: self.open_file())

        self.update_idletasks()
        width = 430
        height = max(140, self.winfo_reqheight() + 4)
        left, top, right, bottom = self._work_area()
        x = max(left + 8, right - width - 18)
        y = max(top + 8, bottom - height - 18)
        self.geometry("%dx%d+%d+%d" % (width, height, x, y))
        self.after(120, self._bring_forward)

    # ----- helpers ------------------------------------------------------
    def _gear_button(self, parent, tooltip, command):
        canvas = tk.Canvas(parent, width=26, height=26, bg=BG,
                           highlightthickness=0, bd=0, cursor="hand2")
        items = _draw_gear(canvas, MUTED, 24)

        def recolor(color):
            for item in items:
                if canvas.type(item) == "line":
                    canvas.itemconfigure(item, fill=color)
                else:
                    canvas.itemconfigure(item, outline=color)

        canvas.bind("<Enter>", lambda _e: recolor("#ffffff"))
        canvas.bind("<Leave>", lambda _e: recolor(MUTED))
        canvas.bind("<Button-1>", lambda _e: command())
        self.app._add_tooltip(canvas, tooltip)
        return canvas

    def _work_area(self):
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                         ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def _bring_forward(self):
        """Float the card above other windows for a moment, without focus."""
        try:
            self.attributes("-topmost", True)
            self.lift()
            self.after(2500, self._drop_topmost)
        except tk.TclError:
            pass

    def _drop_topmost(self):
        try:
            self.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _missing(self):
        messagebox.showinfo(APP_NAME,
                            "The file is no longer at:\n%s" % self.target,
                            parent=self)
        return "break"

    # ----- actions ------------------------------------------------------
    def open_file(self):
        if not self.target.is_file():
            return self._missing()
        try:
            open_path(self.target)
        except Exception as exc:
            messagebox.showerror(APP_NAME, "Could not open the file:\n%s" % exc,
                                 parent=self)
            return "break"
        self.close()
        return "break"

    def open_folder(self):
        try:
            reveal_path(self.target)
        except Exception:
            pass
        self.close()
        return "break"

    def open_settings(self):
        self.app.open_settings()

    def close(self):
        if self._closing:
            return
        self._closing = True
        try:
            self.app._completion_closed(self)
        except Exception:
            pass
        super().close()


def _name_from_url(url):
    """Guess a file name from the last path segment of a link."""
    try:
        path = unquote(urlparse(url).path)
    except Exception:
        return ""
    base = path.rstrip("/").rsplit("/", 1)[-1].strip()
    if base and "." in base:
        return sanitize_filename(base)
    return ""


class AddDownloadDialog(_Dialog):
    """Single-link confirmation dialog (URL, category, folder and file name)."""

    def __init__(self, app, prefill=""):
        if getattr(app, "_add_open", False):
            app.open_add_dialog(prefill)
            return
        app._add_open = True
        super().__init__(app, "Add Download", 700, 306)
        self.app = app
        self.settings = app.settings
        self._probe_token = 0
        self._probe_timer = None
        self._probe_result = None
        self._name_auto = True
        self._category_auto = True

        self.url_var = tk.StringVar(value=(prefill or "").strip())
        self.size_var = tk.StringVar(value="")
        self.folder_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.category_var = tk.StringVar(value=CATEGORY_NAMES[0])
        self.use_category = tk.BooleanVar(
            value=bool(self.settings.get("use_category_folders", True)))

        body = self.body
        body.grid_columnconfigure(0, weight=1)

        # URL field with a paste button inside the field, like the reference.
        url_card = tk.Frame(body, bg=INPUT, highlightbackground=BORDER,
                            highlightthickness=1)
        url_card.grid(row=0, column=0, sticky="ew")
        self.url_entry = tk.Entry(
            url_card, textvariable=self.url_var, bg=INPUT, fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=0, font=("Segoe UI", 10),
            selectbackground=ACCENT, selectforeground="#ffffff")
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(10, 4), pady=6)
        self._paste_button(url_card).pack(side="right", padx=(0, 7))

        options = tk.Frame(body, bg=BG)
        options.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        ttk.Checkbutton(options, text="Use Category", variable=self.use_category,
                        style="Dialog.TCheckbutton",
                        command=self._toggle_category).pack(side="left")
        self.category_box = ttk.Combobox(
            options, textvariable=self.category_var, style="Dialog.TCombobox",
            values=CATEGORY_CHOICES, state="readonly", width=13,
            font=("Segoe UI", 9))
        self.category_box.pack(side="left", padx=(10, 5))
        self.category_box.bind("<<ComboboxSelected>>", self._on_category_picked)
        self._flat_button(options, "+", "New folder inside the download folder",
                          self.new_folder).pack(side="left")

        self.size_label = tk.Label(options, textvariable=self.size_var, bg=BG,
                                   fg=MUTED, font=("Segoe UI", 8))
        self.size_label.pack(side="right")
        disk = tk.Canvas(options, width=15, height=15, bg=BG, highlightthickness=0, bd=0)
        disk.create_rectangle(2, 5, 13, 13, outline=SUBTLE, width=1)
        disk.create_oval(4, 1, 11, 6, outline=SUBTLE, width=1, fill=BG)
        disk.pack(side="right", padx=(0, 5))

        # Folder row.
        folder_row = tk.Frame(body, bg=BG)
        folder_row.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.folder_box = ttk.Combobox(
            folder_row, textvariable=self.folder_var, style="Dialog.TCombobox",
            values=self._folder_values(), font=("Segoe UI", 9))
        self.folder_box.pack(side="left", fill="x", expand=True)
        self._flat_button(folder_row, "\u21bb", "Check the link again",
                          self.start_probe).pack(side="left", padx=(6, 0))
        self._flat_button(folder_row, "\u2699", "Open this folder",
                          self.open_folder).pack(side="left", padx=(6, 0))

        # File name row.
        name_card = tk.Frame(body, bg=INPUT, highlightbackground=BORDER,
                             highlightthickness=1)
        name_card.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.name_entry = tk.Entry(
            name_card, textvariable=self.name_var, bg=INPUT, fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=0, font=("Segoe UI", 10),
            selectbackground=ACCENT, selectforeground="#ffffff")
        self.name_entry.pack(fill="x", padx=10, pady=6)
        self.name_entry.bind("<KeyRelease>", self._on_name_typed)

        bar = tk.Frame(body, bg=BG)
        bar.grid(row=4, column=0, sticky="ew", pady=(16, 0))
        ttk.Button(bar, text="Add", style="Tool.TButton", width=9,
                   command=lambda: self.submit(False)).pack(side="left")
        ttk.Button(bar, text="Download", style="Accent.TButton", width=13,
                   command=lambda: self.submit(True)).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Cancel", style="Tool.TButton", width=9,
                   command=self.close).pack(side="right")

        self.url_entry.bind("<KeyRelease>", self._on_url_typed)
        self.url_entry.bind("<Return>", lambda _e: self.submit(True))
        self.name_entry.bind("<Return>", lambda _e: self.submit(True))
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<Return>", lambda _e: self.submit(True))

        self._sync_folder()
        if self.url_var.get():
            self.start_probe()
        self.update_idletasks()
        self.geometry("%dx%d+%d+%d" % (700, self.winfo_reqheight() + 8,
                                       self.winfo_x(), self.winfo_y()))
        self.after(60, self.url_entry.focus_set)

    # ----- widgets ------------------------------------------------------
    def _flat_button(self, parent, text, tooltip, command):
        button = tk.Label(parent, text=text, bg="#2d2d30", fg=TEXT,
                          font=("Segoe UI Symbol", 10), padx=8, pady=2,
                          cursor="hand2")
        button.bind("<Button-1>", lambda _e: command())
        button.bind("<Enter>", lambda _e: button.configure(bg=HOVER, fg="#ffffff"))
        button.bind("<Leave>", lambda _e: button.configure(bg="#2d2d30", fg=TEXT))
        self.app._add_tooltip(button, tooltip)
        return button

    def _paste_button(self, parent):
        canvas = tk.Canvas(parent, width=20, height=20, bg=INPUT,
                           highlightthickness=0, bd=0, cursor="hand2")
        canvas.create_rectangle(4, 3, 15, 17, outline=MUTED, width=1)
        canvas.create_rectangle(6.5, 1, 11.5, 5, outline=MUTED, fill=INPUT, width=1)
        canvas.create_line(7, 9, 13, 9, fill=MUTED)
        canvas.create_line(7, 12, 13, 12, fill=MUTED)
        canvas.bind("<Button-1>", lambda _e: self.paste_url())
        canvas.bind("<Enter>", lambda _e: canvas.itemconfigure("all", outline="#ffffff"))
        canvas.bind("<Leave>", lambda _e: canvas.itemconfigure("all", outline=MUTED))
        self.app._add_tooltip(canvas, "Paste a link from the clipboard")
        return canvas

    # ----- url / probe --------------------------------------------------
    def _on_url_typed(self, event=None):
        if event is not None and event.keysym in ("Return", "Tab", "Escape"):
            return
        self._schedule_probe(650)

    def _on_name_typed(self, _event=None):
        self._name_auto = False

    def _schedule_probe(self, delay):
        if self._probe_timer:
            try:
                self.after_cancel(self._probe_timer)
            except Exception:
                pass
        self._probe_timer = self.after(delay, self.start_probe)

    def paste_url(self):
        try:
            text = self.clipboard_get()
        except Exception:
            text = ""
        text = (text or "").strip()
        if not text:
            return
        found = re.search(r"https?://\S+", text)
        self.set_url(found.group(0) if found else text)

    def set_url(self, url):
        self.url_var.set((url or "").strip())
        self._name_auto = True
        self._category_auto = True
        self.start_probe()
        self.url_entry.focus_set()

    def start_probe(self):
        self._probe_timer = None
        url = self.url_var.get().strip()
        self._probe_token += 1
        token = self._probe_token
        self._probe_result = None
        if not re.match(r"^https?://", url, re.I):
            self.size_var.set("")
            return
        media = ytdl.can_handle(url)
        self.size_var.set("Media page" if media else "Checking size...")
        self._apply_url_defaults(url, media)
        self._sync_folder()

        def work():
            try:
                self._probe_result = (token, grabber.probe_link(url))
            except Exception as exc:  # noqa: BLE001
                self._probe_result = (token, grabber.LinkInfo(url=url, kind="file",
                                                             error=str(exc)))

        threading.Thread(target=work, daemon=True).start()
        self.after(120, lambda: self._poll_probe(token))

    def _poll_probe(self, token):
        try:
            if token != self._probe_token or not self.winfo_exists():
                return
        except Exception:
            return
        result = self._probe_result
        if result and result[0] == token:
            self._probe_result = None
            self._probe_done(token, result[1])
            return
        self.after(120, lambda: self._poll_probe(token))

    def _probe_done(self, token, info):
        if token != self._probe_token:
            return
        url = self.url_var.get().strip()
        media = ytdl.can_handle(url)
        if info.size:
            self.size_var.set(human_size(info.size))
        elif media:
            self.size_var.set("Media page")
        else:
            self.size_var.set("Unknown size")
        if info.filename and self._name_auto and not media:
            self.name_var.set(sanitize_filename(info.filename))
            if self._category_auto:
                category = category_for(info.filename)
                if category in CATEGORY_CHOICES:
                    self.category_var.set(category)
                    self._sync_folder()

    def _apply_url_defaults(self, url, media=False):
        name = "" if media else _name_from_url(url)
        if name and self._name_auto:
            self.name_var.set(name)
        if media:
            self.name_var.set("")
        if self._category_auto and name:
            category = category_for(name)
            if category in CATEGORY_CHOICES:
                self.category_var.set(category)

    # ----- folder / category --------------------------------------------
    def _folder_values(self):
        paths = []
        for path in self.settings.get("recent_dirs") or []:
            if isinstance(path, str) and path:
                paths.append(path)
        for name in CATEGORY_CHOICES:
            try:
                paths.append(str(self.settings.download_dir_for(name)))
            except Exception:
                pass
        unique = []
        seen = set()
        for path in paths:
            key = path.lower()
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique

    def _toggle_category(self):
        if self.use_category.get():
            self._sync_folder()
        else:
            self.folder_box.configure(state="normal")
        self.folder_box.configure(values=self._folder_values())

    def _on_category_picked(self, _event=None):
        self._category_auto = False
        if self.use_category.get():
            self._sync_folder()

    def _sync_folder(self):
        values = self._folder_values()
        self.folder_box.configure(values=values)
        if self.use_category.get():
            category = self.category_var.get() or "Other"
            self.folder_var.set(str(self.settings.download_dir_for(category)))
            self.folder_box.configure(state="readonly")
        else:
            self.folder_box.configure(state="normal")
            if not self.folder_var.get().strip():
                self.folder_var.set(values[0] if values else "")

    def new_folder(self):
        from tkinter import simpledialog
        from pathlib import Path as _Path
        name = simpledialog.askstring("New folder", "Folder name:",
                                      parent=self)
        if not name or not name.strip():
            return
        base = self.settings.get("download_dir")
        path = _Path(base) / sanitize_filename(name.strip(), "New folder")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showwarning(APP_NAME, "Could not create the folder:\n%s" % exc,
                                   parent=self)
            return
        self.use_category.set(False)
        self._toggle_category()
        self.folder_var.set(str(path))

    def open_folder(self):
        from pathlib import Path as _Path
        folder = self.folder_var.get().strip()
        if not folder:
            self.set_message("Choose a folder first.")
            return
        path = _Path(os.path.expandvars(os.path.expanduser(folder)))
        try:
            path.mkdir(parents=True, exist_ok=True)
            open_path(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning(APP_NAME, "Could not open the folder:\n%s" % exc,
                                   parent=self)

    def set_message(self, text):
        self.size_var.set(text)

    # ----- submit -------------------------------------------------------
    def submit(self, start_now):
        url = self.url_var.get().strip()
        if not re.match(r"^https?://", url, re.I):
            messagebox.showwarning(APP_NAME, "Enter an http:// or https:// link.",
                                   parent=self)
            self.url_entry.focus_set()
            return
        folder = self.folder_var.get().strip()
        if not folder:
            folder = str(self.settings.download_dir_for(self.category_var.get() or "Other"))
        folder = os.path.expandvars(os.path.expanduser(folder))
        name = self.name_var.get().strip() or None
        if ytdl.can_handle(url):
            name = None
        try:
            recent = [p for p in (self.settings.get("recent_dirs") or [])
                      if isinstance(p, str)]
            recent = [folder] + [p for p in recent if p.lower() != folder.lower()]
            self.settings.set("recent_dirs", recent[:8])
        except Exception:
            pass
        self.close()
        self.app.add_urls([url], save_dir=folder, file_name=name, start_now=start_now)
        if not start_now:
            self.app.set_message("Added to the queue - press Start to download it.")

    def close(self):
        self.app._add_open = False
        if self._probe_timer:
            try:
                self.after_cancel(self._probe_timer)
            except Exception:
                pass
            self._probe_timer = None
        self._probe_token += 1
        super().close()


class BatchDialog(_Dialog):
    """Add many links at once, with IDM-style [1-50] range expansion."""

    def __init__(self, app):
        if getattr(app, "_batch_open", False):
            app.bring_to_front()
            return
        app._batch_open = True
        super().__init__(app, "Batch download", 680, 470)
        self.heading("Batch download")
        self.hint("One link per line. Ranges make numbered files quick to add:\n"
                  "https://example.com/file[001-050].zip  ->  50 separate downloads.")

        text_frame = tk.Frame(self.body, bg=PANEL, highlightbackground=BORDER,
                              highlightthickness=1)
        text_frame.pack(fill="both", expand=True)
        self.text = tk.Text(text_frame, height=9, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                            relief="flat", font=("Consolas", 9), wrap="none",
                            selectbackground=ACCENT)
        self.text.pack(fill="both", expand=True, padx=6, pady=6)

        options = tk.Frame(self.body, bg=BG)
        options.pack(fill="x", pady=(8, 0))
        self.expand = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="Expand [start-end] ranges",
                        variable=self.expand,
                        command=self.refresh_count).pack(side="left")
        self.count = tk.Label(options, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8))
        self.count.pack(side="right")

        self.save_dir = self.field("Save to", str(app.settings.get("download_dir")))
        self.referer = self.field("Referer", "")

        self.start_now = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.body, text="Start downloading immediately",
                        variable=self.start_now).pack(anchor="w", pady=(6, 0))

        bar = self.buttons("Add all", self.submit)
        ttk.Button(bar, text="Browse...", style="Tool.TButton",
                   command=self.browse).pack(side="left")

        self.text.bind("<KeyRelease>", lambda _e: self.refresh_count())
        self.bind("<Control-Return>", lambda _e: self.submit())
        self.text.focus_set()
        self.refresh_count()

    def collect(self):
        raw = self.text.get("1.0", "end-1c")
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if self.expand.get():
            return expand_ranges("\n".join(lines))
        return lines

    def refresh_count(self):
        try:
            links = [u for u in self.collect() if re.match(r"^https?://", u, re.I)]
        except Exception:
            links = []
        self.count.configure(text="%d link%s" % (len(links), "" if len(links) == 1 else "s"))

    def browse(self):
        from tkinter import filedialog
        chosen = filedialog.askdirectory(parent=self, title="Choose download folder")
        if chosen:
            self.save_dir.delete(0, "end")
            self.save_dir.insert(0, chosen)

    def submit(self):
        urls = [u for u in self.collect() if re.match(r"^https?://", u, re.I)]
        if not urls:
            messagebox.showwarning(APP_NAME, "Enter at least one http:// or https:// link.",
                                   parent=self)
            return
        if len(urls) > 500:
            urls = urls[:500]
        save_dir = self.save_dir.get().strip() or None
        referer = self.referer.get().strip()
        self.close()
        self.app.add_urls(urls, save_dir=save_dir, referer=referer,
                          start_now=bool(self.start_now.get()))

    def close(self):
        self.app._batch_open = False
        super().close()


class GrabberDialog(_Dialog):
    def __init__(self, app):
        if getattr(app, "_grabber_open", False):
            app.bring_to_front()
            return
        app._grabber_open = True
        super().__init__(app, "Media Grabber", 760, 520)
        self.heading("Media Grabber")
        self.hint("Enter a page address. Every link on the page is listed; filter to "
                  "show only files.")

        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=(0, 8))
        self.url_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.url_var)
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Scan", style="Accent.TButton",
                   command=self.scan).pack(side="left", padx=(6, 0))

        options = tk.Frame(self.body, bg=BG)
        options.pack(fill="x")
        self.include_all = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="Include every link (not only known file types)",
                        variable=self.include_all).pack(side="left")
        ttk.Button(options, text="Download selected", style="Tool.TButton",
                   command=self.download_selected).pack(side="right")
        ttk.Button(options, text="Download all", style="Tool.TButton",
                   command=self.download_all).pack(side="right", padx=6)

        card = tk.Frame(self.body, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True, pady=(8, 0))
        columns = ("name", "type", "size", "url")
        self.tree = ttk.Treeview(card, columns=columns, show="headings", selectmode="extended")
        for column, (label, width) in {
            "name": ("File", 240), "type": ("Type", 130),
            "size": ("Size", 90), "url": ("URL", 340),
        }.items():
            self.tree.heading(column, text=label, anchor="w")
            self.tree.column(column, width=width, minwidth=60,
                             anchor="e" if column == "size" else "w",
                             stretch=column in ("name", "url"))
        yscroll = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        yscroll.pack(side="right", fill="y", padx=(0, 1), pady=1)

        self.status = tk.Label(self.body, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8))
        self.status.pack(anchor="w", pady=(6, 0))

    def scan(self):
        url = self.url_var.get().strip()
        if not re.match(r"^https?://", url, re.I):
            messagebox.showwarning(APP_NAME, "Enter a valid page address.", parent=self)
            return
        self.status.config(text="Scanning...")
        self.tree.delete(*self.tree.get_children())
        include_all = bool(self.include_all.get())

        def work():
            try:
                links = grabber.grab(url, include_all=include_all, max_links=400)
            except Exception as exc:
                self.after(0, lambda: self.status.config(text="Scan failed: %s" % exc))
                return
            self.after(0, lambda: self.show_links(links))

        threading.Thread(target=work, daemon=True).start()

    def show_links(self, links):
        self.tree.delete(*self.tree.get_children())
        for index, link in enumerate(links):
            name = link.label or link.url.rsplit("/", 1)[-1]
            kind = link.content_type or link.kind or "file"
            self.tree.insert("", "end", iid="g%d" % index,
                             values=(name, kind, human_size(link.size) if link.size else "-",
                                     link.url))
        self.status.config(text="%d link(s) found." % len(links))

    def _urls(self, items):
        return [self.tree.item(iid, "values")[3] for iid in items]

    def download_selected(self):
        urls = self._urls(self.tree.selection())
        if not urls:
            messagebox.showinfo(APP_NAME, "Select one or more rows first.", parent=self)
            return
        self.app.add_urls(urls, referer=self.url_var.get().strip(), start_now=True)
        self.status.config(text="Sent %d link(s)." % len(urls))

    def download_all(self):
        urls = self._urls(self.tree.get_children())
        if not urls:
            messagebox.showinfo(APP_NAME, "Scan a page first.", parent=self)
            return
        self.app.add_urls(urls, referer=self.url_var.get().strip(), start_now=True)
        self.status.config(text="Sent %d link(s)." % len(urls))

    def close(self):
        self.app._grabber_open = False
        super().close()


class SettingsDialog(_Dialog):
    def __init__(self, app):
        if getattr(app, "_settings_open", False):
            app.bring_to_front()
            return
        app._settings_open = True
        super().__init__(app, "Settings", 660, 560)
        self.heading("Settings")
        self.settings = app.settings

        self.dir_entry = self.field("Download folder", str(self.settings.get("download_dir")))
        self.connections = self.field("Connections / file", str(self.settings.get("connections")), 8)
        self.parallel = self.field("Parallel downloads", str(self.settings.get("max_concurrent")), 8)
        self.retries = self.field("Retries", str(self.settings.get("max_retries")), 8)
        self.limit = self.field("Speed limit (KB/s)", str(self.settings.get("speed_limit_kbps")), 8)
        self.port = self.field("Browser API port", str(self.settings.get("api_port")), 8)

        self.category_folders = tk.BooleanVar(value=bool(self.settings.get("use_category_folders")))
        ttk.Checkbutton(self.body, text="Sort downloads into category folders",
                        variable=self.category_folders).pack(anchor="w", pady=(6, 0))
        self.api_enabled = tk.BooleanVar(value=bool(self.settings.get("api_enabled")))
        ttk.Checkbutton(self.body, text="Enable local browser integration API",
                        variable=self.api_enabled).pack(anchor="w")
        self.clipboard = tk.BooleanVar(value=bool(self.settings.get("clipboard_monitor")))
        ttk.Checkbutton(self.body, text="Monitor the clipboard for links",
                        variable=self.clipboard).pack(anchor="w")
        self.notify = tk.BooleanVar(value=bool(self.settings.get("notify_on_complete", True)))
        ttk.Checkbutton(self.body, text="Show the 'Download Completed' window when a file finishes",
                        variable=self.notify).pack(anchor="w")
        self.confirm = tk.BooleanVar(value=bool(self.settings.get("confirm_exit")))
        ttk.Checkbutton(self.body, text="Confirm before exiting with active downloads",
                        variable=self.confirm).pack(anchor="w")
        self.tray = tk.BooleanVar(value=bool(self.settings.get("close_to_tray", True)))
        ttk.Checkbutton(self.body, text="Keep running in the tray when the window is closed",
                        variable=self.tray).pack(anchor="w")
        self.autostart = tk.BooleanVar(value=bool(self.settings.get("launch_on_start")))
        ttk.Checkbutton(self.body, text="Start with Windows in the background (browser extension always works)",
                        variable=self.autostart).pack(anchor="w")

        bar = self.buttons("Save", self.save)
        ttk.Button(bar, text="Browse...", style="Tool.TButton",
                   command=self.browse).pack(side="left")

    def browse(self):
        from tkinter import filedialog
        chosen = filedialog.askdirectory(parent=self, title="Choose download folder")
        if chosen:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, chosen)

    def _int(self, entry, fallback):
        try:
            return max(0, int(entry.get().strip()))
        except ValueError:
            return fallback

    def save(self):
        values = {
            "download_dir": self.dir_entry.get().strip() or str(self.settings.get("download_dir")),
            "connections": max(1, min(32, self._int(self.connections, 8))),
            "max_concurrent": max(1, min(16, self._int(self.parallel, 3))),
            "max_retries": max(0, min(20, self._int(self.retries, 5))),
            "speed_limit_kbps": self._int(self.limit, 0),
            "api_port": max(1, min(65535, self._int(self.port, 53991))),
            "use_category_folders": bool(self.category_folders.get()),
            "api_enabled": bool(self.api_enabled.get()),
            "clipboard_monitor": bool(self.clipboard.get()),
            "notify_on_complete": bool(self.notify.get()),
            "confirm_exit": bool(self.confirm.get()),
            "close_to_tray": bool(self.tray.get()),
            "launch_on_start": bool(self.autostart.get()),
        }
        self.settings.update(values)
        self.settings.save()
        try:
            set_autostart(bool(values["launch_on_start"]), APP_NAME)
        except Exception:
            pass
        self.app.manager.apply_settings()
        self.app._update_summary()
        self.app.start_api()
        self.app.set_message("Settings saved.")
        self.close()

    def close(self):
        self.app._settings_open = False
        super().close()


class AboutDialog(_Dialog):
    def __init__(self, app):
        super().__init__(app, "About", 460, 300)
        self.heading(APP_NAME)
        tk.Label(self.body, text="Version %s" % VERSION, bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(self.body, justify="left", bg=BG, fg=MUTED, wraplength=400,
                 font=("Segoe UI", 9),
                 text=("\nA segmented HTTP/HTTPS download manager for Windows.\n\n"
                       "- Parallel byte-range downloads with resume\n"
                       "- Queue, retries, speed limit and category folders\n"
                       "- Media Grabber for any page link or media source\n"
                       "- Bundled Chrome/Edge extension and shdm:// handler\n\n"
                       "Independent implementation. Not affiliated with any other\n"
                       "download manager product.")).pack(anchor="w", pady=(8, 0))
        self.buttons("Close", self.close, secondary_text="Close")










