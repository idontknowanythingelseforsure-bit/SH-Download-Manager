"""Reusable pieces of the download list."""

from __future__ import annotations

from kivy.animation import Animation
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label

from .theme import (
    ACCENT, AMBER, BAR, Card, DIM, FlatButton, GREEN, MUTED, PANEL, PANEL_HI,
    RED, TEXT, Bar, make_label, shade,
)

STATUS_TEXT = {
    "queued": "Queued",
    "connecting": "Connecting",
    "downloading": "Downloading",
    "pausing": "Pausing",
    "paused": "Paused",
    "completed": "Completed",
    "error": "Failed",
    "canceled": "Canceled",
}

STATUS_COLOR = {
    "queued": MUTED,
    "connecting": ACCENT,
    "downloading": ACCENT,
    "pausing": AMBER,
    "paused": AMBER,
    "completed": GREEN,
    "error": RED,
    "canceled": DIM,
}

ACTIVE = ("queued", "connecting", "downloading", "pausing")


def _detail_text(snap: dict) -> str:
    status = snap.get("status")
    size = snap.get("size_text") or ""
    if status == "downloading":
        parts = [size]
        speed = snap.get("speed_text")
        if speed and speed not in ("0 B/s", "--", ""):
            parts.append(speed)
        eta = snap.get("eta_text")
        if eta and eta not in ("--", ""):
            parts.append(f"{eta} left")
        return " \u00b7 ".join(part for part in parts if part)
    if status == "connecting":
        return size or "Contacting server\u2026"
    if status == "completed":
        return size or "Finished"
    if status == "error":
        return snap.get("error") or "Download failed"
    if status == "paused":
        return (size + " \u00b7 paused") if size else "Paused"
    if status == "queued":
        return (size + " \u00b7 queued") if size else "Waiting"
    if status == "pausing":
        return "Pausing\u2026"
    return size


class TaskRow(Card):
    """One download, with progress and contextual actions."""

    def __init__(self, app, snap, **kwargs):
        super().__init__(
            orientation="vertical",
            padding=(dp(12), dp(10)),
            spacing=dp(7),
            size_hint_y=None,
            height=dp(116),
            bg=PANEL,
            radius=12,
            **kwargs,
        )
        self.app = app
        self.task_id = snap["id"]
        self._mode = None

        top = BoxLayout(size_hint_y=None, height=dp(22), spacing=dp(6))
        self.name = make_label(
            snap.get("file_name") or snap.get("url", ""),
            size=14, color=TEXT, halign="left", shorten=True,
            shorten_from="right",
        )
        top.add_widget(self.name)

        self.state = Label(
            text="", font_size=sp(11), color=MUTED, bold=True,
            size_hint_x=None, width=dp(84), halign="right", valign="middle",
        )
        self.state.bind(size=lambda inst, size: setattr(inst, "text_size", size))
        top.add_widget(self.state)
        self.add_widget(top)

        self.bar = Bar(size_hint_y=None, height=dp(6))
        self.add_widget(self.bar)

        bottom = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(6))
        self.detail = make_label(
            "", size=11, color=MUTED, halign="left", shorten=True,
            shorten_from="right",
        )
        bottom.add_widget(self.detail)

        self.actions = BoxLayout(size_hint_x=None, width=dp(1), spacing=dp(6))
        bottom.add_widget(self.actions)
        self.add_widget(bottom)

        self.update(snap)

    # -- interactions -----------------------------------------------------
    def _task(self):
        return self.app.engine.get(self.task_id)

    def _pause(self, *_args):
        task = self._task()
        if task is not None:
            self.app.engine.pause(task)

    def _resume(self, *_args):
        task = self._task()
        if task is not None:
            self.app.engine.resume(task)

    def _remove(self, *_args):
        task = self._task()
        if task is not None:
            self.app.engine.remove(task)
            self.app.root_widget.toast("Removed from the list")

    def _open(self, *_args):
        task = self._task()
        if task is not None:
            self.app.open_path(task.target_path)

    def _folder(self, *_args):
        task = self._task()
        if task is not None:
            self.app.open_path(task.save_dir)

    # -- rendering --------------------------------------------------------
    def _set_actions(self, mode: str):
        if mode == self._mode:
            return
        self._mode = mode
        self.actions.clear_widgets()
        if mode == "done":
            self._add_action("Open", self._open, dp(54))
            self._add_action("Folder", self._folder, dp(62))
            self._add_action("Remove", self._remove, dp(68), bg=shade(PANEL_HI, 0.9))
        elif mode == "idle":
            self._add_action("Resume", self._resume, dp(70), bg=ACCENT)
            self._add_action("Remove", self._remove, dp(68))
        else:
            self._add_action("Pause", self._pause, dp(62))
            self._add_action("Remove", self._remove, dp(68))

    def _add_action(self, text, callback, width, bg=PANEL_HI):
        button = FlatButton(
            text=text, bg=bg, font_size=11,
            size_hint=(None, None), size=(width, dp(30)), radius=8,
        )
        button.bind(on_release=callback)
        self.actions.add_widget(button)
        self.actions.width = sum(
            child.width for child in self.actions.children
        ) + dp(6) * max(0, len(self.actions.children) - 1)

    def update(self, snap: dict):
        status = snap.get("status") or "queued"
        self.name.text = snap.get("file_name") or snap.get("url", "")
        self.state.text = STATUS_TEXT.get(status, status.title())
        self.state.color = STATUS_COLOR.get(status, MUTED)
        self.bar.color = STATUS_COLOR.get(status, ACCENT)
        progress = snap.get("progress")
        if status == "completed":
            progress = 1.0
        elif progress is None:
            progress = 0.08 if status in ACTIVE else 0.0
        self.bar.value = progress or 0.0
        self.detail.text = _detail_text(snap)
        self.detail.color = RED if status == "error" else MUTED
        if status == "completed":
            mode = "done"
        elif status in ("paused", "error", "canceled"):
            mode = "idle"
        else:
            mode = "active"
        self._set_actions(mode)


