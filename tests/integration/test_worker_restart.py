"""Worker-process restart tests.

Verifies that the worker process can be restarted without losing sync capability
and that master handles temporary worker unavailability gracefully.
"""

import time

import pytest


@pytest.mark.integration
def test_worker_restart_recovers(mirror_stack):
    """Restarting worker does not break subsequent syncs.

    After worker restarts, master should reconnect and the next triggered sync
    must complete with ACTIVE status.
    """
    mirror_stack.restart_process("worker")
    mirror_stack.wait_for_worker_ready(timeout=30)

    mirror_stack.trigger_sync("rsync-test")
    mirror_stack.wait_for_status("rsync-test", "ACTIVE", timeout=30)

    assert mirror_stack.package_status("rsync-test") == "ACTIVE", (
        "rsync-test did not reach ACTIVE after worker restart"
    )


@pytest.mark.integration
def test_master_handles_worker_unavailable(mirror_stack):
    """Master tolerates a stopped worker and recovers when worker comes back.

    Stops the worker, triggers a sync (expected to error or stay pending), then
    restarts the worker and confirms a subsequent sync completes.
    """
    mirror_stack.stop_process("worker")

    # Give master a moment to notice the worker is gone.
    time.sleep(3)

    # Attempt to trigger a sync while worker is down; may fail gracefully.
    try:
        mirror_stack.trigger_sync("rsync-test")
    except Exception:
        # Trigger may raise if master cannot reach worker — that is acceptable.
        pass

    mirror_stack.start_process("worker")
    mirror_stack.wait_for_worker_ready(timeout=30)

    # After worker comes back, a fresh sync must succeed.
    mirror_stack.trigger_sync("rsync-test")
    mirror_stack.wait_for_status("rsync-test", "ACTIVE", timeout=30)

    assert mirror_stack.package_status("rsync-test") == "ACTIVE", (
        "rsync-test did not reach ACTIVE after worker was stopped and restarted"
    )
