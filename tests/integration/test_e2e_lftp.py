"""End-to-end lftp sync tests."""

import time

import pytest


@pytest.mark.integration
def test_basic_lftp_sync(mirror_stack):
    """Basic lftp sync completes with ACTIVE status and README in publish dir."""
    lastsync_before = mirror_stack.package_lastsync("lftp-test")

    mirror_stack.trigger_sync("lftp-test")
    _wait_for_new_sync(mirror_stack, "lftp-test", lastsync_before, timeout=120)

    readme = mirror_stack.publish_dir / "lftp-test" / "README"
    assert readme.exists(), f"README not found at {readme}"
    assert readme.read_text().strip() == "mirror.py integration test fixture (lftp) v1", (
        f"README content mismatch: {readme.read_text()!r}"
    )


def _wait_for_new_sync(mirror_stack, pkgid: str, prev_lastsync: float, timeout: int = 60) -> float:
    """Poll package_lastsync until a new sync cycle completes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = mirror_stack.package_lastsync(pkgid)
        if cur > prev_lastsync and mirror_stack.package_status(pkgid) == "ACTIVE":
            return cur
        time.sleep(1)
    raise TimeoutError(f"{pkgid} did not sync again within {timeout}s")
