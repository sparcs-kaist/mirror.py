"""State persistence test: lastsync timestamp survives master + worker restart."""

import time

import pytest

from .helpers import temporary_rsync_package


@pytest.mark.integration
def test_lastsync_survives_master_worker_restart(mirror_stack):
    """lastsync is preserved in stat.json after restarting both master and worker.

    A PT0S package cannot auto-sync after restart, so exact timestamp equality
    proves restoration rather than allowing a replacement sync to hide loss.
    """
    with temporary_rsync_package(
        mirror_stack, "state-persistence-rsync"
    ) as pkgid:
        mirror_stack.trigger_sync(pkgid)
        lastsync_before = mirror_stack.wait_for_new_active_sync(
            pkgid, previous_lastsync=0.0, timeout=30
        )

        stat_before = mirror_stack.stat_json()["packages"][pkgid]
        assert stat_before["lastsync"] == lastsync_before

        # Stop in dependency order, then start worker and wait for its socket
        # before bringing master back up.
        mirror_stack.stop_process("master")
        mirror_stack.stop_process("worker")
        mirror_stack.start_process("worker")
        mirror_stack.wait_for_worker_ready(timeout=30)
        mirror_stack.start_process("master")
        mirror_stack.wait_for_master_ready(timeout=30)

        deadline = time.monotonic() + 10
        while True:
            try:
                runtime_package = mirror_stack.runtime_package(pkgid)
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)

        observation_deadline = time.monotonic() + 3
        while time.monotonic() < observation_deadline:
            stat_package = mirror_stack.stat_json()["packages"][pkgid]
            runtime_package = mirror_stack.runtime_package(pkgid)
            assert stat_package["status"]["status"] == "ACTIVE"
            assert runtime_package["status"]["status"] == "ACTIVE"
            assert stat_package["lastsync"] == lastsync_before
            assert runtime_package["lastsync"] == lastsync_before
            assert mirror_stack.worker_progress(pkgid)["syncing"] is False
            time.sleep(0.2)
