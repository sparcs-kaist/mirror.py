"""prune_finished retries notification and force-prunes after budget."""
import importlib
import sys
import os
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

# Capture the original mirror.socket.worker module at import time, before any
# test contamination can replace the mirror.socket.worker attribute.
import mirror.socket.worker as _original_worker_mod

from mirror.worker import process


class _FinishedFakePopen:
    def __init__(self, *args, **kwargs):
        self.pid = 1
        self.returncode = 0
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def poll(self):
        return 0  # already done

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def _setup_fresh_job(job_id: str) -> process.Job:
    """Create a finished job in _jobs with completely clean state."""
    with process._jobs_lock:
        process._jobs.pop(job_id, None)

    with patch("mirror.worker.process.subprocess.Popen", _FinishedFakePopen):
        return process.create(job_id, ["true"], {}, os.getuid(), os.getgid(), 0)


@contextmanager
def _patch_notification(fn):
    """Patch send_finished_notification on all possible module locations
    that prune_finished() might resolve to."""
    import mirror.socket

    # Determine the module object that prune_finished()'s lazy import will resolve to.
    # This might be the original module or whatever mirror.socket.worker currently is.
    current_worker = getattr(mirror.socket, "worker", _original_worker_mod)
    if not hasattr(current_worker, "send_finished_notification"):
        # Possibly a MagicMock from another test; patch our original module instead
        current_worker = _original_worker_mod

    # Patch on both original and current to be safe
    targets = set()
    targets.add(id(_original_worker_mod))
    targets.add(id(current_worker))

    if _original_worker_mod is current_worker:
        with patch.object(_original_worker_mod, "send_finished_notification", fn):
            yield
    else:
        # Two distinct module objects; patch both
        with patch.object(_original_worker_mod, "send_finished_notification", fn):
            with patch.object(current_worker, "send_finished_notification", fn):
                yield


def test_prune_force_prunes_after_attempt_budget():
    job = _setup_fresh_job("pr_test")
    job._notify_attempts = 0

    raised_count = {"n": 0}

    def _raising(*args, **kwargs):
        raised_count["n"] += 1
        raise ConnectionError("no client")

    try:
        with _patch_notification(_raising):
            for attempt in range(1, process.NOTIFY_ATTEMPT_BUDGET):
                process.prune_finished()
                assert process.get("pr_test") is not None, (
                    f"job was pruned early after attempt {attempt}"
                )
            # This call pushes attempts to NOTIFY_ATTEMPT_BUDGET -> force-prune
            process.prune_finished()
            assert process.get("pr_test") is None

        assert raised_count["n"] >= process.NOTIFY_ATTEMPT_BUDGET
    finally:
        with process._jobs_lock:
            process._jobs.pop("pr_test", None)


def test_prune_succeeds_when_notification_works():
    sent = []

    def _ok(job_id, success, returncode):
        sent.append((job_id, success, returncode))

    _setup_fresh_job("pr_ok")

    try:
        with _patch_notification(_ok):
            process.prune_finished()
        assert ("pr_ok", True, 0) in sent
        assert process.get("pr_ok") is None
    finally:
        with process._jobs_lock:
            process._jobs.pop("pr_ok", None)
