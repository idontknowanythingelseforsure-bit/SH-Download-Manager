from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path

APP_NAME = "SH Download Manager"
APP_ID = "SHDownloadManager"
VERSION = "1.1.0"

CATEGORIES: dict[str, list[str]] = {
    "Compressed": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".zst", ".iso", ".cab"],
    "Documents": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".epub",
                  ".mobi", ".csv", ".rtf", ".odt", ".ods"],
    "Music": [".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma", ".aiff", ".mid"],
    "Video": [".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg",
              ".ts", ".m2ts", ".3gp", ".ogv"],
    "Programs": [".exe", ".msi", ".apk", ".dmg", ".pkg", ".deb", ".rpm", ".appx", ".msix",
                 ".jar", ".bin"],
    "Images": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff",
               ".heic", ".avif"],
}
CATEGORY_NAMES = list(CATEGORIES)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def default_download_dir() -> str:
    home = Path.home()
    downloads = home / "Downloads"
    return str(downloads if downloads.exists() else home)


def category_for(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    for name, exts in CATEGORIES.items():
        if ext in exts:
            return name
    return "Other"


DEFAULTS: dict = {
    "download_dir": default_download_dir(),
    "use_category_folders": True,
    "category_dirs": {name: "" for name in [*CATEGORY_NAMES, "Other"]},
    "recent_dirs": [],
    "max_concurrent": 3,
    "connections": 8,
    "min_segment_size": 1024 * 1024,
    "max_retries": 5,
    "connect_timeout": 20,
    "read_timeout": 45,
    "user_agent": USER_AGENT,
    "speed_limit_kbps": 0,
    "proxy_mode": "system",          # system | none | manual
    "proxy_url": "",
    "clipboard_monitor": False,
    "start_minimized": False,
    "notify_on_complete": True,
    "close_to_tray": True,
    "launch_on_start": True,
    "api_enabled": True,
    "api_port": 53991,
    "remember_last_dir": True,
    "last_save_dir": "",
    "window_geometry": "",
    "sort_column": "added",
    "sort_desc": True,
    "show_details": True,
    "confirm_exit": True,
    "theme": "light",
    "max_segments_factor": 2,
}


class Settings:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path(os.environ.get("APPDATA", Path.home())) / APP_ID
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.base_dir / "settings.json"
        self._lock = threading.RLock()
        self._data = deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as fh:
                    stored = json.load(fh)
                if isinstance(stored, dict):
                    for key, value in stored.items():
                        if key in DEFAULTS and isinstance(value, type(DEFAULTS[key])):
                            self._data[key] = value
                        elif key in ("category_dirs",) and isinstance(value, dict):
                            merged = dict(DEFAULTS[key])
                            merged.update({k: v for k, v in value.items() if isinstance(v, str)})
                            self._data[key] = merged
        except Exception:
            pass

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            tmp.replace(self.path)

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value

    def update(self, values: dict) -> None:
        with self._lock:
            self._data.update(values)

    def as_dict(self) -> dict:
        with self._lock:
            return deepcopy(self._data)

    def download_dir_for(self, category: str) -> Path:
        with self._lock:
            if self._data.get("use_category_folders"):
                custom = (self._data.get("category_dirs") or {}).get(category, "")
                if custom:
                    return Path(custom)
                return Path(self._data["download_dir"]) / category
            return Path(self._data["download_dir"])

    def proxies(self) -> dict | None:
        mode = self.get("proxy_mode")
        if mode == "manual" and self.get("proxy_url"):
            url = self.get("proxy_url")
            return {"http": url, "https": url}
        return None

    def trust_env(self) -> bool:
        return self.get("proxy_mode") == "system"
