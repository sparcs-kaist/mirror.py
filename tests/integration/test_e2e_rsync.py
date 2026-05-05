"""End-to-end rsync sync tests, including FFTS short-circuit and FFTS-changed scenarios."""

import time
from pathlib import Path

import pytest


@pytest.mark.integration
def test_basic_rsync_sync(mirror_stack):
    """Basic rsync sync completes with ACTIVE status and README in publish dir."""
    mirror_stack.wait_for_status("rsync-test", "ACTIVE", timeout=30)

    readme = mirror_stack.publish_dir / "rsync-test" / "README"
    assert readme.exists(), f"README not found at {readme}"
    assert readme.read_text().strip() == "mirror.py integration test fixture (rsync) v1", (
        f"README content mismatch: {readme.read_text()!r}"
    )


@pytest.mark.integration
def test_ffts_short_circuit_when_unchanged(mirror_stack):
    """Second sync cycle skips bulk transfer when FFTS shows no changes.

    After first sync, the FFTS file is identical on both sides. The next sync
    triggered by the PT5S syncrate should log 'FFTS check: Up to date.' and
    leave the README mtime unchanged.
    """
    mirror_stack.wait_for_status("rsync-test", "ACTIVE", timeout=30)

    readme = mirror_stack.publish_dir / "rsync-test" / "README"
    assert readme.exists(), f"README not found before second cycle at {readme}"
    mtime_before = readme.stat().st_mtime

    lastsync_before = mirror_stack.package_lastsync("rsync-test")

    # Wait for a new sync cycle to complete (PT5S syncrate — poll up to 30s).
    _wait_for_new_sync(mirror_stack, "rsync-test", lastsync_before, timeout=30)

    mtime_after = readme.stat().st_mtime
    assert mtime_after == mtime_before, (
        f"README mtime changed from {mtime_before} to {mtime_after}; "
        "FFTS short-circuit did not fire — unexpected full sync occurred."
    )

    logs = mirror_stack.read_package_log_dir("rsync-test")
    assert logs, "No package log files found for rsync-test"
    latest_log = sorted(logs)[-1]
    log_text = latest_log.read_text(errors="replace") if latest_log.suffix != ".gz" else _read_gz(latest_log)
    assert "FFTS check: Up to date." in log_text, (
        f"Expected 'FFTS check: Up to date.' in latest log {latest_log}, got:\n{log_text[-2000:]}"
    )


@pytest.mark.integration
def test_ffts_changed_triggers_full_sync(mirror_stack):
    """Swapping fixture tree causes FFTS to detect change and run full sync.

    After swap, the next PT5S cycle should log 'FFTS check: Update needed.'
    and write tree_v2's NEW_FILE to the publish dir.
    """
    mirror_stack.wait_for_status("rsync-test", "ACTIVE", timeout=30)

    # The v1 baseline lives in the rsync-fixture build context itself; tree_v2
    # is the changed-state overlay used to provoke FFTS to re-sync.
    v1_dir = Path("tests/integration/docker/rsync-fixture/data")
    v2_dir = Path("tests/integration/fixtures/tree_v2")

    lastsync_before = mirror_stack.package_lastsync("rsync-test")

    mirror_stack.swap_rsync_fixture_tree(v2_dir)

    try:
        # Wait for a NEW sync cycle — not just any ACTIVE status.
        _wait_for_new_sync(mirror_stack, "rsync-test", lastsync_before, timeout=30)

        new_file = mirror_stack.publish_dir / "rsync-test" / "NEW_FILE"
        assert new_file.exists(), f"NEW_FILE not synced to {new_file} after tree swap"
        assert new_file.read_text().strip() == "appeared in v2", (
            f"NEW_FILE content mismatch: {new_file.read_text()!r}"
        )

        logs = mirror_stack.read_package_log_dir("rsync-test")
        assert logs, "No package log files found for rsync-test"
        latest_log = sorted(logs)[-1]
        log_text = latest_log.read_text(errors="replace") if latest_log.suffix != ".gz" else _read_gz(latest_log)
        assert "FFTS check: Update needed." in log_text, (
            f"Expected 'FFTS check: Update needed.' in latest log {latest_log}, got:\n{log_text[-2000:]}"
        )
    finally:
        mirror_stack.swap_rsync_fixture_tree(v1_dir)


def _wait_for_new_sync(mirror_stack, pkgid: str, prev_lastsync: float, timeout: int = 30) -> float:
    """Poll package_lastsync until a new sync cycle completes.

    Args:
        mirror_stack: MirrorStack instance.
        pkgid(str): Package identifier.
        prev_lastsync(float): The lastsync timestamp before the new cycle.
        timeout(int): Maximum seconds to wait.

    Return:
        new_lastsync(float): The updated lastsync timestamp.

    Raises:
        TimeoutError: If lastsync does not advance within timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = mirror_stack.package_lastsync(pkgid)
        if cur > prev_lastsync:
            return cur
        time.sleep(1)
    raise TimeoutError(f"{pkgid} did not sync again within {timeout}s")


def _read_gz(path: Path) -> str:
    """Read a gzip-compressed log file as text."""
    import gzip
    with gzip.open(path, "rt", errors="replace") as fh:
        return fh.read()
