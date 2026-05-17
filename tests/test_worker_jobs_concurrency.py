"""Concurrent _jobs access is thread-safe."""
import threading
import os
from unittest.mock import patch

import pytest

from mirror.worker import process


@pytest.fixture(autouse=True)
def isolate_jobs_state():
    """Clear `_jobs` before and after each test to defend against contamination
    from sibling tests (notably `tests/test_socket.py`'s module-load tricks
    that mutate `sys.modules` and can leave stale entries behind).
    """
    with process._jobs_lock:
        process._jobs.clear()
    yield
    with process._jobs_lock:
        process._jobs.clear()


class _FakePopen:
    def __init__(self, *args, **kwargs):
        self.pid = 1
        self.returncode = None
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def test_concurrent_create_and_get_no_race():
    threads = []
    errors = []

    def _create(i):
        try:
            process.create(f"j{i}", ["true"], {}, os.getuid(), os.getgid(), 0)
        except Exception as exc:
            errors.append((i, exc))

    def _get(i):
        try:
            process.get(f"j{i}")
            process.get_all()
        except Exception as exc:
            errors.append((i, exc))

    for i in range(50):
        threads.append(threading.Thread(target=_create, args=(i,)))
        threads.append(threading.Thread(target=_get, args=(i,)))

    with patch("mirror.worker.process.subprocess.Popen", _FakePopen):
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

    try:
        assert not errors, f"errors: {errors}"
        with process._jobs_lock:
            assert len(process._jobs) == 50
    finally:
        with process._jobs_lock:
            for i in range(50):
                process._jobs.pop(f"j{i}", None)


def test_duplicate_create_rejected():
    with patch("mirror.worker.process.subprocess.Popen", _FakePopen):
        process.create("dup", ["true"], {}, os.getuid(), os.getgid(), 0)
        try:
            try:
                process.create("dup", ["true"], {}, os.getuid(), os.getgid(), 0)
                raise AssertionError("expected ValueError")
            except ValueError:
                pass
        finally:
            with process._jobs_lock:
                process._jobs.pop("dup", None)