class TaskList(BoxLayout):
    """Vertical list that rebuilds only when the set of tasks changes."""

    def __init__(self, app, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("spacing", dp(8))
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("padding", (0, dp(2)))
        super().__init__(**kwargs)
        self.app = app
        self.rows = {}
        self._ids = None
        self.empty = make_label(
            "No downloads yet\n\nTap  New  to add a link, or share a link "
            "from a browser to this app.",
            size=13, color=MUTED, halign="center", height=180,
        )
        self.add_widget(self.empty)
        self.bind(minimum_height=self.setter("height"))

    def sync(self, snaps: list[dict]):
        snaps = sorted(snaps, key=lambda item: item.get("added_at") or 0, reverse=True)
        ids = [snap["id"] for snap in snaps]
        if ids != self._ids:
            self._ids = ids
            self.clear_widgets()
            self.rows = {}
            if not snaps:
                self.add_widget(self.empty)
            for snap in snaps:
                row = TaskRow(self.app, snap)
                self.rows[snap["id"]] = row
                self.add_widget(row)
        for snap in snaps:
            row = self.rows.get(snap["id"])
            if row is not None:
                row.update(snap)


class Toast(Label):
    """Small transient message shown over the screens."""

    def __init__(self, **kwargs):
        super().__init__(
            text="", font_size=sp(13), color=TEXT, opacity=0,
            size_hint=(None, None), size=(dp(300), dp(40)),
            halign="center", valign="middle", **kwargs
        )
        self.bind(size=lambda inst, size: setattr(inst, "text_size", size))
        from kivy.graphics import Color, RoundedRectangle

        with self.canvas.before:
            self._bg = Color(0.102, 0.102, 0.102, 0.96)
            self._rect = RoundedRectangle(radius=[dp(10)])
        self.bind(pos=self._sync, size=self._sync)
        self._anim = None

    def _sync(self, *_args):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def show(self, text: str, parent_size, parent_pos):
        self.text = text
        self.width = min(dp(320), parent_size[0] - dp(24))
        self.pos = (
            parent_pos[0] + (parent_size[0] - self.width) / 2,
            parent_pos[1] + dp(76),
        )
        if self._anim is not None:
            self._anim.cancel(self)
        self.opacity = 1
        self._anim = Animation(opacity=0, duration=0.4, t="in_quad")
        from kivy.clock import Clock

        Clock.schedule_once(lambda _dt: self._anim.start(self), 2.2)
