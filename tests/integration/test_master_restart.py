"""Master-process restart tests.

Verifies that restarting the master daemon does not kill the worker subprocess
and that master reconnects to worker and resumes normal operation afterward.
"""


import pytest


@pytest.mark.integration
def test_master_restart_during_sync_does_not_kill_worker_subprocess(mirror_stack):
    """Restarting master mid-sync leaves worker process and subprocess alive.

    Inserts a 200MB sparse file into the rsync fixture to create a longer-running
    sync, triggers the sync, observes SYNC status, then restarts master. Asserts:
    - The sync was actually in-progress (SYNC status observed before restart).
    - Worker PID is unchanged after master restart.
    - The sync eventually completes with ACTIVE status.
    """
    # Write a 200MB sparse file into the rsync-fixture container to slow down sync.
    mirror_stack.write_file_in_fixture(
        "rsync-fixture",
        "/srv/data/big.bin",
        b"\x00" * 200_000_000,
    )

    mirror_stack.trigger_sync("rsync-test")

    # Poll until we observe SYNC status — critical gate to ensure mid-flight restart.
    mirror_stack.wait_for_status("rsync-test", "SYNC", timeout=30)
    observed_sync = mirror_stack.package_status("rsync-test") == "SYNC"
    assert observed_sync, (
        "rsync-test never reached SYNC status; sync finished too fast to test mid-flight restart. "
        "The large file may not be large enough or the rsync connection is too slow to start."
    )

    worker_pid_before = mirror_stack.process_pid("worker")
    assert worker_pid_before is not None, "Could not determine worker PID before master restart"

    mirror_stack.restart_process("master")
    mirror_stack.wait_for_master_ready(timeout=30)

    worker_pid_after = mirror_stack.process_pid("worker")
    assert worker_pid_after == worker_pid_before, (
        f"Worker PID changed after master restart: {worker_pid_before} -> {worker_pid_after}; "
        "master restart killed the worker process."
    )

    mirror_stack.wait_for_status("rsync-test", "ACTIVE", timeout=90)
    assert mirror_stack.package_status("rsync-test") == "ACTIVE", (
        "rsync-test did not reach ACTIVE after master restart during sync"
    )


@pytest.mark.integration
def test_master_reconnects_to_worker_after_restart(mirror_stack):
    """Master reconnects to worker after restart and subsequent sync completes."""
    mirror_stack.restart_process("master")
    mirror_stack.wait_for_master_ready(timeout=30)

    mirror_stack.trigger_sync("rsync-test")
    mirror_stack.wait_for_status("rsync-test", "ACTIVE", timeout=30)

    assert mirror_stack.package_status("rsync-test") == "ACTIVE", (
        "rsync-test did not reach ACTIVE after master restart and re-trigger"
    )
