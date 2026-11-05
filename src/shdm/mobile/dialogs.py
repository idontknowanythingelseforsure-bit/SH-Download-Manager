"""Modal dialogs for the mobile UI."""

from __future__ import annotations

import re
import threading
from pathlib import Path

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from shdm import grabber, ytdl
from shdm.engine import clean_error
from shdm.settings import category_for
from shdm.util import human_size

from . import device
from .theme import (
    ACCENT, ACCENT_HI, AMBER, BAR, BORDER, Card, CheckBadge, DIM, Divider,
    FlatButton, GREEN, MUTED, PANEL, PANEL_HI, RED, TEXT, ToggleChip,
    make_label, shade,
)

URL_OK = re.compile(r"^https?://", re.I)


def dark_input(hint="", text="", multiline=False, height=46, **kwargs):
    options = dict(
        text=text,
        hint_text=hint,
        multiline=multiline,
        size_hint_y=None,
        height=dp(height),
        background_normal="",
        background_active="",
        background_color=(0.102, 0.102, 0.110, 1),
        foreground_color=TEXT,
        cursor_color=TEXT,
        cursor_width=dp(1.4),
        hint_text_color=DIM,
        selection_color=(0.0, 0.478, 0.8, 0.35),
        padding=(dp(10), dp(12)),
        font_size=sp(14),
    )
    options.update(kwargs)
    return TextInput(**options)


def labelled(title, widget, height=None):
    box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
    label = make_label(title, size=11, color=MUTED, height=16)
    box.add_widget(label)
    box.add_widget(widget)
    box.height = dp(16) + widget.height + dp(2)
    return box


def wrapped_label(text="", size=12, color=TEXT, width=None, **kwargs):
    """Left-aligned label whose height follows the wrapped text."""
    label = Label(
        text=text, font_size=sp(size), color=color, halign="left",
        valign="top", size_hint_y=None, **kwargs
    )
    label.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
    label.bind(texture_size=lambda inst, ts: setattr(inst, "height", ts[1]))
    if width is not None:
        label.width = width
    return label


class BaseDialog(ModalView):
    def __init__(self, title="", width=440, height=520, show_header=True, **kwargs):
        super().__init__(
            size_hint=(None, None),
            auto_dismiss=False,
            background_color=(0, 0, 0, 0.62),
            **kwargs,
        )
        self.width = min(dp(width), Window.width - dp(12))
        self.height = min(dp(height), Window.height - dp(20))
        self._base_size = (dp(width), dp(height))
        Window.bind(size=self._clamp)
        self.bind(on_dismiss=lambda *_: Window.unbind(size=self._clamp))
        self.panel = Card(
            orientation="vertical", padding=dp(16), spacing=dp(10), radius=14
        )
        super().add_widget(self.panel)

        if show_header:
            header = BoxLayout(size_hint_y=None, height=dp(28), spacing=dp(8))
            header.add_widget(make_label(title, size=17, color=TEXT, bold=True))
            close = FlatButton(
                text="Close", bg=PANEL_HI, font_size=11,
                size_hint=(None, None), size=(dp(62), dp(28)), radius=8,
            )
            close.bind(on_release=lambda *_: self.dismiss())
            header.add_widget(close)
            self.panel.add_widget(header)

    def _clamp(self, *_args):
        base_w, base_h = self._base_size
        self.width = min(base_w, Window.width - dp(12))
        self.height = min(base_h, Window.height - dp(20))

    def body(self) -> BoxLayout:
        """A scrollable body; callers add widgets to the returned layout."""
        scroll = ScrollView(
            do_scroll_x=False, bar_width=dp(3),
            bar_color=ACCENT, bar_inactive_color=PANEL_HI,
        )
        inner = BoxLayout(
            orientation="vertical", spacing=dp(9), size_hint_y=None,
        )
        inner.bind(minimum_height=inner.setter("height"))
        scroll.add_widget(inner)
        self.panel.add_widget(scroll)
        return inner

    def footer(self, buttons):
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        for text, callback, accent in buttons:
            button = FlatButton(
                text=text, bg=ACCENT if accent else PANEL_HI, font_size=13,
                bold=accent,
            )
            button.bind(on_release=callback)
            row.add_widget(button)
        self.panel.add_widget(row)
        return row


