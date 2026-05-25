"""Concurrency tests for prune_finished: exactly-once notification and collision-safe re-insert."""
import os
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

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
        return 0

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
    """Patch send_finished_notification on all possible module locations."""
    import mirror.socket

    current_worker = getattr(mirror.socket, "worker", _original_worker_mod)
    if not hasattr(current_worker, "send_finished_notification"):
        current_worker = _original_worker_mod

    if _original_worker_mod is current_worker:
        with patch.object(_original_worker_mod, "send_finished_notification", fn):
            yield
    else:
        with patch.object(_original_worker_mod, "send_finished_notification", fn):
            with patch.object(current_worker, "send_finished_notification", fn):
                yield


def test_concurrent_prune_notifies_once():
    """N threads calling prune_finished simultaneously must notify exactly once per wid."""
    wid = "conc_test"
    _setup_fresh_job(wid)

    call_args = []
    call_lock = threading.Lock()

    def _slow_notify(job_id, success, returncode):
        time.sleep(0.05)
        with call_lock:
            call_args.append(job_id)

    barrier = threading.Barrier(10)

    def _worker():
        barrier.wait()
        process.prune_finished()

    try:
        with _patch_notification(_slow_notify):
            threads = [threading.Thread(target=_worker) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        wid_calls = [a for a in call_args if a == wid]
        assert len(wid_calls) == 1, (
            f"Expected exactly 1 notification for {wid}, got {len(wid_calls)}"
        )
    finally:
        with process._jobs_lock:
            process._jobs.pop(wid, None)


def test_collision_safe_reinsert():
    """When notification fails and a new job J2 occupies the wid, J1 must not overwrite J2."""
    wid = "coll_test"
    j1 = _setup_fresh_job(wid)
    j1._notify_attempts = 0

    j2 = _setup_fresh_job("coll_test_j2_placeholder")
    with process._jobs_lock:
        process._jobs.pop("coll_test_j2_placeholder", None)

    def _inject_and_raise(job_id, success, returncode):
        with process._jobs_lock:
            process._jobs[wid] = j2
        raise ConnectionError("no client")

    try:
        with _patch_notification(_inject_and_raise):
            with patch("mirror.worker.process.logger") as mock_logger:
                process.prune_finished()

        assert process._jobs.get(wid) is j2, (
            "J2 should remain in _jobs; J1 must not overwrite it"
        )

        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("Cannot re-queue" in w for w in warning_calls), (
            f"Expected 'Cannot re-queue' warning, got: {warning_calls}"
        )
    finally:
        with process._jobs_lock:
            process._jobs.pop(wid, None)
