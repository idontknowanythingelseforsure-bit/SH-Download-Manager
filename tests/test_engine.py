from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from shdm.engine import DownloadManager  # noqa: E402
from shdm.settings import Settings  # noqa: E402
from tests.test_server import BODY, payload, start_server  # noqa: E402


def digest_of(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def wait_until(condition, timeout: float = 90.0, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


class Harness:
    def __init__(self):
        self.server, self.base, _ = start_server()
        self.dir = Path(tempfile.mkdtemp(prefix="shdm-test-"))
        self.settings = Settings(base_dir=self.dir / "config")
        self.settings.set("max_concurrent", 2)
        self.manager = DownloadManager(self.settings, state_path=self.dir / "state.json")

    def close(self):
        self.manager.shutdown()
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)


def run_multipart():
    h = Harness()
    try:
        task, added = h.manager.add(h.base + "/file?size=6291456", save_dir=str(h.dir / "out"),
                                    connections=4, start_now=True)
        assert added
        assert wait_until(lambda: task.status == "completed", 60), (task.status, task.error)
        out = Path(task.target_path)
        assert out.stat().st_size == 6 * 1024 * 1024
        assert digest_of(out) == hashlib.sha256(BODY[:6 * 1024 * 1024]).hexdigest()
        assert not Path(task.temp_path).exists()
        assert task.snapshot()["segment_count"] >= 2
        print("ok multipart", task.snapshot()["segment_count"], "segments")
    finally:
        h.close()


def run_redirect_and_name():
    h = Harness()
    try:
        task, _ = h.manager.add(h.base + "/disp", save_dir=str(h.dir / "out"), connections=2)
        assert wait_until(lambda: task.status == "completed", 30), (task.status, task.error)
        assert task.file_name == "report final.bin", task.file_name
        assert Path(task.target_path).name == "report final.bin"
        assert digest_of(Path(task.target_path)) == hashlib.sha256(BODY).hexdigest()
        print("ok content-disposition")

        task2, _ = h.manager.add(h.base + "/redirect", save_dir=str(h.dir / "out"), connections=2)
        assert wait_until(lambda: task2.status == "completed", 30), (task2.status, task2.error)
        assert Path(task2.target_path).stat().st_size == 1024 * 1024
        print("ok redirect")
    finally:
        h.close()


def run_no_range():
    h = Harness()
    try:
        size = 3 * 1024 * 1024
        task, _ = h.manager.add(h.base + f"/norange?size={size}", save_dir=str(h.dir / "out"),
                                connections=8)
        assert wait_until(lambda: task.status == "completed", 30), (task.status, task.error)
        assert digest_of(Path(task.target_path)) == hashlib.sha256(BODY[:size]).hexdigest()
        print("ok single-stream")
    finally:
        h.close()


def run_pause_resume():
    h = Harness()
    try:
        size = 12 * 1024 * 1024
        task, _ = h.manager.add(h.base + f"/file?size={size}&rate=131072",
                                save_dir=str(h.dir / "out"), connections=4)
        assert wait_until(lambda: task.downloaded > 1024 * 1024, 20), task.downloaded
        h.manager.pause(task)
        assert wait_until(lambda: task.status == "paused", 15), (task.status, task.error)
        resume_point = task.downloaded
        time.sleep(1.0)
        assert task.downloaded >= resume_point
        assert task.downloaded - resume_point < 512 * 1024
        h.manager.resume(task)
        assert wait_until(lambda: task.status == "completed", 90), (task.status, task.error)
        assert digest_of(Path(task.target_path)) == hashlib.sha256(payload(size)).hexdigest()
        assert resume_point > 0
        print("ok pause/resume", resume_point, "bytes preserved")
    finally:
        h.close()


def run_retry():
    h = Harness()
    try:
        url = h.base + "/flaky?key=retry1&fail=1&size=2097152"
        task, _ = h.manager.add(url, save_dir=str(h.dir / "out"), connections=2)
        assert wait_until(lambda: task.status == "completed", 45), (task.status, task.error)
        assert digest_of(Path(task.target_path)) == hashlib.sha256(BODY[:2097152]).hexdigest()
        print("ok retry after 503")
    finally:
        h.close()


def run_rate_limit():
    """Regression: a speed limit must not starve parallel segment threads."""
    h = Harness()
    try:
        h.settings.set("speed_limit_kbps", 400)
        h.manager.apply_settings()
        started = time.monotonic()
        task, _ = h.manager.add(h.base + "/file?size=2097152",
                                save_dir=str(h.dir / "out"), connections=4)
        assert wait_until(lambda: task.status == "completed", 60), (task.status, task.error)
        elapsed = time.monotonic() - started
        assert elapsed >= 3.5, f"limit ignored: {elapsed:.1f}s"
        assert digest_of(Path(task.target_path)) == hashlib.sha256(BODY[:2097152]).hexdigest()
        print(f"ok rate limit ({elapsed:.1f}s for 2 MB at 400 KB/s)")
    finally:
        h.close()


def run_duplicate():
    h = Harness()
    try:
        url = h.base + "/file?size=1048576"
        first, added1 = h.manager.add(url, save_dir=str(h.dir / "out"), connections=1)
        second, added2 = h.manager.add(url, save_dir=str(h.dir / "out"), connections=1)
        assert added1 and not added2 and first.id == second.id
        assert wait_until(lambda: first.status == "completed", 30)
        print("ok duplicate detection")
    finally:
        h.close()


def main():
    tests = [run_multipart, run_redirect_and_name, run_no_range, run_pause_resume,
             run_retry, run_rate_limit, run_duplicate]
    selected = sys.argv[1:] or [t.__name__ for t in tests]
    failures = 0
    for test in tests:
        if test.__name__ not in selected and test.__name__.removeprefix("run_") not in selected:
            continue
        started = time.time()
        try:
            test()
            print(f"PASS {test.__name__} ({time.time() - started:.1f}s)")
        except Exception as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