class AddDialog(BaseDialog):
    def __init__(self, app, preset_url="", **kwargs):
        super().__init__("Add Download", width=470, height=530, **kwargs)
        self.app = app
        self._probe_token = 0
        self._probe_event = None
        self._auto_name = ""

        body = self.body()

        self.url_input = dark_input("https://example.com/file.zip")
        paste = FlatButton(
            text="Paste", bg=PANEL_HI, font_size=12,
            size_hint=(None, None), size=(dp(64), dp(46)), radius=8,
        )
        paste.bind(on_release=self._paste)
        url_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        url_row.add_widget(self.url_input)
        url_row.add_widget(paste)
        body.add_widget(labelled("Link", url_row))

        self.hint = wrapped_label(
            "Paste a link or share one from your browser.", size=11, color=MUTED,
        )
        body.add_widget(self.hint)

        self.folder_input = dark_input(
            "Download folder", text=str(app.settings.get("download_dir") or ""),
        )
        self.folder_input.cursor = (0, 0)
        browse = FlatButton(
            text="...", bg=PANEL_HI, font_size=14,
            size_hint=(None, None), size=(dp(46), dp(46)), radius=8,
        )
        browse.bind(on_release=self._browse)
        folder_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        folder_row.add_widget(self.folder_input)
        folder_row.add_widget(browse)
        body.add_widget(labelled("Folder", folder_row))

        self.category = ToggleChip(
            active=bool(app.settings.get("use_category_folders", True)),
            size_hint=(None, None), size=(dp(60), dp(30)),
        )
        category_row = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(8))
        category_row.add_widget(
            make_label("Use category folders", size=13, color=TEXT)
        )
        category_row.add_widget(self.category)
        body.add_widget(category_row)

        self.name_input = dark_input("File name (optional)")
        body.add_widget(labelled("Save as", self.name_input))

        self.audio = ToggleChip(
            active=False, on_text="AUDIO", off_text="VIDEO",
            size_hint=(None, None), size=(dp(76), dp(30)),
        )
        media_row = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(8))
        media_row.add_widget(
            make_label("Media links (YouTube and similar)", size=13, color=TEXT)
        )
        media_row.add_widget(self.audio)
        body.add_widget(media_row)

        self.error = wrapped_label("", size=11, color=RED)
        body.add_widget(self.error)

        self.footer(
            [
                ("Cancel", lambda *_: self.dismiss(), False),
                ("Add paused", lambda *_: self._submit(False), False),
                ("Download", lambda *_: self._submit(True), True),
            ]
        )

        self.url_input.bind(text=self._url_changed)
        self.name_input.bind(focus=self._name_focus)
        if preset_url:
            self.url_input.text = preset_url
            Clock.schedule_once(lambda _dt: self._start_probe(), 0.2)

    # -- helpers ----------------------------------------------------------
    def _name_focus(self, _instance, focused):
        if focused:
            self._auto_name = ""

    def _paste(self, *_args):
        try:
            from kivy.core.clipboard import Clipboard

            text = Clipboard.paste() or ""
        except Exception:
            text = ""
        if text:
            self.url_input.text = device.first_url(text) or text.strip()

    def _browse(self, *_args):
        picker = FolderBrowser(
            start=self.folder_input.text or self.app.settings.get("download_dir"),
            on_pick=self._folder_picked,
        )
        picker.open()

    def _folder_picked(self, path):
        self.folder_input.text = str(path)

    def _url_changed(self, _instance, text):
        text = (text or "").strip()
        if self._probe_event is not None:
            self._probe_event.cancel()
            self._probe_event = None
        self._probe_token += 1
        self.error.text = ""
        if not URL_OK.match(text):
            self.hint.text = "Paste a link or share one from your browser."
            self.hint.color = MUTED
            return
        self.hint.text = "Reading link\u2026"
        self.hint.color = MUTED
        self._probe_event = Clock.schedule_once(lambda _dt: self._start_probe(), 0.7)

    def _start_probe(self):
        url = (self.url_input.text or "").strip()
        if not URL_OK.match(url):
            return
        token = self._probe_token
        media = ytdl.can_handle(url)

        def work():
            info = None
            error = ""
            if not media:
                try:
                    info = grabber.probe_link(url)
                except Exception as exc:  # noqa: BLE001 - network errors are normal
                    error = str(exc)
            Clock.schedule_once(
                lambda _dt: self._apply_probe(token, info, error, media), 0
            )

        threading.Thread(target=work, name="shdm-probe", daemon=True).start()

    def _apply_probe(self, token, info, error, media):
        if token != self._probe_token or not self.url_input.text.strip():
            return
        if media:
            self.hint.text = (
                "Media page detected \u2014 pick VIDEO or AUDIO above."
            )
            self.hint.color = ACCENT_HI
            return
        if info is None:
            self.hint.text = error or "Could not read the link, but it can still download."
            self.hint.color = AMBER
            return
        size = human_size(info.size) if info.size else "Unknown size"
        name = info.filename or info.label
        self.hint.text = f"{size} \u00b7 {name}"
        self.hint.color = MUTED
        if name and self.name_input.text.strip() in ("", self._auto_name):
            self._auto_name = name
            self.name_input.text = name

    def _submit(self, start_now):
        url = (self.url_input.text or "").strip()
        if not URL_OK.match(url):
            self.error.text = "Enter a link that starts with http:// or https://"
            return
        base = (self.folder_input.text or "").strip()
        if not base:
            base = str(self.app.settings.get("download_dir") or "")
        name = (self.name_input.text or "").strip()
        use_category = self.category.active
        settings = self.app.settings
        try:
            settings.set("use_category_folders", use_category)
            settings.save()
            if ytdl.can_handle(url):
                audio = self.audio.active
                if use_category:
                    save_dir = str(Path(base) / ("Music" if audio else "Video"))
                else:
                    save_dir = base
                if audio:
                    selector = ytdl.audio_selector()
                else:
                    selector = ytdl.video_selector()
                task, added = self.app.engine.add_ytdl(
                    url, selector, kind="audio" if audio else "video",
                    save_dir=save_dir, start_now=start_now,
                )
            else:
                category = category_for(name) if name else category_for(url)
                save_dir = str(Path(base) / category) if use_category else base
                task, added = self.app.engine.add(
                    url, save_dir=save_dir, file_name=name or None,
                    start_now=start_now,
                )
        except Exception as exc:  # noqa: BLE001 - surface validation errors
            self.error.text = clean_error(exc)
            return
        self.dismiss()
        if added:
            self.app.root_widget.toast(f"Added: {task.file_name}")
        else:
            self.app.root_widget.toast("That link is already in the list")


