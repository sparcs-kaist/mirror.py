"""State persistence test: lastsync timestamp survives master + worker restart."""

import json
import time

import pytest


@pytest.mark.integration
def test_lastsync_survives_master_worker_restart(mirror_stack):
    """lastsync is preserved in stat.json after restarting both master and worker.

    Steps:
    1. Wait for rsync-test to complete successfully.
    2. Read lastsync from stat.json.
    3. Restart master and worker.
    4. Re-read stat.json and assert lastsync >= captured value (not zeroed).
    """
    mirror_stack.wait_for_status("rsync-test", "ACTIVE", timeout=30)

    lastsync_before = mirror_stack.package_lastsync("rsync-test")
    assert lastsync_before > 0, (
        f"Expected lastsync > 0 after ACTIVE sync, got {lastsync_before}"
    )

    mirror_stack.restart_process("worker")
    mirror_stack.restart_process("master")
    mirror_stack.wait_for_master_ready(timeout=30)

    # Give master a moment to load stat.json.
    time.sleep(2)

    lastsync_after = mirror_stack.package_lastsync("rsync-test")
    assert lastsync_after >= lastsync_before, (
        f"lastsync regressed after restart: before={lastsync_before}, after={lastsync_after}. "
        "Stat file was not persisted correctly."
    )
