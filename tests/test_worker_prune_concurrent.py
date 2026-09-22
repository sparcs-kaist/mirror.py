"""Concurrency tests for prune_finished: exactly-once notification and collision-safe re-insert."""
import threading
import time
from unittest.mock import patch

from mirror.worker import process


def test_concurrent_prune_notifies_once(
    finished_job_factory,
    patch_worker_notification,
):
    """N threads calling prune_finished simultaneously must notify exactly once per wid."""
    wid = "conc_test"
    finished_job_factory(wid)

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
        with patch_worker_notification(_slow_notify):
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


def test_collision_safe_reinsert(
    finished_job_factory,
    patch_worker_notification,
):
    """When notification fails and a new job J2 occupies the wid, J1 must not overwrite J2."""
    wid = "coll_test"
    j1 = finished_job_factory(wid)
    j1._notify_attempts = 0

    j2 = finished_job_factory("coll_test_j2_placeholder")
    with process._jobs_lock:
        process._jobs.pop("coll_test_j2_placeholder", None)

    def _inject_and_raise(job_id, success, returncode):
        with process._jobs_lock:
            process._jobs[wid] = j2
        raise ConnectionError("no client")

    try:
        with patch_worker_notification(_inject_and_raise):
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