class FolderBrowser(BaseDialog):
    def __init__(self, start, on_pick, **kwargs):
        super().__init__("Choose folder", width=460, height=520, **kwargs)
        self.on_pick = on_pick
        self.path = Path(start or Path.home())

        self.path_label = make_label(str(self.path), size=11, color=MUTED, height=34)
        self.panel.add_widget(self.path_label)

        scroll = ScrollView(
            do_scroll_x=False, bar_width=dp(3),
            bar_color=ACCENT, bar_inactive_color=PANEL_HI,
        )
        self.entries = BoxLayout(
            orientation="vertical", spacing=dp(2), size_hint_y=None,
        )
        self.entries.bind(minimum_height=self.entries.setter("height"))
        scroll.add_widget(self.entries)
        self.panel.add_widget(scroll)

        up = FlatButton(
            text="Up one level", bg=PANEL_HI, font_size=12,
            size_hint=(None, None), size=(dp(110), dp(40)),
        )
        up.bind(on_release=lambda *_: self._go(self.path.parent))
        use = FlatButton(text="Use this folder", bg=ACCENT, font_size=13)
        use.bind(
            on_release=lambda *_: (
                self.on_pick(self.path),
                self.dismiss(),
            )
        )
        cancel = FlatButton(
            text="Cancel", bg=PANEL_HI, font_size=12,
            size_hint=(None, None), size=(dp(80), dp(40)),
        )
        cancel.bind(on_release=lambda *_: self.dismiss())
        row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        row.add_widget(up)
        row.add_widget(use)
        row.add_widget(cancel)
        self.panel.add_widget(row)

        self._load()

    def _go(self, path: Path):
        try:
            if path.is_dir():
                self.path = path
                self._load()
        except Exception:
            pass

    def _load(self):
        self.path_label.text = str(self.path)
        self.entries.clear_widgets()
        try:
            children = sorted(
                (child for child in self.path.iterdir() if child.is_dir()),
                key=lambda child: child.name.lower(),
            )
        except Exception as exc:  # noqa: BLE001 - unreadable folders are common
            self.entries.add_widget(
                make_label(f"Cannot open this folder\n{exc}", size=12, color=RED,
                           height=50, halign="center")
            )
            return
        for child in children[:200]:
            button = FlatButton(
                text="   " + child.name + "/", bg=PANEL, font_size=13,
                halign="left", size_hint_y=None, height=dp(38), radius=6,
            )
            button.bind(on_release=lambda _btn, p=child: self._go(p))
            self.entries.add_widget(button)
        if not children:
            self.entries.add_widget(
                make_label("No sub-folders", size=12, color=DIM, height=30)
            )


