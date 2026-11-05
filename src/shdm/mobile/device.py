"""Platform bridge for the Kivy build.

Every Android-specific call lives here, wrapped so that importing this module
(and previewing the UI on a desktop) never fails.  When pyjnius or the
``android`` package is unavailable each helper falls back to a sensible
desktop behaviour.
"""

from __future__ import annotations

import ctypes
import mimetypes
import os
import re
import subprocess
import sys
from pathlib import Path

PACKAGE = "org.shdownloadmanager.shdownloadmanager"
AUTHORITY = PACKAGE + ".fileprovider"

URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)

ANDROID = bool(os.environ.get("ANDROID_ARGUMENT")) or hasattr(sys, "getandroidapilevel")


# ---------------------------------------------------------------------------
# Android basics
# ---------------------------------------------------------------------------
def activity():
    from jnius import autoclass

    return autoclass("org.kivy.android.PythonActivity").mActivity


def sdk_int() -> int:
    if not ANDROID:
        return 0
    try:
        from jnius import autoclass

        return int(autoclass("android.os.Build$VERSION").SDK_INT)
    except Exception:
        return 0


def _argb(value: str) -> int:
    """Convert ``#rrggbb`` to a signed Android colour int."""
    raw = value.lstrip("#")
    if len(raw) == 6:
        raw = "ff" + raw
    return ctypes.c_int32(int(raw, 16)).value


def apply_system_bars() -> None:
    """Paint the status and navigation bars to match the app chrome."""
    if not ANDROID:
        return
    try:
        window = activity().getWindow()
        window.setStatusBarColor(_argb("#1b1b1b"))
        window.setNavigationBarColor(_argb("#1b1b1b"))
    except Exception:
        pass


def request_permissions(callback=None) -> None:
    """Ask for legacy storage access only where it still exists (API <= 28)."""
    if not ANDROID:
        return
    try:
        from android.permissions import Permission, request_permissions as request

        needed = []
        if sdk_int() <= 28:
            needed.append(Permission.WRITE_EXTERNAL_STORAGE)
        if needed:
            request(needed, callback or (lambda *_: None))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def external_files_dir() -> Path:
    """App-private folder on shared storage (always writable, no permission)."""
    try:
        folder = activity().getExternalFilesDir(None)
        if folder is not None:
            return Path(str(folder.getAbsolutePath()))
    except Exception:
        pass
    return Path.home()


def public_download_dir() -> Path | None:
    try:
        from android.storage import primary_external_storage_path

        return Path(str(primary_external_storage_path())) / "Download" / "SH Download Manager"
    except Exception:
        return None


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".shdm-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def pick_download_dir() -> Path:
    """Prefer the public Download folder, fall back to app storage."""
    candidates = []
    public = public_download_dir()
    if public is not None:
        candidates.append(public)
    candidates.append(external_files_dir() / "Downloads")
    candidates.append(Path.home() / "Downloads")
    for candidate in candidates:
        if _writable(candidate):
            return candidate
    return external_files_dir()


def resolve_download_dir(settings) -> None:
    """Replace an unusable stored folder with one this device can write to."""
    if not ANDROID:
        return
    current = Path(str(settings.get("download_dir") or ""))
    if current.is_dir() and _writable(current):
        return
    new = pick_download_dir()
    settings.set("download_dir", str(new))
    settings.save()


# ---------------------------------------------------------------------------
# Keep the CPU awake while transfers run
# ---------------------------------------------------------------------------
class WakeLock:
    def __init__(self) -> None:
        self._lock = None
        self._held = False

    def acquire(self) -> None:
        if not ANDROID or self._held:
            return
        try:
            from jnius import autoclass

            manager_cls = autoclass("android.os.PowerManager")
            context_cls = autoclass("android.content.Context")
            ctx = activity()
            manager = ctx.getSystemService(context_cls.POWER_SERVICE)
            self._lock = manager.newWakeLock(
                manager_cls.PARTIAL_WAKE_LOCK, PACKAGE + ":downloads"
            )
            self._lock.setReferenceCounted(False)
            self._lock.acquire()
            self._held = True
        except Exception:
            self._lock = None
            self._held = False

    def release(self) -> None:
        if not self._held:
            return
        try:
            self._lock.release()
        except Exception:
            pass
        self._lock = None
        self._held = False


# ---------------------------------------------------------------------------
# Sharing / opening
# ---------------------------------------------------------------------------
def first_url(text: str) -> str:
    match = URL_RE.search(text or "")
    return match.group(0) if match else ""


def _intent_url(intent) -> str:
    if intent is None:
        return ""
    try:
        from jnius import autoclass

        Intent = autoclass("android.content.Intent")
        action = str(intent.getAction() or "")
        data = intent.getData()
        if data is not None and action == "android.intent.action.VIEW":
            return first_url(str(data.toString()))
        if action == "android.intent.action.SEND":
            extra = intent.getStringExtra(Intent.EXTRA_TEXT)
            text = str(extra or "")
            return first_url(text) or text.strip()
    except Exception:
        pass
    return ""


def bind_share_intent(callback) -> None:
    """Deliver URLs shared from a browser, plus the intent that launched us."""
    if not ANDROID:
        return
    try:
        from android import activity as android_activity

        def handle(intent):
            url = _intent_url(intent)
            if url:
                callback(url)

        android_activity.bind(on_new_intent=handle)
        handle(activity().getIntent())
    except Exception:
        pass


def open_file(path) -> None:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    if not ANDROID:
        _open_desktop(path)
        return

    from jnius import autoclass

    Intent = autoclass("android.content.Intent")
    File = autoclass("java.io.File")
    FileProvider = autoclass("androidx.core.content.FileProvider")

    uri = FileProvider.getUriForFile(activity(), AUTHORITY, File(str(path)))
    mime, _ = mimetypes.guess_type(str(path))
    intent = Intent(Intent.ACTION_VIEW)
    intent.setDataAndType(uri, mime or "application/octet-stream")
    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    activity().startActivity(Intent.createChooser(intent, "Open with"))


def open_folder(path) -> None:
    path = Path(path)
    folder = path if path.is_dir() else path.parent
    if not ANDROID:
        _open_desktop(folder)
        return

    from jnius import autoclass

    Intent = autoclass("android.content.Intent")
    File = autoclass("java.io.File")
    FileProvider = autoclass("androidx.core.content.FileProvider")

    uri = FileProvider.getUriForFile(activity(), AUTHORITY, File(str(folder)))
    intent = Intent(Intent.ACTION_VIEW)
    intent.setDataAndType(uri, "resource/folder")
    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    activity().startActivity(Intent.createChooser(intent, "Open folder with"))


def _open_desktop(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
