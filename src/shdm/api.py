from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs

from .settings import APP_NAME, VERSION


class BrowserAPI:
    """Small localhost-only HTTP endpoint for the bundled browser extension."""

    def __init__(
        self,
        port: int,
        on_add: Callable[[dict], dict],
        on_list: Callable[[], list],
        on_focus: Callable[[], None],
        on_pause: Callable[[str], bool],
        on_resume: Callable[[str], bool],
        on_formats: Callable[[str], list] | None = None,
    ) -> None:
        self.port = int(port)
        self.on_add = on_add
        self.on_list = on_list
        self.on_focus = on_focus
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_formats = on_formats
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> tuple[bool, str]:
        if self.server is not None:
            return True, ""
        api = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass

            def _origin_allowed(self) -> bool:
                origin = (self.headers.get("Origin") or "").lower()
                if not origin:
                    return True
                return origin.startswith((
                    "chrome-extension://", "moz-extension://", "safari-web-extension://",
                    "extension://", "http://127.0.0.1", "http://localhost",
                ))

            def _cors(self):
                origin = self.headers.get("Origin")
                if origin and self._origin_allowed():
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Max-Age", "86400")
                self.send_header("X-Content-Type-Options", "nosniff")

            def _json(self, code: int, payload: dict):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                if not self._origin_allowed():
                    self._json(403, {"ok": False, "error": "Origin not allowed"})
                    return
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self._cors()
                self.end_headers()

            def do_GET(self):
                if not self._origin_allowed():
                    self._json(403, {"ok": False, "error": "Origin not allowed"})
                    return
                path = self.path.split("?", 1)[0]
                if path == "/ping":
                    self._json(200, {"ok": True, "name": APP_NAME, "version": VERSION,
                                     "api": 1})
                elif path == "/list":
                    try:
                        self._json(200, {"ok": True, "tasks": api.on_list()})
                    except Exception as exc:  # noqa: BLE001
                        self._json(500, {"ok": False, "error": str(exc)})
                else:
                    self._json(404, {"ok": False, "error": "Unknown endpoint"})

            def do_POST(self):
                path = self.path.split("?", 1)[0]
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if length > 1024 * 1024:
                    self._json(413, {"ok": False, "error": "Request too large"})
                    return
                raw = self.rfile.read(length) if length else b""
                payload: dict = {}
                if raw:
                    try:
                        payload = json.loads(raw.decode("utf-8"))
                    except Exception:
                        form = parse_qs(raw.decode("utf-8", "replace"))
                        payload = {k: v[0] for k, v in form.items() if v}
                if not isinstance(payload, dict):
                    self._json(400, {"ok": False, "error": "Expected a JSON object"})
                    return
                try:
                    if path == "/formats":
                        target = str(payload.get("url") or "").strip()
                        if not target:
                            raise ValueError("No URL supplied.")
                        formats = api.on_formats(target) if api.on_formats else []
                        self._json(200, {"ok": True, "formats": formats})
                    elif path == "/add":
                        result = api.on_add(payload)
                        self._json(200, {"ok": True, **result})
                    elif path == "/focus":
                        api.on_focus()
                        self._json(200, {"ok": True})
                    elif path in ("/pause", "/resume"):
                        task_id = str(payload.get("id", ""))
                        changed = api.on_pause(task_id) if path == "/pause" else api.on_resume(task_id)
                        self._json(200 if changed else 404,
                                   {"ok": changed, "error": "" if changed else "Unknown download"})
                    else:
                        self._json(404, {"ok": False, "error": "Unknown endpoint"})
                except ValueError as exc:
                    self._json(400, {"ok": False, "error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    self._json(500, {"ok": False, "error": str(exc)})

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
            self.server.daemon_threads = True
        except OSError as exc:
            self.server = None
            return False, str(exc)
        self.thread = threading.Thread(target=self.server.serve_forever, name="shdm-api", daemon=True)
        self.thread.start()
        return True, ""

    def stop(self) -> None:
        if self.server is None:
            return
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        self.server = None
        self.thread = None