class CompletionPopup(BaseDialog):
    """Small \"Download Completed\" card with Open / Folder actions."""

    def __init__(self, app, info, on_close=None, **kwargs):
        super().__init__("", width=400, height=272, show_header=False, **kwargs)
        self.app = app
        self.info = info
        self.on_close = on_close
        self.panel.spacing = dp(6)
        self.panel.padding = dp(18)

        badge = CheckBadge(48)
        badge.pos_hint = {"center_x": 0.5}
        self.panel.add_widget(badge)
        self.panel.add_widget(
            make_label("Download Completed", size=17, color=TEXT, bold=True,
                       height=26, halign="center")
        )
        name = make_label(
            info.get("file_name") or "file", size=13, color=TEXT,
            height=42, halign="center",
        )
        name.bind(width=lambda inst, width: setattr(inst, "text_size", (width, None)))
        self.panel.add_widget(name)
        self.panel.add_widget(
            make_label(info.get("size_text") or "", size=11, color=MUTED,
                       height=18, halign="center")
        )

        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        open_btn = FlatButton(text="Open", bg=ACCENT, font_size=13, bold=True)
        open_btn.bind(on_release=lambda *_: self._open_file())
        folder_btn = FlatButton(text="Open Folder", bg=PANEL_HI, font_size=12)
        folder_btn.bind(on_release=lambda *_: self._open_folder())
        close_btn = FlatButton(text="Close", bg=PANEL_HI, font_size=12)
        close_btn.bind(on_release=lambda *_: self.dismiss())
        row.add_widget(open_btn)
        row.add_widget(folder_btn)
        row.add_widget(close_btn)
        self.panel.add_widget(row)

    def _open_file(self):
        self.app.open_path(self.info.get("target_path") or "")

    def _open_folder(self):
        target = self.info.get("target_path") or ""
        self.app.open_path(str(Path(target).parent) if target else "")

    def dismiss(self, *args, **kwargs):
        result = super().dismiss(*args, **kwargs)
        if self.on_close is not None:
            callback, self.on_close = self.on_close, None
            Clock.schedule_once(lambda _dt: callback(), 0.15)
        return result


class DetailsPopup(BaseDialog):
    def __init__(self, app, snap, **kwargs):
        super().__init__("Details", width=440, height=470, **kwargs)
        self.app = app
        self.snap = snap
        body = self.body()
        body.add_widget(
            make_label(snap.get("file_name") or "Download", size=15, color=TEXT,
                       bold=True, height=26)
        )
        body.add_widget(
            make_label(f"Status: {snap.get('status', '').title()}", size=12,
                       color=MUTED, height=20)
        )
        body.add_widget(
            make_label(f"Size: {snap.get('size_text') or 'unknown'}", size=12,
                       color=MUTED, height=20)
        )
        # The error comes first: it is the reason someone opens this popup.
        error = (snap.get("error") or "").strip()
        if error:
            body.add_widget(make_label("Error", size=11, color=MUTED, height=18))
            body.add_widget(wrapped_label(error, size=11, color=RED))
            copy = FlatButton(
                text="Copy error", bg=PANEL_HI, font_size=11,
                size_hint=(None, None), size=(dp(100), dp(30)), radius=8,
            )
            copy.bind(on_release=lambda *_: self._copy(error))
            body.add_widget(copy)
        body.add_widget(
            make_label("Saved to", size=11, color=MUTED, height=18)
        )
        body.add_widget(
            make_label(snap.get("target_path") or "", size=12, color=TEXT,
                       height=44)
        )
        body.add_widget(make_label("Source", size=11, color=MUTED, height=18))
        body.add_widget(
            make_label(snap.get("url") or "", size=11, color=DIM, height=54)
        )
        notes = snap.get("notes") or []
        if notes:
            body.add_widget(make_label("Recent events", size=11, color=MUTED,
                                       height=18))
            body.add_widget(
                make_label("\n".join(notes[-4:]), size=10, color=MUTED, height=64)
            )
        self.footer(
            [
                ("Open", lambda *_: self._open(), True),
                ("Folder", lambda *_: self.app.open_path(
                    snap.get("save_dir") or ""), False),
                ("Close", lambda *_: self.dismiss(), False),
            ]
        )

    def _open(self):
        self.app.open_path(self.snap.get("target_path") or "")

    def _copy(self, text):
        try:
            from kivy.core.clipboard import Clipboard

            Clipboard.copy(text)
        except Exception:
            pass
        self.app.root_widget.toast("Error copied")


class AboutPopup(BaseDialog):
    def __init__(self, app, version, **kwargs):
        super().__init__("About", width=420, height=330, **kwargs)
        body = self.body()
        body.add_widget(
            make_label("SH Download Manager", size=17, color=TEXT, bold=True,
                       height=26)
        )
        body.add_widget(
            make_label(f"Version {version}", size=12, color=MUTED, height=20)
        )
        body.add_widget(Divider())
        body.add_widget(
            make_label(
                "Segmented HTTP downloader with a local API, browser "
                "extension and yt-dlp media support.\n\n"
                "The Windows build adds a tray icon, an installer and a "
                "desktop completion card. This Android build reuses the same "
                "engine with a touch interface.",
                size=12, color=TEXT, height=120,
            )
        )
        body.add_widget(
            make_label("github.com/idontknowanythingelseforsure-bit",
                       size=11, color=DIM, height=22)
        )
        self.footer([("Close", lambda *_: self.dismiss(), True)])
