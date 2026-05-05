"""Worker subprocess streams default to DEVNULL when no log_path is given."""
import subprocess
from unittest.mock import patch

from mirror.worker import process


def test_pipelines_default_to_devnull():
    captured = {}

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self.pid = 7
            self.returncode = None
            self.stdin = None
            self.stdout = None
            self.stderr = None

        def poll(self):
            return None

    with patch("mirror.worker.process.subprocess.Popen", _FakePopen):
        job = process.create("p_test", ["true"], {}, None, None, 0)
        try:
            assert captured["stdin"] is subprocess.DEVNULL
            assert captured["stdout"] is subprocess.DEVNULL
            assert captured["stderr"] is subprocess.DEVNULL
        finally:
            with process._jobs_lock:
                process._jobs.pop("p_test", None)
