"""The two screens of the mobile UI."""

from __future__ import annotations

from pathlib import Path

from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.textinput import TextInput

from shdm.settings import VERSION
from shdm.util import human_speed

from .dialogs import AboutPopup, AddDialog, FolderBrowser, dark_input, labelled
from .theme import (
    ACCENT, BAR, BORDER, Card, DIM, Divider, FlatButton, MUTED, PANEL,
    PANEL_HI, TEXT, ToggleChip, make_label,
)
from .widgets import ACTIVE, TaskList


class DownloadsScreen(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(orientation="vertical", **kwargs)
        self.app = app

        header = Card(
            bg=BAR, radius=0, orientation="horizontal",
            size_hint_y=None, height=dp(62),
            padding=(dp(14), dp(9)), spacing=dp(8),
        )
        titles = BoxLayout(orientation="vertical", spacing=dp(1))
        titles.add_widget(
            make_label("SH Download Manager", size=15, color=TEXT, bold=True)
        )
        self.stats = make_label("No downloads", size=11, color=MUTED, height=16)
        titles.add_widget(self.stats)
        header.add_widget(titles)

        new_button = FlatButton(
            text="New", bg=ACCENT, font_size=13, bold=True,
            size_hint=(None, None), size=(dp(62), dp(38)),
        )
        new_button.bind(on_release=lambda *_: self.open_add())
        header.add_widget(new_button)

        settings_button = FlatButton(
            text="Settings", bg=PANEL_HI, font_size=11,
            size_hint=(None, None), size=(dp(74), dp(38)),
        )
        settings_button.bind(on_release=lambda *_: self.app.root_widget.show_settings())
        header.add_widget(settings_button)
        self.add_widget(header)

        scroll = ScrollView(
            do_scroll_x=False, bar_width=dp(3),
            bar_color=ACCENT, bar_inactive_color=PANEL_HI,
        )
        self.list = TaskList(app, padding=(dp(12), dp(10)))
        scroll.add_widget(self.list)
        wrap = BoxLayout(padding=(dp(6), dp(2)))
        wrap.add_widget(scroll)
        self.add_widget(wrap)

        bottom = Card(
            bg=BAR, radius=0, orientation="horizontal",
            size_hint_y=None, height=dp(60),
            padding=(dp(10), dp(9)), spacing=dp(8),
        )
        self._action(bottom, "Pause all", self._pause_all, PANEL_HI)
        self._action(bottom, "Resume all", self._resume_all, PANEL_HI)
        self._action(bottom, "Clear done", self._clear_done, PANEL_HI)
        self._action(bottom, "Folder", self._open_folder, PANEL_HI)
        self.add_widget(bottom)

    def _action(self, parent, text, callback, bg):
        button = FlatButton(text=text, bg=bg, font_size=11)
        button.bind(on_release=callback)
        parent.add_widget(button)

    def open_add(self, url=""):
        AddDialog(self.app, preset_url=url).open()

    def _pause_all(self, *_args):
        self.app.engine.pause_all()
        self.app.root_widget.toast("Pausing all downloads")

    def _resume_all(self, *_args):
        self.app.engine.resume_all()
        self.app.root_widget.toast("Resuming downloads")

    def _clear_done(self, *_args):
        self.app.engine.clear_completed(delete_files=False)
        self.app.root_widget.toast("Finished downloads removed from the list")

    def _open_folder(self, *_args):
        self.app.open_path(str(self.app.settings.get("download_dir") or ""))

    def sync(self, snaps):
        self.list.sync(snaps)
        active = sum(1 for snap in snaps if snap.get("status") in ACTIVE)
        speed = sum(
            float(snap.get("speed") or 0)
            for snap in snaps
            if snap.get("status") == "downloading"
        )
        if not snaps:
            text = "No downloads"
        else:
            text = f"{len(snaps)} download{'s' if len(snaps) != 1 else ''}"
            if active:
                text += f" \u00b7 {active} active"
            if speed:
                text += f" \u00b7 {human_speed(speed)}"
        self.stats.text = text


class SettingsScreen(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(orientation="vertical", **kwargs)
        self.app = app

        header = Card(
            bg=BAR, radius=0, orientation="horizontal",
            size_hint_y=None, height=dp(62),
            padding=(dp(14), dp(9)), spacing=dp(8),
        )
        back = FlatButton(
            text="Back", bg=PANEL_HI, font_size=12,
            size_hint=(None, None), size=(dp(62), dp(38)),
        )
        back.bind(on_release=lambda *_: self.app.root_widget.show_downloads())
        header.add_widget(back)
        header.add_widget(make_label("Settings", size=15, color=TEXT, bold=True))
        header.add_widget(
            make_label(f"v{VERSION}", size=11, color=DIM, halign="right")
        )
        self.add_widget(header)

        scroll = ScrollView(
            do_scroll_x=False, bar_width=dp(3),
            bar_color=ACCENT, bar_inactive_color=PANEL_HI,
        )
        body = BoxLayout(
            orientation="vertical", spacing=dp(2), size_hint_y=None,
            padding=(dp(14), dp(10)),
        )
        body.bind(minimum_height=body.setter("height"))
        scroll.add_widget(body)
        self.body = body

        # Folder ---------------------------------------------------------
        self.folder_label = make_label("", size=11, color=MUTED, height=34)
        folder_row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(8))
        folder_row.add_widget(make_label("Download folder", size=13, color=TEXT))
        change = FlatButton(
            text="Change", bg=PANEL_HI, font_size=11,
            size_hint=(None, None), size=(dp(74), dp(32)),
        )
        change.bind(on_release=self._choose_folder)
        folder_row.add_widget(change)
        body.add_widget(folder_row)
        body.add_widget(self.folder_label)
        body.add_widget(Divider())

        # Category folders ----------------------------------------------
        self.category = ToggleChip(
            active=bool(app.settings.get("use_category_folders", True)),
            size_hint=(None, None), size=(dp(60), dp(30)),
        )
        self.category.bind(on_release=lambda *_: self._save(
            "use_category_folders", self.category.active))
        body.add_widget(self._row("Use category folders", self.category,
                                  "Save into Music, Video, Documents folders."))

        # Concurrency ----------------------------------------------------
        self.concurrent_value = make_label("3", size=12, color=MUTED, width=dp(28))
        self.concurrent = Slider(
            min=1, max=6, step=1,
            value=float(app.settings.get("max_concurrent", 3)),
            size_hint=(None, None), size=(dp(140), dp(36)),
        )
        self.concurrent.bind(on_value=lambda _s, value: self.concurrent_value.setter(
            "text")(str(int(value))))
        self.concurrent.bind(on_touch_up=lambda *_: self._save(
            "max_concurrent", int(self.concurrent.value)))
        concurrency_row = BoxLayout(size_hint_y=None, height=dp(36))
        concurrency_row.add_widget(self.concurrent)
        concurrency_row.add_widget(self.concurrent_value)
        body.add_widget(self._row("Downloads at once", concurrency_row))

        # Connections ----------------------------------------------------
        self.connections_value = make_label("8", size=12, color=MUTED, width=dp(28))
        self.connections = Slider(
            min=1, max=16, step=1,
            value=float(app.settings.get("connections", 8)),
            size_hint=(None, None), size=(dp(140), dp(36)),
        )
        self.connections.bind(on_value=lambda _s, value: self.connections_value.setter(
            "text")(str(int(value))))
        self.connections.bind(on_touch_up=lambda *_: self._save(
            "connections", int(self.connections.value)))
        connections_row = BoxLayout(size_hint_y=None, height=dp(36))
        connections_row.add_widget(self.connections)
        connections_row.add_widget(self.connections_value)
        body.add_widget(self._row("Connections per file", connections_row,
                                  "More connections can be faster on some servers."))

        # Speed limit ----------------------------------------------------
        self.speed = dark_input(
            "0", text=str(app.settings.get("speed_limit_kbps", 0)), height=38,
            input_filter="int",
            background_color=(0.196, 0.196, 0.200, 1),
        )
        self.speed.size_hint_x = None
        self.speed.width = dp(88)
        self.speed.bind(on_text_validate=lambda *_: self._save(
            "speed_limit_kbps", int(self.speed.text or 0)))
        self.speed.bind(focus=lambda _w, focused: self._save(
            "speed_limit_kbps", int(self.speed.text or 0)) if not focused else None)
        body.add_widget(self._row("Speed limit (KB/s)", self.speed,
                                  "0 means unlimited."))

        # Notifications --------------------------------------------------
        self.notify = ToggleChip(
            active=bool(app.settings.get("notify_on_complete", True)),
            size_hint=(None, None), size=(dp(60), dp(30)),
        )
        self.notify.bind(on_release=lambda *_: self._save(
            "notify_on_complete", self.notify.active))
        body.add_widget(self._row("Completion popup", self.notify,
                                  "Show a small card when a download finishes."))
        body.add_widget(Divider())

        # Tools ----------------------------------------------------------
        open_button = FlatButton(text="Open download folder", bg=PANEL_HI,
                                 font_size=13, size_hint_y=None, height=dp(42))
        open_button.bind(on_release=lambda *_: self.app.open_path(
            str(self.app.settings.get("download_dir") or "")))
        body.add_widget(open_button)

        about_button = FlatButton(text="About SH Download Manager", bg=PANEL_HI,
                                  font_size=13, size_hint_y=None, height=dp(42))
        about_button.bind(on_release=lambda *_: AboutPopup(self.app, VERSION).open())
        body.add_widget(about_button)

        body.add_widget(
            make_label(
                "Shared engine with the Windows build. Files are saved to the "
                "folder above; category folders can be turned off.",
                size=11, color=DIM, height=60,
            )
        )
        self.add_widget(scroll)
        self.refresh()

    def _row(self, title, control, subtitle=None):
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(1))
        top = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        top.add_widget(make_label(title, size=13, color=TEXT))
        top.add_widget(control)
        box.add_widget(top)
        if subtitle:
            box.add_widget(make_label(subtitle, size=10, color=DIM, height=16))
        box.height = dp(38) + (dp(16) if subtitle else 0) + dp(8)
        return box

    def _choose_folder(self, *_args):
        picker = FolderBrowser(
            start=self.app.settings.get("download_dir"),
            on_pick=self._folder_picked,
        )
        picker.open()

    def _folder_picked(self, path):
        self.app.settings.set("download_dir", str(path))
        self.app.settings.save()
        self.refresh()
        self.app.root_widget.toast("Folder updated")

    def _save(self, key, value):
        self.app.settings.set(key, value)
        self.app.settings.save()
        try:
            self.app.engine.apply_settings()
        except Exception:
            pass

    def refresh(self):
        folder = str(self.app.settings.get("download_dir") or "")
        self.folder_label.text = folder
