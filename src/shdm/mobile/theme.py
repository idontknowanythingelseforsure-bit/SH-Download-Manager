"""Dark palette and the small widgets the Kivy UI is built from."""

from __future__ import annotations

from kivy.graphics import Color, Ellipse, Line, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.properties import ColorProperty, NumericProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

# Windows build palette, reused so both front ends look like one product.
BG = (0.118, 0.118, 0.118, 1.0)
BAR = (0.106, 0.106, 0.106, 1.0)
PANEL = (0.145, 0.145, 0.149, 1.0)
PANEL_HI = (0.196, 0.196, 0.200, 1.0)
BORDER = (0.235, 0.235, 0.243, 1.0)
TEXT = (0.878, 0.878, 0.878, 1.0)
MUTED = (0.600, 0.600, 0.620, 1.0)
DIM = (0.400, 0.400, 0.420, 1.0)
ACCENT = (0.000, 0.478, 0.800, 1.0)
ACCENT_HI = (0.070, 0.560, 0.900, 1.0)
GREEN = (0.235, 0.706, 0.345, 1.0)
RED = (0.812, 0.286, 0.286, 1.0)
AMBER = (0.900, 0.650, 0.200, 1.0)
TRANSPARENT = (0, 0, 0, 0)


def shade(color, factor: float = 0.78):
    r, g, b, a = color
    return (
        max(0.0, min(1.0, r * factor)),
        max(0.0, min(1.0, g * factor)),
        max(0.0, min(1.0, b * factor)),
        a,
    )


class Card(BoxLayout):
    """BoxLayout with a rounded, themed background."""

    bg = ColorProperty(PANEL)
    radius = NumericProperty(12)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            self._bg_color = Color(*self.bg)
            self._bg_rect = RoundedRectangle(radius=[dp(self.radius)])
        self.bind(pos=self._sync, size=self._sync, bg=self._sync_color)

    def _sync(self, *_args):
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size
        self._bg_rect.radius = [dp(self.radius)]

    def _sync_color(self, *_args):
        self._bg_color.rgba = self.bg


class FlatButton(ButtonBehavior, Label):
    """Rounded, flat button with a dark look and press feedback."""

    bg = ColorProperty(PANEL_HI)
    radius = NumericProperty(10)

    def __init__(self, bg=PANEL_HI, fg=TEXT, font_size=14, bold=False, **kwargs):
        kwargs.setdefault("halign", "center")
        kwargs.setdefault("valign", "middle")
        kwargs.setdefault("color", fg)
        kwargs.setdefault("markup", False)
        super().__init__(**kwargs)
        self.font_size = sp(font_size)
        self.bold = bold
        self.bg = bg
        with self.canvas.before:
            self._btn_color = Color(*bg)
            self._btn_rect = RoundedRectangle(radius=[dp(self.radius)])
        self.bind(pos=self._sync, size=self._sync, bg=self._sync_color,
                  disabled=self._sync_color)
        self.bind(on_press=self._pressed, on_release=self._released)
        if self.halign == "left":
            self.bind(size=lambda inst, size: setattr(
                inst, "text_size", (max(0, size[0] - dp(6)), None)))

    def _sync(self, *_args):
        self._btn_rect.pos = self.pos
        self._btn_rect.size = self.size
        self._btn_rect.radius = [dp(self.radius)]

    def _sync_color(self, *_args):
        if self.disabled:
            self._btn_color.rgba = shade(self.bg, 0.55)
        else:
            self._btn_color.rgba = self.bg

    def _pressed(self, *_args):
        if not self.disabled:
            self._btn_color.rgba = shade(self.bg, 0.72)

    def _released(self, *_args):
        self._sync_color()


class ToggleChip(FlatButton):
    """Two-state button used instead of a Switch (looks right on dark)."""

    def __init__(self, active=True, on_text="ON", off_text="OFF", **kwargs):
        self._on_text = on_text
        self._off_text = off_text
        kwargs.setdefault("font_size", 12)
        super().__init__(**kwargs)
        self.active = bool(active)
        self.bind(on_release=self._flip)
        self._apply()

    def _flip(self, *_args):
        self.active = not self.active
        self._apply()

    def _apply(self):
        self.text = self._on_text if self.active else self._off_text
        self.bg = ACCENT if self.active else PANEL_HI
        self.color = TEXT if self.active else MUTED
        self._sync_color()


class CheckBadge(Widget):
    """Green disc with a drawn tick (font independent, no glyph tofu)."""

    color = ColorProperty(GREEN)

    def __init__(self, size=44, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(size), dp(size)))
        super().__init__(**kwargs)
        with self.canvas:
            self._disc_color = Color(*self.color)
            self._disc = Ellipse()
            Color(1, 1, 1, 1)
            self._check_a = Line(width=dp(2.8))
            self._check_b = Line(width=dp(2.8))
        self.bind(pos=self._sync, size=self._sync, color=self._sync_color)

    def _sync(self, *_args):
        x, y = self.pos
        w, h = self.size
        self._disc.pos = (x, y)
        self._disc.size = (w, h)
        self._check_a.points = [
            x + w * 0.27, y + h * 0.52, x + w * 0.44, y + h * 0.31,
        ]
        self._check_b.points = [
            x + w * 0.44, y + h * 0.31, x + w * 0.75, y + h * 0.70,
        ]

    def _sync_color(self, *_args):
        self._disc_color.rgba = self.color


class Bar(Widget):
    """Thin progress bar drawn without images."""

    value = NumericProperty(0.0)
    color = ColorProperty(ACCENT)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas:
            self._track_color = Color(*PANEL_HI)
            self._track = RoundedRectangle(radius=[dp(3)])
            self._fill_color = Color(*self.color)
            self._fill = RoundedRectangle(radius=[dp(3)])
        self.bind(pos=self._sync, size=self._sync, value=self._sync,
                  color=self._sync_color)

    def _sync(self, *_args):
        self._track.pos = self.pos
        self._track.size = self.size
        width = max(0.0, min(1.0, float(self.value))) * self.width
        self._fill.pos = self.pos
        self._fill.size = (width, self.height)

    def _sync_color(self, *_args):
        self._fill_color.rgba = self.color


class Divider(Widget):
    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(1))
        super().__init__(**kwargs)
        with self.canvas:
            self._color = Color(*BORDER)
            self._rect = RoundedRectangle(radius=[0])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_args):
        self._rect.pos = self.pos
        self._rect.size = self.size


class HSpacer(Widget):
    pass


def make_label(text="", size=14, color=TEXT, bold=False, height=None,
               halign="left", valign="middle", **kwargs):
    if height is not None:
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(height))
    label = Label(text=text, font_size=sp(size), color=color, bold=bold,
                  halign=halign, valign=valign, **kwargs)
    label.bind(width=lambda inst, width: setattr(inst, "text_size", (width, None)))
    return label
