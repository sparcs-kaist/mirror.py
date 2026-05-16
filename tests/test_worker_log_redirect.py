"""Worker log redirect: subprocess streams go to log file when log_path is set."""
import subprocess
import time
import os
from pathlib import Path
from unittest.mock import patch

from mirror.worker import process


def test_log_path_redirects_output(tmp_path):
    """When log_path is given, stdout is redirected to a file and process produces output."""
    log_file = tmp_path / "worker.log"
    command = ["/bin/sh", "-c", "echo 'Line 1'; echo 'Line 2'"]

    worker = process.create("log_test_worker", command, {}, os.getuid(), os.getgid(), 0, log_path=log_file)

    try:
        max_retries = 30
        while worker.is_running and max_retries > 0:
            time.sleep(0.1)
            max_retries -= 1

        assert log_file.exists()
        content = log_file.read_bytes()
        assert b"Line 1" in content
        assert b"Line 2" in content
    finally:
        worker.stop()
        with process._jobs_lock:
            process._jobs.pop("log_test_worker", None)


def test_log_path_popen_kwargs(monkeypatch, tmp_path):
    """When log_path is provided, Popen must be called with stdin=DEVNULL and stderr=STDOUT."""
    log_file = tmp_path / "test.log"
    captured = {}

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self.pid = 42
            self.returncode = None

        def poll(self):
            return None

    monkeypatch.setattr("mirror.worker.process.subprocess.Popen", _FakePopen)

    job = process.create("log_kwargs_test", ["true"], {}, os.getuid(), os.getgid(), 0, log_path=log_file)

    try:
        assert captured["stdin"] is subprocess.DEVNULL
        assert captured["stderr"] is subprocess.STDOUT
        # stdout should be a file-like object (log handle), not DEVNULL or PIPE
        assert captured["stdout"] is not subprocess.DEVNULL
        assert captured["stdout"] is not subprocess.PIPE
    finally:
        with process._jobs_lock:
            process._jobs.pop("log_kwargs_test", None)
