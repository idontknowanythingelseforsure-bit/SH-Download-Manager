from __future__ import annotations

import argparse
import ctypes
import sys
from urllib.parse import parse_qs, urlparse

import requests

from .app import SHApp
from .settings import APP_NAME, Settings, VERSION

_MUTEX = None


def _dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _message(title: str, text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    except Exception:
        print(text)


def _parse_urls(values: list[str]) -> list[str]:
    urls: list[str] = []
    for value in values:
        value = value.strip().strip('"')
        if not value:
            continue
        if value.lower().startswith("shdm://"):
            query = parse_qs(urlparse(value).query)
            for key in ("url", "u", "link"):
                urls.extend(query.get(key, []))
        elif value.lower().startswith(("http://", "https://")):
            urls.append(value)
    return urls


def _already_running(port: int, urls: list[str]) -> bool:
    base = f"http://127.0.0.1:{port}"
    try:
        response = requests.get(base + "/ping", timeout=1.5)
        if response.status_code != 200:
            return False
    except Exception:
        return False
    try:
        if urls:
            requests.post(base + "/add", json={"urls": urls, "start": True}, timeout=3)
        else:
            requests.post(base + "/focus", json={}, timeout=2)
        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    global _MUTEX
    parser = argparse.ArgumentParser(prog="SH Download Manager", add_help=True)
    parser.add_argument("urls", nargs="*", help="URLs to download")
    parser.add_argument("--url", action="append", default=[], help="URL to download")
    parser.add_argument("--minimized", action="store_true", help="Start minimized")
    parser.add_argument("--version", action="store_true", help="Show version")
    args = parser.parse_args(argv)

    if args.version:
        _message(APP_NAME, f"{APP_NAME} {VERSION}")
        return 0

    urls = _parse_urls([*args.urls, *args.url])
    settings = Settings()

    kernel32 = ctypes.windll.kernel32
    _MUTEX = kernel32.CreateMutexW(None, False, "Local\\SHDownloadManager.SingleInstance")
    already = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    if already:
        if _already_running(int(settings.get("api_port", 53991)), urls):
            return 0
        _message(APP_NAME, f"{APP_NAME} is already running.\n\n"
                           "Enable the local browser API in Settings if you want to send links "
                           "to the running window.")
        return 0

    _dpi_aware()
    app = SHApp(urls=urls, minimized=args.minimized)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
