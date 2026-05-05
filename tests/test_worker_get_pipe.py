"""get_pipe returns None when streams are redirected to DEVNULL."""
import subprocess
from unittest.mock import patch

from mirror.worker import process


def test_get_pipe_returns_none_when_streams_devnull():
    class _FakePopen:
        def __init__(self, *args, **kwargs):
            self.pid = 9
            self.returncode = None
            self.stdin = None
            self.stdout = None
            self.stderr = None

        def poll(self):
            return None

    with patch("mirror.worker.process.subprocess.Popen", _FakePopen):
        job = process.create("gp_test", ["true"], {}, None, None, 0)
        try:
            assert job.get_pipe("stdin") is None
            assert job.get_pipe("stdout") is None
            assert job.get_pipe("stderr") is None
        finally:
            with process._jobs_lock:
                process._jobs.pop("gp_test", None)
