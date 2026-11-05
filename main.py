"""Kivy entry point (Android, and a desktop preview of the mobile UI).

The Windows product still starts from ``src/run.py``.  This file exists for
python-for-android, which always looks for ``main.py`` in the source tree.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from kivy.config import Config  # noqa: E402

# Older Android GPUs are happier without multisampling; harmless elsewhere.
Config.set("graphics", "multisamples", "0")
Config.set("kivy", "exit_on_escape", "0")

from shdm.mobile.app import main  # noqa: E402

if __name__ == "__main__":
    main()
