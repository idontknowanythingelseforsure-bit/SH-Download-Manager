from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import requests

from .settings import APP_ID, Settings, category_for
from .util import human_eta, human_size, human_speed, sanitize_filename, unique_path

CHUNK = 64 * 1024
USER_AGENT = None  # replaced by settings


class FatalError(Exception):
    pass


class TransientError(Exception):
    pass


class PauseRequested(Exception):
    pass


class RestartRequired(Exception):
    pass


class RateLimiter:
    """A simple global token bucket. 0 kbps disables limiting."""

    def __init__(self, kbps: int = 0) -> None:
        self.lock = threading.Lock()
        self.rate = max(0, int(kbps)) * 1024
        self.tokens = float(self.rate)
        self.last = time.monotonic()

    def update(self, kbps: int) -> None:
        with self.lock:
            self.rate = max(0, int(kbps)) * 1024
            self.tokens = min(self.tokens, float(self.rate))
            self.last = time.monotonic()

    def consume(self, amount: int) -> None:
        if self.rate <= 0:
            return
        amount = max(1, int(amount))
        capacity = float(max(self.rate, amount))
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(capacity, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= amount:
                    self.tokens -= amount
                    return
                wait = (amount - self.tokens) / self.rate
            # Keep accrued tokens: zeroing them here starves every waiter when
            # several segment threads share the same limiter.
            time.sleep(min(max(wait, 0.005), 0.25))


@dataclass
class Segment:
    start: int
    end: int | None
    pos: int
    status: str = "pending"       # pending | active | done | failed
    attempts: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def remaining(self) -> int | None:
        if self.end is None:
            return None
        return max(0, self.end - self.pos + 1)

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "pos": self.pos,
                "status": self.status, "attempts": self.attempts}

    @classmethod
    def from_dict(cls, data: dict) -> "Segment":
        return cls(int(data["start"]), data.get("end"), int(data.get("pos", data["start"])),
                   data.get("status", "pending"), int(data.get("attempts", 0)))


def _url_filename(url: str) -> str:
    try:
        path = unquote(urlparse(url).path)
        name = path.rstrip("/").rsplit("/", 1)[-1]
        if name:
            return sanitize_filename(name)
        host = urlparse(url).hostname or "download"
        return sanitize_filename(host)
    except Exception:
        return "download"


def _parse_content_disposition(value: str | None) -> str | None:
    if not value:
        return None
    # RFC 5987 filename*
    m = re.search(r"filename\*\s*=\s*([^;]+)", value, re.I)
    if m:
        raw = m.group(1).strip().strip('"')
        parts = raw.split("''", 1)
        encoded = parts[-1]
        try:
            from urllib.parse import unquote
            return unquote(encoded, encoding="utf-8", errors="replace")
        except Exception:
            pass
    m = re.search(r'filename\s*=\s*"([^"]*)"', value, re.I)
    if m:
        return m.group(1)
    m = re.search(r"filename\s*=\s*([^;]+)", value, re.I)
    if m:
        return m.group(1).strip().strip('"')
    return None


def _parse_content_range(value: str) -> tuple[int, int, int | None] | None:
    m = re.match(r"\s*bytes\s+(\d+)-(\d+)/(\d+|\*)\s*$", value or "", re.I)
    if not m:
        return None
    total = None if m.group(3) == "*" else int(m.group(3))
    return int(m.group(1)), int(m.group(2)), total


