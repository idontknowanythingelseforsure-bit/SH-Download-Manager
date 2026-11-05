"""Kivy front end used by the Android build.

The download engine, settings and helpers in ``shdm`` are shared with the
Windows/Tkinter front end and stay platform independent.  This package only
contains the touch UI and the small Android bridge.
"""

from __future__ import annotations

__all__ = ["app"]
