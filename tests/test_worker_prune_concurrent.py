"""Concurrency tests for prune_finished: exactly-once notification and collision-safe re-insert."""
import multiprocessing
import threading
import time
from unittest.mock import patch

from mirror.worker import process


class _FinishedJob:
    """Minimal finished job used in the isolated concurrency check."""

    _notify_attempts = 0
    returncode = 0
    is_running = False

    def reap(self) -> None:
        pass


def _run_concurrent_prune(connection) -> None:
    """Exercise concurrent pruning in a child so a deadlock cannot hang pytest."""
    import mirror.socket.worker as worker_module

    wid = "conc_test"
    with process._jobs_lock:
        process._jobs.clear()
        process._jobs[wid] = _FinishedJob()

    call_args: list[str] = []
    call_lock = threading.Lock()
    thread_errors: list[str] = []
    error_lock = threading.Lock()

    def _slow_notify(job_id, success, returncode):
        time.sleep(0.05)
        with call_lock:
            call_args.append(job_id)

    barrier = threading.Barrier(10)

    def _worker():
        try:
            barrier.wait(timeout=2.0)
            process.prune_finished()
        except BaseException as exc:
            with error_lock:
                thread_errors.append(repr(exc))

    try:
        with patch.object(worker_module, "send_finished_notification", _slow_notify):
            threads = [threading.Thread(target=_worker, daemon=True) for _ in range(10)]
            for t in threads:
                t.start()

            deadline = time.monotonic() + 4.0
            for t in threads:
                t.join(timeout=max(0.0, deadline - time.monotonic()))

        alive = sum(t.is_alive() for t in threads)
        if alive:
            connection.send({"alive": alive, "errors": thread_errors})
            return

        with process._jobs_lock:
            job_remains = wid in process._jobs
        connection.send(
            {
                "alive": 0,
                "errors": thread_errors,
                "calls": call_args,
                "job_remains": job_remains,
            }
        )
    finally:
        connection.close()


def test_concurrent_prune_notifies_once():
    """N threads calling prune_finished simultaneously must notify exactly once per wid."""
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    child = context.Process(target=_run_concurrent_prune, args=(send,))
    child.start()
    send.close()
    child.join(timeout=6.0)

    try:
        if child.is_alive():
            child.terminate()
            child.join(timeout=2.0)
            if child.is_alive():
                child.kill()
                child.join(timeout=2.0)
            raise AssertionError("concurrent prune child process deadlocked")

        assert child.exitcode == 0, f"concurrent prune child exited with {child.exitcode}"
        assert receive.poll(timeout=1.0), "concurrent prune child returned no result"
        result = receive.recv()
        assert result["alive"] == 0, f"prune threads did not finish: {result}"
        assert result["errors"] == [], f"prune thread failed: {result['errors']}"
        assert result["calls"] == ["conc_test"]
        assert result["job_remains"] is False
    finally:
        receive.close()
        if child.is_alive():
            child.kill()
            child.join(timeout=2.0)
        if not child.is_alive():
            child.close()


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