class DownloadTask:
    def __init__(
        self,
        id: str,
        url: str,
        file_name: str,
        save_dir: str,
        target_path: str,
        temp_path: str,
        *,
        category: str = "Other",
        referer: str = "",
        cookie_header: str = "",
        user_agent: str = "",
        connections: int = 8,
        retries: int = 5,
        connect_timeout: int = 20,
        read_timeout: int = 45,
        name_locked: bool = False,
        added_at: float | None = None,
        ytdl: bool = False,
        ytdl_format: str = "",
    ) -> None:
        self.id = id
        self.url = url
        self.final_url = url
        self.file_name = file_name
        self.save_dir = save_dir
        self.target_path = target_path
        self.temp_path = temp_path
        self.category = category
        self.referer = referer
        self.cookie_header = cookie_header
        self.user_agent = user_agent
        self.connections = max(1, min(32, connections))
        self.retries = max(0, min(20, retries))
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.name_locked = name_locked
        self.added_at = added_at or time.time()
        self.ytdl = bool(ytdl)
        self.ytdl_format = ytdl_format or ""
        self.started_at: float | None = None
        self.completed_at: float | None = None
        self.total_size: int | None = None
        self.downloaded = 0
        self.segments: list[Segment] = []
        self.status = "queued"
        self.error = ""
        self.speed = 0.0
        self.eta: float | None = None
        self.mime = ""
        self.etag = ""
        self.last_modified = ""
        self.resumable = False
        self.notes: list[str] = []
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()
        self.fail_event = threading.Event()
        self.restart_requested = False
        self.lock = threading.RLock()
        self.progress_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ----- state helpers -------------------------------------------------
    def set_status(self, status: str) -> None:
        with self.lock:
            self.status = status

    def note(self, message: str) -> None:
        with self.lock:
            self.notes.append(f"{time.strftime('%H:%M:%S')}  {message}")
            self.notes = self.notes[-80:]

    def base_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent or "SHDownloadManager/1.0",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
        }
        if self.referer:
            headers["Referer"] = self.referer
        if self.cookie_header:
            headers["Cookie"] = self.cookie_header
        return headers

    def progress(self) -> float | None:
        with self.lock:
            if not self.total_size:
                return None
            return max(0.0, min(1.0, self.downloaded / self.total_size))

    def recompute_downloaded(self) -> None:
        with self.lock:
            self.downloaded = sum(max(0, s.pos - s.start) for s in self.segments)
            if self.total_size and self.status == "completed":
                self.downloaded = self.total_size

    def reset(self, total: int | None) -> None:
        with self.lock:
            self.total_size = total if total and total > 0 else None
            self.downloaded = 0
            self.resumable = False
            self.etag = ""
            self.last_modified = ""
            self.segments = [Segment(0, (self.total_size - 1) if self.total_size else None, 0)]
            try:
                with open(self.temp_path, "wb"):
                    pass
            except Exception:
                pass

    def plan_segments(self, min_segment: int) -> None:
        with self.lock:
            total = self.total_size
            if not total:
                self.segments = [Segment(0, None, 0)]
                return
            if not self.resumable or total < min_segment * 2:
                self.segments = [Segment(0, total - 1, 0)]
                return
            count = max(1, min(self.connections, total // max(1, min_segment)))
            base = total // count
            segments = []
            start = 0
            for i in range(count):
                end = total - 1 if i == count - 1 else start + base - 1
                segments.append(Segment(start, end, start))
                start = end + 1
            self.segments = segments

    def snapshot(self) -> dict:
        with self.lock:
            total = self.total_size
            downloaded = self.downloaded
            segs = [s.to_dict() for s in self.segments]
            active = sum(1 for s in self.segments if s.status == "active")
            return {
                "id": self.id,
                "url": self.url,
                "final_url": self.final_url,
                "file_name": self.file_name,
                "save_dir": self.save_dir,
                "target_path": self.target_path,
                "temp_path": self.temp_path,
                "category": self.category,
                "status": self.status,
                "error": self.error,
                "total_size": total,
                "downloaded": downloaded,
                "progress": (downloaded / total) if total else None,
                "speed": self.speed,
                "eta": self.eta,
                "connections": self.connections,
                "active_segments": active,
                "segment_count": len(self.segments),
                "added_at": self.added_at,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "size_text": human_size(total) if total else human_size(downloaded),
                "speed_text": human_speed(self.speed),
                "eta_text": human_eta(self.eta),
                "notes": list(self.notes[-6:]),
            }

    def to_dict(self) -> dict:
        with self.lock:
            return {
                "version": 1,
                "id": self.id,
                "url": self.url,
                "final_url": self.final_url,
                "file_name": self.file_name,
                "save_dir": self.save_dir,
                "target_path": self.target_path,
                "temp_path": self.temp_path,
                "category": self.category,
                "referer": self.referer,
                "cookie_header": self.cookie_header,
                "user_agent": self.user_agent,
                "connections": self.connections,
                "retries": self.retries,
                "connect_timeout": self.connect_timeout,
                "read_timeout": self.read_timeout,
                "name_locked": self.name_locked,
                "ytdl": self.ytdl,
                "ytdl_format": self.ytdl_format,
                "added_at": self.added_at,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "total_size": self.total_size,
                "downloaded": self.downloaded,
                "segments": [s.to_dict() for s in self.segments],
                "status": self.status,
                "error": self.error,
                "mime": self.mime,
                "etag": self.etag,
                "last_modified": self.last_modified,
                "resumable": self.resumable,
                "notes": self.notes[-80:],
            }

    @classmethod
    def from_dict(cls, data: dict) -> "DownloadTask":
        task = cls(
            data["id"], data["url"], data["file_name"], data["save_dir"],
            data["target_path"], data["temp_path"],
            category=data.get("category", "Other"),
            referer=data.get("referer", ""),
            cookie_header=data.get("cookie_header", ""),
            user_agent=data.get("user_agent", ""),
            connections=int(data.get("connections", 8)),
            retries=int(data.get("retries", 5)),
            connect_timeout=int(data.get("connect_timeout", 20)),
            read_timeout=int(data.get("read_timeout", 45)),
            name_locked=bool(data.get("name_locked", False)),
            added_at=float(data.get("added_at", time.time())),
            ytdl=bool(data.get("ytdl", False)),
            ytdl_format=str(data.get("ytdl_format", "")),
        )
        task.final_url = data.get("final_url", task.url)
        task.started_at = data.get("started_at")
        task.completed_at = data.get("completed_at")
        task.total_size = data.get("total_size")
        task.downloaded = int(data.get("downloaded", 0))
        task.status = data.get("status", "paused")
        task.error = data.get("error", "")
        task.mime = data.get("mime", "")
        task.etag = data.get("etag", "")
        task.last_modified = data.get("last_modified", "")
        task.resumable = bool(data.get("resumable", False))
        task.notes = list(data.get("notes", []))[-80:]
        task.segments = [Segment.from_dict(s) for s in data.get("segments", [])]
        for segment in task.segments:
            if segment.status in ("active", "failed"):
                segment.status = "pending"
        if task.segments:
            task.recompute_downloaded()
        return task


class DownloadManager:
    def __init__(self, settings: Settings, state_path: Path | None = None,
                 on_event: Callable[[str, str], None] | None = None) -> None:
        self.settings = settings
        self.state_path = state_path or (settings.base_dir / "downloads.json")
        self.lock = threading.RLock()
        self.wake = threading.Condition(self.lock)
        self.tasks: dict[str, DownloadTask] = {}
        self.active = 0
        self.stop = False
        self.global_speed = 0.0
        self.on_event = on_event
        self.limiter = RateLimiter(int(settings.get("speed_limit_kbps", 0)))
        self._last_saved = 0.0
        self._load()
        self._scheduler = threading.Thread(target=self._schedule_loop, name="shdm-scheduler", daemon=True)
        self._sampler = threading.Thread(target=self._sample_loop, name="shdm-sampler", daemon=True)
        self._scheduler.start()
        self._sampler.start()

    # ----- persistence --------------------------------------------------
    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            with self.state_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            for raw in data.get("tasks", []):
                try:
                    task = DownloadTask.from_dict(raw)
                except Exception:
                    continue
                if task.status in ("queued", "connecting", "downloading", "pausing"):
                    task.status = "paused"
                if task.status == "completed" and not Path(task.target_path).exists():
                    task.status = "error"
                    task.error = "File was moved or deleted."
                if task.status == "error" and not task.error:
                    task.error = "Interrupted."
                self.tasks[task.id] = task
        except Exception:
            pass

    def save(self, force: bool = False) -> None:
        now = time.time()
        with self.lock:
            if not force and now - self._last_saved < 1.5:
                return
            self._last_saved = now
            payload = {"version": 1, "saved_at": now,
                       "tasks": [t.to_dict() for t in self.tasks.values()]}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1)
            tmp.replace(self.state_path)
        except Exception:
            pass

    def shutdown(self) -> None:
        self.stop = True
        with self.wake:
            self.wake.notify_all()
        for task in self.snapshots_raw():
            task.cancel_event.set()
            task.pause_event.set()
        self.save(force=True)

    # ----- task creation ------------------------------------------------
    def add(self, url: str, *, save_dir: str | None = None, file_name: str | None = None,
            referer: str = "", cookie_header: str = "", connections: int | None = None,
            start_now: bool = True, user_agent: str | None = None) -> tuple[DownloadTask, bool]:
        url = (url or "").strip()
        if not re.match(r"^https?://", url, re.I):
            raise ValueError("Only http:// and https:// URLs are supported.")
        duplicate = self.find_duplicate(url)
        if duplicate is not None:
            return duplicate, False
        name = sanitize_filename(file_name or _url_filename(url))
        category = category_for(name)
        directory = Path(save_dir) if save_dir else self.settings.download_dir_for(category)
        directory.mkdir(parents=True, exist_ok=True)
        target = unique_path(directory / name)
        temp = target.with_name(target.name + ".shdm-part")
        task = DownloadTask(
            uuid.uuid4().hex, url, target.name, str(directory), str(target), str(temp),
            category=category,
            referer=referer,
            cookie_header=cookie_header,
            user_agent=user_agent or str(self.settings.get("user_agent")),
            connections=int(connections or self.settings.get("connections", 8)),
            retries=int(self.settings.get("max_retries", 5)),
            connect_timeout=int(self.settings.get("connect_timeout", 20)),
            read_timeout=int(self.settings.get("read_timeout", 45)),
            name_locked=bool(file_name),
        )
        task.status = "queued" if start_now else "paused"
        with self.lock:
            self.tasks[task.id] = task
        self.save(force=True)
        self._emit("added", task.id)
        with self.wake:
            self.wake.notify_all()
        return task, True

    def add_ytdl(self, url: str, format_selector: str = "", *, kind: str = "video",
                 save_dir: str | None = None, referer: str = "", cookie_header: str = "",
                 start_now: bool = True, user_agent: str | None = None
                 ) -> tuple[DownloadTask, bool]:
        """Add a media-page download handled by yt-dlp (YouTube and similar)."""
        url = (url or "").strip()
        if not re.match(r"^https?://", url, re.I):
            raise ValueError("Only http:// and https:// URLs are supported.")
        duplicate = self.find_duplicate(url)
        if duplicate is not None:
            return duplicate, False
        ext = ".mp3" if kind == "audio" else ".mp4"
        stem = Path(_url_filename(url)).stem or "media"
        name = sanitize_filename(stem + ext)
        category = "Music" if kind == "audio" else "Video"
        directory = Path(save_dir) if save_dir else self.settings.download_dir_for(category)
        directory.mkdir(parents=True, exist_ok=True)
        target = unique_path(directory / name)
        temp = target.with_name(target.name + ".shdm-part")
        task = DownloadTask(
            uuid.uuid4().hex, url, target.name, str(directory), str(target), str(temp),
            category=category,
            referer=referer,
            cookie_header=cookie_header,
            user_agent=user_agent or str(self.settings.get("user_agent")),
            connections=1,
            retries=int(self.settings.get("max_retries", 5)),
            connect_timeout=int(self.settings.get("connect_timeout", 20)),
            read_timeout=int(self.settings.get("read_timeout", 45)),
            ytdl=True,
            ytdl_format=format_selector or "",
        )
        task.status = "queued" if start_now else "paused"
        with self.lock:
            self.tasks[task.id] = task
        self.save(force=True)
        self._emit("added", task.id)
        with self.wake:
            self.wake.notify_all()
        return task, True

    def find_duplicate(self, url: str) -> DownloadTask | None:
        normalized = url.split("#", 1)[0]
        with self.lock:
            for task in self.tasks.values():
                if task.status in ("completed", "error", "canceled"):
                    continue
                if task.url.split("#", 1)[0] == normalized:
                    return task
        return None

    # ----- controls -----------------------------------------------------
    def get(self, task_id: str) -> DownloadTask | None:
        with self.lock:
            return self.tasks.get(task_id)

    def snapshots_raw(self) -> list[DownloadTask]:
        with self.lock:
            return list(self.tasks.values())

    def snapshots(self) -> list[dict]:
        with self.lock:
            tasks = list(self.tasks.values())
        return [t.snapshot() for t in tasks]

    def start(self, task: DownloadTask) -> None:
        if task.status in ("completed", "canceled"):
            return
        task.cancel_event.clear()
        task.fail_event.clear()
        task.pause_event.clear()
        task.restart_requested = False
        task.error = ""
        with task.lock:
            for segment in task.segments:
                if segment.status in ("failed", "active"):
                    segment.status = "pending"
        task.set_status("queued")
        with self.wake:
            self.wake.notify_all()
        self._emit("queued", task.id)

    def pause(self, task: DownloadTask) -> None:
        with task.lock:
            if task.status in ("completed", "error", "paused", "canceled"):
                return
            if task.status == "queued":
                task.status = "paused"
                task.pause_event.clear()
                self._emit("paused", task.id)
                return
            task.pause_event.set()
            task.status = "pausing"
        self._emit("pausing", task.id)

    def resume(self, task: DownloadTask) -> None:
        self.start(task)

    def remove(self, task: DownloadTask, delete_temp: bool = True) -> None:
        task.cancel_event.set()
        task.pause_event.set()
        task.fail_event.set()
        with self.lock:
            self.tasks.pop(task.id, None)
        if delete_temp:
            try:
                Path(task.temp_path).unlink(missing_ok=True)
            except Exception:
                pass
        self.save(force=True)
        self._emit("removed", task.id)

    def pause_all(self) -> None:
        for task in self.snapshots_raw():
            if task.status in ("queued", "connecting", "downloading", "pausing"):
                self.pause(task)

    def resume_all(self) -> None:
        for task in self.snapshots_raw():
            if task.status in ("paused", "error"):
                self.start(task)

    def clear_completed(self, delete_files: bool = False) -> None:
        with self.lock:
            doomed = [t for t in self.tasks.values() if t.status == "completed"]
            for task in doomed:
                self.tasks.pop(task.id, None)
        if delete_files:
            for task in doomed:
                try:
                    Path(task.target_path).unlink(missing_ok=True)
                except Exception:
                    pass
        self.save(force=True)
        self._emit("cleared", "")

    def apply_settings(self) -> None:
        self.limiter.update(int(self.settings.get("speed_limit_kbps", 0)))
        with self.wake:
            self.wake.notify_all()

    def stats(self) -> dict:
        with self.lock:
            tasks = list(self.tasks.values())
        active = sum(1 for t in tasks if t.status in ("connecting", "downloading", "pausing"))
        queued = sum(1 for t in tasks if t.status == "queued")
        completed = sum(1 for t in tasks if t.status == "completed")
        speed = sum(t.speed for t in tasks)
        return {"active": active, "queued": queued, "completed": completed,
                "total": len(tasks), "speed": speed}

    def _emit(self, event: str, task_id: str) -> None:
        if self.on_event:
            try:
                self.on_event(event, task_id)
            except Exception:
                pass

    # ----- scheduling ---------------------------------------------------
    def _schedule_loop(self) -> None:
        while not self.stop:
            started = []
            with self.lock:
                limit = max(1, int(self.settings.get("max_concurrent", 3)))
                while self.active < limit:
                    candidate = None
                    for task in sorted(self.tasks.values(), key=lambda t: t.added_at):
                        if task.status == "queued" and not task.cancel_event.is_set():
                            candidate = task
                            break
                    if candidate is None:
                        break
                    candidate.status = "connecting"
                    self.active += 1
                    started.append(candidate)
            for task in started:
                thread = threading.Thread(target=self._run_task, args=(task,),
                                          name=f"shdm-{task.id[:8]}", daemon=True)
                task._thread = thread
                thread.start()
            with self.wake:
                self.wake.wait(timeout=0.5)

    def _run_task(self, task: DownloadTask) -> None:
        try:
            if task.ytdl:
                self._download_ytdl(task)
            else:
                self._download(task)
        except PauseRequested:
            if not task.cancel_event.is_set():
                task.set_status("paused")
                task.speed = 0
                task.eta = None
        except RestartRequired as exc:
            task.error = str(exc) or "Server does not support resuming."
            task.set_status("error")
        except FatalError as exc:
            task.error = str(exc)
            task.set_status("error")
        except Exception as exc:  # noqa: BLE001 - surface engine failures in the UI
            task.error = f"{type(exc).__name__}: {exc}"
            task.set_status("error")
        finally:
            task.speed = 0
            task.eta = None
            with self.lock:
                self.active = max(0, self.active - 1)
            self.save(force=True)
            with self.wake:
                self.wake.notify_all()
            self._emit("changed", task.id)

    # ----- transfer -----------------------------------------------------
    def _make_session(self, task: DownloadTask) -> requests.Session:
        session = requests.Session()
        session.trust_env = self.settings.trust_env()
        proxies = self.settings.proxies()
        if proxies:
            session.proxies.update(proxies)
        session.headers.update(task.base_headers())
        return session

    def _probe(self, task: DownloadTask) -> None:
        if task.cancel_event.is_set():
            raise PauseRequested
        session = self._make_session(task)
        headers = task.base_headers()
        headers["Range"] = "bytes=0-"
        try:
            response = session.get(task.url, headers=headers, stream=True, allow_redirects=True,
                                   timeout=(task.connect_timeout, task.read_timeout))
        except requests.RequestException as exc:
            session.close()
            raise TransientError(f"Could not connect: {exc}") from exc
        try:
            status = response.status_code
            if status in (401, 402, 403, 404, 405, 407, 410, 451):
                raise FatalError(f"HTTP {status} {response.reason or ''}".strip())
            if status not in (200, 206, 416):
                raise TransientError(f"HTTP {status} {response.reason or ''}".strip())

            total: int | None = None
            resumable = False
            if status == 206:
                parsed = _parse_content_range(response.headers.get("Content-Range", ""))
                if parsed:
                    start, end, total = parsed
                    resumable = total is not None and total > 0
            elif status == 416:
                parsed = _parse_content_range(response.headers.get("Content-Range", ""))
                if parsed and parsed[2]:
                    total = parsed[2]
                    resumable = True
            else:
                try:
                    total = int(response.headers.get("Content-Length", "") or 0) or None
                except ValueError:
                    total = None
                resumable = False

            task.final_url = response.url or task.url
            task.mime = (response.headers.get("Content-Type") or "").split(";")[0].strip()
            old_etag = task.etag
            new_etag = response.headers.get("ETag", "")
            new_lm = response.headers.get("Last-Modified", "")
            if new_etag:
                task.etag = new_etag
            if new_lm:
                task.last_modified = new_lm

            # Capture cookies set during the probe so range requests keep the session.
            try:
                cookies = requests.utils.dict_from_cookiejar(session.cookies)
                if cookies:
                    extra = "; ".join(f"{k}={v}" for k, v in cookies.items())
                    if task.cookie_header:
                        existing = {p.split("=", 1)[0].strip() for p in task.cookie_header.split(";") if "=" in p}
                        extra = "; ".join(f"{k}={v}" for k, v in cookies.items() if k not in existing)
                        if extra:
                            task.cookie_header = f"{task.cookie_header}; {extra}"
                    else:
                        task.cookie_header = extra
            except Exception:
                pass

            if not task.name_locked and task.downloaded == 0 and not Path(task.temp_path).exists():
                disposition = _parse_content_disposition(response.headers.get("Content-Disposition"))
                if disposition:
                    name = sanitize_filename(disposition)
                    if name and name != task.file_name:
                        task.file_name = name
                        target = unique_path(Path(task.save_dir) / name)
                        task.target_path = str(target)
                        task.temp_path = str(target.with_name(target.name + ".shdm-part"))
            task.category = category_for(task.file_name)

            reset = False
            if task.total_size and total and task.total_size != total:
                task.note(f"Remote size changed from {human_size(task.total_size)} to {human_size(total)}; restarting.")
                reset = True
            elif task.downloaded > 0 and not resumable:
                task.note("Server stopped advertising byte ranges; restarting.")
                reset = True
            elif task.downloaded > 0 and old_etag and new_etag and old_etag != new_etag:
                task.note("Remote file changed; restarting.")
                reset = True

            if reset:
                task.reset(total)
            task.total_size = total
            task.resumable = resumable
            if reset or not task.segments:
                task.plan_segments(int(self.settings.get("min_segment_size", 1024 * 1024)))
        finally:
            try:
                response.close()
            except Exception:
                pass
            session.close()

        if task.cancel_event.is_set():
            raise PauseRequested
        if task.pause_event.is_set():
            raise PauseRequested

    def _prepare_file(self, task: DownloadTask) -> None:
        path = Path(task.temp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
        size = path.stat().st_size
        if task.total_size:
            if size != task.total_size:
                with open(path, "r+b") as fh:
                    fh.truncate(task.total_size)
        elif size > 0 and task.downloaded == 0:
            with open(path, "r+b") as fh:
                fh.truncate(0)

    def _download(self, task: DownloadTask) -> None:
        if task.cancel_event.is_set():
            raise PauseRequested
        task.started_at = task.started_at or time.time()
        task.fail_event.clear()
        task.restart_requested = False
        task.error = ""

        task.set_status("connecting")
        probe_attempt = 0
        while True:
            try:
                self._probe(task)
                break
            except TransientError as exc:
                probe_attempt += 1
                if probe_attempt > task.retries:
                    raise
                task.note(f"Connection attempt {probe_attempt} failed: {exc}")
                delay = min(2 ** probe_attempt, 15)
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    if task.pause_event.is_set() or task.cancel_event.is_set():
                        raise PauseRequested
                    time.sleep(0.15)
        if task.pause_event.is_set():
            raise PauseRequested

        if task.downloaded > 0 and not Path(task.temp_path).exists():
            task.note("Partial file is missing; starting over.")
            task.reset(task.total_size)
            task.plan_segments(int(self.settings.get("min_segment_size", 1024 * 1024)))

        self._prepare_file(task)
        task.set_status("downloading")
        self._emit("changed", task.id)

        restart_attempts = 0
        while True:
            with task.lock:
                for segment in task.segments:
                    if segment.status == "pending" and segment.end is not None and segment.pos > segment.end:
                        segment.status = "done"
            if not task.segments:
                raise FatalError("The server did not return a downloadable file.")

            workers = max(1, min(task.connections, len(task.segments)))
            threads = []
            for i in range(workers):
                thread = threading.Thread(target=self._worker, args=(task,),
                                          name=f"shdm-w{i}-{task.id[:6]}", daemon=True)
                threads.append(thread)
                thread.start()

            while any(t.is_alive() for t in threads):
                if task.pause_event.is_set() or task.cancel_event.is_set():
                    break
                if task.fail_event.is_set():
                    break
                for thread in threads:
                    thread.join(timeout=0.1)
                    if thread.is_alive():
                        break

            for thread in threads:
                thread.join(timeout=5)

            if task.cancel_event.is_set() or task.pause_event.is_set():
                raise PauseRequested

            if task.restart_requested and restart_attempts == 0:
                restart_attempts += 1
                task.restart_requested = False
                task.fail_event.clear()
                task.error = ""
                total = task.total_size
                task.reset(total)
                task.resumable = False
                task.plan_segments(int(self.settings.get("min_segment_size", 1024 * 1024)))
                self._prepare_file(task)
                task.note("Server ignored byte ranges; restarting from the beginning.")
                continue

            if task.fail_event.is_set():
                raise FatalError(task.error or "Download failed.")
            if any(s.status != "done" for s in task.segments):
                raise FatalError(task.error or "Download did not finish.")

            self._finalize(task)
            return

    def _download_ytdl(self, task: DownloadTask) -> None:
        from . import ytdl

        if task.cancel_event.is_set():
            raise PauseRequested
        task.started_at = task.started_at or time.time()
        task.set_status("connecting")
        self._emit("changed", task.id)

        def stop() -> bool:
            return task.cancel_event.is_set() or task.pause_event.is_set()

        def progress(data: dict) -> None:
            if data.get("status") == "downloading":
                task.set_status("downloading")
                task.downloaded = int(data.get("downloaded_bytes") or 0)
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                if total:
                    task.total_size = int(total)
                if data.get("speed"):
                    task.speed = float(data["speed"])
                if data.get("eta") is not None:
                    task.eta = float(data["eta"])
                self._emit("changed", task.id)

        try:
            final, info = ytdl.download(task.url, task.ytdl_format,
                                        Path(task.save_dir), task.id,
                                        progress=progress, stop=stop)
        except ytdl.Cancelled:
            raise PauseRequested
        except Exception as exc:  # noqa: BLE001 - surface extractor errors in the UI
            raise FatalError(str(exc)) from exc

        info = info or {}
        title = str(info.get("title") or Path(final).stem)
        ext = Path(final).suffix or ".mp4"
        if title.lower().endswith(ext.lower()):
            name = sanitize_filename(title)
        else:
            name = sanitize_filename(title + ext)
        target = unique_path(Path(task.save_dir) / name)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if Path(final) != target:
                os.replace(final, target)
        except Exception:
            target = Path(final)
        task.file_name = target.name
        task.target_path = str(target)
        task.temp_path = str(target) + ".shdm-part"
        task.category = category_for(task.file_name)
        if not task.total_size:
            try:
                task.total_size = target.stat().st_size
            except Exception:
                pass
        task.downloaded = task.total_size or task.downloaded
        task.completed_at = time.time()
        task.speed = 0
        task.eta = None
        task.set_status("completed")
        task.note("Download complete.")
        self._emit("completed", task.id)

    def _worker(self, task: DownloadTask) -> None:
        session = self._make_session(task)
        try:
            while not (task.pause_event.is_set() or task.cancel_event.is_set() or task.fail_event.is_set()):
                segment = self._acquire_segment(task)
                if segment is None:
                    return
                try:
                    self._fetch_segment(task, segment, session)
                    with segment.lock:
                        if segment.end is None or segment.pos > segment.end:
                            segment.status = "done"
                        else:
                            segment.status = "pending"
                except PauseRequested:
                    with segment.lock:
                        segment.status = "pending"
                    return
                except RestartRequired:
                    task.restart_requested = True
                    task.fail_event.set()
                    return
                except Exception as exc:  # noqa: BLE001
                    with segment.lock:
                        segment.status = "failed"
                    task.error = str(exc)
                    task.fail_event.set()
                    return
        finally:
            session.close()

    def _acquire_segment(self, task: DownloadTask) -> Segment | None:
        with task.lock:
            for segment in task.segments:
                if (segment.status == "pending" and segment.end is not None
                        and segment.pos > segment.end):
                    segment.status = "done"
            free = [s for s in task.segments if s.status == "pending" and (s.remaining() is None or s.remaining() > 0)]
            if free:
                segment = free[0]
                segment.status = "active"
                return segment
            if task.fail_event.is_set() or task.pause_event.is_set():
                return None
            factor = max(1, int(self.settings.get("max_segments_factor", 2)))
            if len(task.segments) >= task.connections * factor:
                return None
            min_size = int(self.settings.get("min_segment_size", 1024 * 1024))
            candidates = [s for s in task.segments
                          if s.status == "active" and s.end is not None and s.remaining() is not None
                          and s.remaining() > min_size * 2]
            if not candidates:
                return None
            victim = max(candidates, key=lambda s: s.remaining() or 0)
            with victim.lock:
                remaining = victim.remaining() or 0
                if remaining <= min_size * 2:
                    return None
                middle = victim.pos + remaining // 2 - 1
                new_segment = Segment(middle + 1, victim.end, middle + 1, "active")
                victim.end = middle
                task.segments.append(new_segment)
                task.segments.sort(key=lambda s: s.start)
                task.note(f"Split a connection to use available bandwidth ({len(task.segments)} parts).")
                return new_segment

    def _fetch_segment(self, task: DownloadTask, segment: Segment, session: requests.Session) -> None:
        attempt = 0
        while True:
            if task.pause_event.is_set() or task.cancel_event.is_set():
                raise PauseRequested
            if task.fail_event.is_set():
                return
            if segment.end is not None and segment.pos > segment.end:
                return

            headers = task.base_headers()
            end = "" if segment.end is None else str(segment.end)
            headers["Range"] = f"bytes={segment.pos}-{end}"
            if task.etag:
                headers["If-Range"] = task.etag
            try:
                with session.get(task.final_url or task.url, headers=headers, stream=True,
                                 allow_redirects=True,
                                 timeout=(task.connect_timeout, task.read_timeout)) as response:
                    status = response.status_code
                    if status in (401, 402, 403, 404, 405, 407, 410, 451):
                        raise FatalError(f"HTTP {status} {response.reason or ''}".strip())
                    if status == 416:
                        with segment.lock:
                            if task.total_size and segment.pos >= task.total_size:
                                segment.pos = (segment.end + 1) if segment.end is not None else task.total_size
                        return
                    if status not in (200, 206):
                        raise TransientError(f"HTTP {status} {response.reason or ''}".strip())

                    skip = 0
                    if status == 206:
                        parsed = _parse_content_range(response.headers.get("Content-Range", ""))
                        if parsed:
                            start, resp_end, total = parsed
                            if total and not task.total_size:
                                task.total_size = total
                            if start != segment.pos:
                                raise TransientError("Server returned an unexpected byte range.")
                    else:
                        if segment.pos > 0 or len(task.segments) > 1:
                            raise RestartRequired("Server does not support resuming this file.")

                    mode_single = status == 200
                    received = 0
                    with open(task.temp_path, "r+b", buffering=0) as fh:
                        for chunk in response.iter_content(CHUNK):
                            if (task.pause_event.is_set() or task.cancel_event.is_set()
                                    or task.fail_event.is_set()):
                                raise PauseRequested
                            if not chunk:
                                continue
                            with segment.lock:
                                if segment.end is not None and segment.pos > segment.end:
                                    break
                                if segment.end is None:
                                    count = len(chunk)
                                else:
                                    count = min(len(chunk), segment.end - segment.pos + 1)
                                if count <= 0:
                                    break
                                fh.seek(segment.pos)
                                fh.write(chunk[:count])
                                segment.pos += count
                                with task.progress_lock:
                                    task.downloaded += count
                                received += count
                            self.limiter.consume(count)

                    if mode_single:
                        with task.lock:
                            if task.total_size is None:
                                task.total_size = segment.pos
                            if segment.pos != task.total_size:
                                raise TransientError("Connection closed before the file finished.")
                            segment.end = task.total_size - 1
                        return
                    if segment.end is not None and segment.pos <= segment.end:
                        raise TransientError("Connection closed before this part finished.")
                    return
            except (FatalError, RestartRequired, PauseRequested):
                raise
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                if attempt > task.retries:
                    raise TransientError(f"Giving up after {attempt} attempts: {exc}") from exc
                task.note(f"Retry {attempt}/{task.retries} for byte {segment.pos}: {exc}")
                delay = min(2 ** attempt, 20)
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    if task.pause_event.is_set() or task.cancel_event.is_set():
                        raise PauseRequested
                    if task.fail_event.is_set():
                        return
                    time.sleep(0.15)

    def _finalize(self, task: DownloadTask) -> None:
        temp = Path(task.temp_path)
        target = Path(task.target_path)
        if not temp.exists():
            raise FatalError("Temporary file is missing.")
        actual = temp.stat().st_size
        if task.total_size and actual != task.total_size:
            raise FatalError(f"File size mismatch: expected {task.total_size} bytes, got {actual}.")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = unique_path(target)
            task.target_path = str(target)
        os.replace(temp, target)
        task.downloaded = task.total_size or actual
        task.completed_at = time.time()
        task.speed = 0
        task.eta = None
        task.set_status("completed")
        task.note("Download complete.")
        self._emit("completed", task.id)

    def _sample_loop(self) -> None:
        last: dict[str, int] = {}
        while not self.stop:
            time.sleep(1.0)
            total_speed = 0.0
            for task in self.snapshots_raw():
                with task.lock:
                    if task.status in ("downloading", "connecting"):
                        previous = last.get(task.id, task.downloaded)
                        delta = max(0, task.downloaded - previous)
                        last[task.id] = task.downloaded
                        if delta:
                            task.speed = (task.speed * 0.4) + (delta * 0.6)
                        else:
                            task.speed *= 0.6
                        if task.speed < 512:
                            task.speed = 0
                        remaining = (task.total_size - task.downloaded) if task.total_size else None
                        task.eta = (remaining / task.speed) if (remaining and task.speed > 0) else None
                        total_speed += task.speed
                    else:
                        last[task.id] = task.downloaded
                        task.eta = None
            self.global_speed = total_speed
            self.save()
