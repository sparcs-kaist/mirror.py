"""Master-process restart tests.

Verifies that restarting the master daemon does not kill the worker subprocess
and that master reconnects to worker and resumes normal operation afterward.
"""


import pytest

from .helpers import (
    release_slow_rsync_gate,
    temporary_rsync_package,
    wait_for_slow_rsync_gate,
)


@pytest.mark.integration
def test_master_restart_during_sync_does_not_kill_worker_subprocess(mirror_stack):
    """Restarting master mid-sync leaves worker process and subprocess alive.

    Uses the fixture's bounded pre-transfer gate to hold a real rsync child,
    restarts master, and verifies that exact child PID remains alive.
    """
    with temporary_rsync_package(
        mirror_stack, "master-restart-rsync", slow=True
    ) as pkgid:
        lastsync_before = mirror_stack.package_lastsync(pkgid)
        mirror_stack.trigger_sync(pkgid)
        wait_for_slow_rsync_gate()

        progress_before = mirror_stack.worker_progress(pkgid)
        assert progress_before["syncing"] is True
        job_pid = progress_before["info"]["pid"]
        assert isinstance(job_pid, int) and job_pid > 0
        assert mirror_stack.docker_exec(
            "kill", "-0", str(job_pid), check=False
        ).returncode == 0

        mirror_stack.restart_process("master")
        mirror_stack.wait_for_master_ready(timeout=30)

        progress_after = mirror_stack.worker_progress(pkgid)
        assert progress_after["syncing"] is True
        assert progress_after["info"]["pid"] == job_pid
        assert mirror_stack.docker_exec(
            "kill", "-0", str(job_pid), check=False
        ).returncode == 0

        release_slow_rsync_gate()
        mirror_stack.wait_for_new_active_sync(pkgid, lastsync_before, timeout=30)


@pytest.mark.integration
def test_master_reconnects_to_worker_after_restart(mirror_stack):
    """Master reconnects to worker after restart and subsequent sync completes."""
    with temporary_rsync_package(
        mirror_stack, "master-reconnect-rsync"
    ) as pkgid:
        mirror_stack.restart_process("master")
        mirror_stack.wait_for_master_ready(timeout=30)

        lastsync_before = mirror_stack.package_lastsync(pkgid)
        mirror_stack.trigger_sync(pkgid)
        lastsync_after = mirror_stack.wait_for_new_active_sync(
            pkgid, lastsync_before, timeout=30
        )

        assert lastsync_after > lastsync_before
