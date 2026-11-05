"""Local HTTP test server used by the engine tests (no external network)."""
from __future__ import annotations

import hashlib
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SIZE = 6 * 1024 * 1024


def payload(size: int = SIZE) -> bytes:
    unit = bytes((i * 37 + 11) % 256 for i in range(65536))
    repeats = size // len(unit) + 1
    return (unit * repeats)[:size]


BODY = payload()
DIGEST = hashlib.sha256(BODY).hexdigest()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    flaky_counts: dict[str, int] = {}
    lock = threading.Lock()

    def log_message(self, fmt, *args):  # silence test output
        pass

    def _range(self, size: int):
        header = self.headers.get("Range", "")
        match = re.match(r"bytes=(\d+)-(\d*)", header)
        if not match:
            return None
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else size - 1
        if start >= size:
            return "unsatisfiable", start, end
        return "ok", start, min(end, size - 1)

    def do_HEAD(self):
        self._serve(head_only=True)

    def do_GET(self):
        self._serve()

    def _serve(self, head_only: bool = False):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path
        size = int(query.get("size", [str(SIZE)])[0])
        body = BODY[:size] if size <= len(BODY) else payload(size)
        no_range = path.startswith("/norange")
        flaky = path.startswith("/flaky")
        rate = int(query.get("rate", ["0"])[0])

        if flaky:
            key = query.get("key", ["default"])[0]
            with self.lock:
                count = self.flaky_counts.get(key, 0)
                self.flaky_counts[key] = count + 1
            if count < int(query.get("fail", ["2"])[0]):
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        if path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/file?size=1048576")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        disposition = None
        if path == "/disp":
            disposition = 'attachment; filename="report final.bin"'
        if path == "/utf8name":
            disposition = "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf"

        status = 200
        start, end = 0, len(body) - 1
        if not no_range:
            rng = self._range(len(body))
            if rng and rng[0] == "unsatisfiable":
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(body)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if rng:
                status = 206
                _, start, end = rng
        chunk = body[start:end + 1]

        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Accept-Ranges", "none" if no_range else "bytes")
        self.send_header("ETag", '"shdm-test-etag"')
        if disposition:
            self.send_header("Content-Disposition", disposition)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
        self.end_headers()
        if head_only:
            return

        send = self.wfile.write
        step = 32768
        for offset in range(0, len(chunk), step):
            try:
                send(chunk[offset:offset + step])
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                return
            if rate > 0:
                time.sleep(step / rate)


def start_server(port: int = 0):
    class QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass

    server = QuietServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}", DIGEST


if __name__ == "__main__":
    server, base, digest = start_server(8765)
    print(base, digest)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
