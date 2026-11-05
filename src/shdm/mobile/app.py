"""Kivy application: shared download engine, touch interface."""

from __future__ import annotations

import os
import traceback
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.modalview import ModalView
from kivy.uix.screenmanager import Screen, ScreenManager

from shdm.engine import DownloadManager
from shdm.settings import VERSION, Settings
from shdm.util import human_size

from . import device
from .dialogs import AddDialog, CompletionPopup, DetailsPopup
from .screens import DownloadsScreen, SettingsScreen
from .theme import BAR, TEXT
from .widgets import ACTIVE, Toast


class Root(FloatLayout):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.sm = ScreenManager()
        self.downloads = DownloadsScreen(app)
        self.settings_screen = SettingsScreen(app)
        for widget, name in (
            (self.downloads, "downloads"),
            (self.settings_screen, "settings"),
        ):
            screen = Screen(name=name)
            screen.add_widget(widget)
            self.sm.add_widget(screen)
        self.add_widget(self.sm)

        self.toast_widget = Toast()
        self.add_widget(self.toast_widget)

    def show_downloads(self):
        self.sm.current = "downloads"

    def show_settings(self):
        self.settings_screen.refresh()
        self.sm.current = "settings"

    def toast(self, text):
        try:
            self.toast_widget.show(text, (self.width, self.height), self.pos)
        except Exception:
            pass


class SHDMApp(App):
    title = "SH Download Manager"

    # -- lifecycle --------------------------------------------------------
    def build(self):
        Window.clearcolor = BAR
        Window.softinput_mode = "below_target"
        self.settings = Settings(base_dir=self._app_dir())
        device.resolve_download_dir(self.settings)
        override = os.environ.get("SHDM_DOWNLOAD_DIR")
        if override:
            Path(override).mkdir(parents=True, exist_ok=True)
            self.settings.set("download_dir", str(Path(override)))
        self.settings.save()

        self.engine = DownloadManager(self.settings, on_event=self._engine_event)
        self._seen: set[str] = set()
        self._status: dict[str, str] = {}
        self._queue: list[dict] = []
        self._completion: CompletionPopup | None = None
        self._wake = device.WakeLock()

        self.root_widget = Root(self)
        Clock.schedule_interval(self._tick, 0.6)
        Clock.schedule_once(self._after_start, 0.2)
        return self.root_widget

    def _app_dir(self) -> Path:
        override = os.environ.get("SHDM_DATA_DIR")
        if override:
            path = Path(override)
            path.mkdir(parents=True, exist_ok=True)
            return path
        return Path(self.user_data_dir)

    def _after_start(self, _dt):
        device.apply_system_bars()
        device.bind_share_intent(self._shared_url)
        device.request_permissions()
        Window.bind(on_keyboard=self._on_key)

    def on_pause(self):
        try:
            self.engine.save(force=True)
        except Exception:
            pass
        return True

    def on_resume(self):
        Clock.schedule_once(lambda _dt: self._refresh(), 0)
        return None

    def on_stop(self):
        try:
            self.engine.save(force=True)
            if not device.ANDROID:
                self.engine.shutdown()
        except Exception:
            pass

    # -- keyboard / back --------------------------------------------------
    def _on_key(self, _window, key, *_args):
        if key == 27:  # Android back, Escape on desktop
            for child in list(Window.children):
                if isinstance(child, ModalView):
                    child.dismiss()
                    return True
            if self.root_widget.sm.current != "downloads":
                self.root_widget.show_downloads()
                return True
        return False

    # -- engine events ----------------------------------------------------
    def _engine_event(self, _event, _task_id):
        Clock.schedule_once(lambda _dt: self._refresh(), 0)

    def _shared_url(self, url):
        Clock.schedule_once(
            lambda _dt: self.root_widget.downloads.open_add(url), 0
        )

    def _refresh(self, *_args):
        try:
            snaps = self.engine.snapshots()
            self.root_widget.downloads.sync(snaps)
        except Exception:
            traceback.print_exc()

    def _tick(self, _dt):
        try:
            snaps = self.engine.snapshots()
            self._check_completions(snaps)
            self.root_widget.downloads.sync(snaps)
            active = any(snap.get("status") in ACTIVE for snap in snaps)
            if active:
                self._wake.acquire()
            else:
                self._wake.release()
        except Exception:
            traceback.print_exc()

    # -- completion cards -------------------------------------------------
    def _check_completions(self, snaps):
        live = set()
        for snap in snaps:
            task_id = snap["id"]
            live.add(task_id)
            status = snap.get("status")
            if task_id not in self._seen:
                self._seen.add(task_id)
                self._status[task_id] = status
                continue
            previous = self._status.get(task_id)
            if status == "completed" and previous != "completed":
                if self.settings.get("notify_on_complete", True):
                    self._show_completion(snap)
            self._status[task_id] = status
        for task_id in list(self._seen):
            if task_id not in live:
                self._seen.discard(task_id)
                self._status.pop(task_id, None)

    def _show_completion(self, snap):
        info = {
            "file_name": snap.get("file_name") or "",
            "target_path": snap.get("target_path") or "",
            "save_dir": snap.get("save_dir") or "",
            "size_text": snap.get("size_text") or human_size(snap.get("total_size")),
        }
        if self._completion is not None:
            self._queue.append(info)
            return
        popup = CompletionPopup(self, info, on_close=self._completion_closed)
        self._completion = popup
        popup.open()

    def _completion_closed(self):
        self._completion = None
        if self._queue:
            self._show_completion(self._queue.pop(0))

    # -- actions ----------------------------------------------------------
    def show_details(self, task_id):
        task = self.engine.get(task_id)
        if task is None:
            return
        DetailsPopup(self, task.snapshot()).open()

    def open_path(self, path):
        if not path:
            return
        try:
            target = Path(path)
            if target.is_dir():
                device.open_folder(target)
            else:
                device.open_file(target)
        except FileNotFoundError:
            self.root_widget.toast("That file no longer exists")
        except Exception:
            self.root_widget.toast("Could not open that location")


def main():
    SHDMApp().run()
