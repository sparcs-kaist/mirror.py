"""Worker-process restart tests.

Verifies that the worker process can be restarted without losing sync capability
and that master handles temporary worker unavailability gracefully.
"""

import time

import pytest

from .helpers import temporary_rsync_package


@pytest.mark.integration
def test_worker_restart_recovers(mirror_stack):
    """Restarting worker does not break subsequent syncs.

    After worker restarts, master should reconnect and the next triggered sync
    must complete with ACTIVE status.
    """
    with temporary_rsync_package(
        mirror_stack, "worker-restart-rsync"
    ) as pkgid:
        mirror_stack.restart_process("worker")
        mirror_stack.wait_for_worker_ready(timeout=30)

        lastsync_before = mirror_stack.package_lastsync(pkgid)
        mirror_stack.trigger_sync(pkgid)
        lastsync_after = mirror_stack.wait_for_new_active_sync(
            pkgid, lastsync_before, timeout=30
        )

        assert lastsync_after > lastsync_before


@pytest.mark.integration
def test_master_handles_worker_unavailable(mirror_stack):
    """Master tolerates a stopped worker and recovers when worker comes back.

    Stops the worker, triggers a sync (expected to error or stay pending), then
    restarts the worker and confirms a subsequent sync completes.
    """
    with temporary_rsync_package(
        mirror_stack, "worker-unavailable-rsync"
    ) as pkgid:
        mirror_stack.stop_process("worker")

        # Give master a moment to notice the worker is gone.
        time.sleep(3)

        # The master accepts the request, while worker delegation fails in the
        # async runner and records an ERROR generation.
        mirror_stack.trigger_sync(pkgid)
        mirror_stack.wait_for_status(pkgid, "ERROR", timeout=30)
        failed_lastsync = mirror_stack.package_lastsync(pkgid)
        assert failed_lastsync > 0

        mirror_stack.start_process("worker")
        mirror_stack.wait_for_worker_ready(timeout=30)
        runtime_package = mirror_stack.runtime_package(pkgid)
        assert runtime_package["status"]["status"] == "ERROR"

        # After worker comes back, a fresh sync must advance lastsync. Merely
        # observing the package's old ACTIVE state would not prove recovery.
        lastsync_before = failed_lastsync
        mirror_stack.trigger_sync(pkgid)
        lastsync_after = mirror_stack.wait_for_new_active_sync(
            pkgid, lastsync_before, timeout=30
        )
        assert lastsync_after > lastsync_before
