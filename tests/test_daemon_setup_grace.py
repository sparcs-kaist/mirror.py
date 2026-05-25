"""Tests for the daemon loop SETUP_GRACE_SECONDS logic.

Verifies that a package stuck in SYNC status without a corresponding worker
job is not immediately transitioned to ERROR. Instead, a grace window of
SETUP_GRACE_SECONDS is observed before the ERROR transition fires.
"""
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.sync
from mirror.command.daemon import MISMATCH_GRACE_SECONDS, SETUP_GRACE_SECONDS, daemon, _mismatch_first_seen


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_master_server():
    with patch("mirror.socket.master.MasterServer") as mock:
        yield mock


@pytest.fixture
def mock_dependencies():
    with patch("mirror.config.load"), \
         patch("mirror.logger.setup_logger"), \
         patch("mirror.sync.start"):

        original_packages = getattr(mirror, "packages", None)
        original_log = getattr(mirror, "log", None)

        mirror.packages = {}
        mirror.log = MagicMock()

        yield

        if original_packages is not None:
            mirror.packages = original_packages
        if original_log is not None:
            mirror.log = original_log


def _make_syncing_pkg(pkgid: str, timestamp_ms: float) -> MagicMock:
    """Build a Package stub in SYNC status with a given timestamp.

    Args:
        pkgid(str): Package identifier.
        timestamp_ms(float): Value of package.timestamp (milliseconds since epoch).

    Return:
        package(MagicMock): Stub where is_syncing() returns True and
            set_status is a MagicMock recording calls.
    """
    pkg = MagicMock()
    pkg.pkgid = pkgid
    pkg.status = "SYNC"
    pkg.timestamp = timestamp_ms
    pkg.is_disabled.return_value = False
    pkg.is_syncing.return_value = True
    pkg.set_status = MagicMock()
    return pkg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_grace_period_prevents_error_transition_when_sync_is_recent(
    mock_master_server,
    mock_dependencies,
):
    """A package in SYNC status with timestamp < SETUP_GRACE_SECONDS ago must not become ERROR.

    When the worker reports no active job but the sync was started fewer than
    SETUP_GRACE_SECONDS ago, the daemon loop must skip the ERROR transition.
    """
    pkgid = "grace-pkg-recent"
    # Timestamp set to now (sync just started).
    pkg = _make_syncing_pkg(pkgid, timestamp_ms=time.time() * 1000)
    mirror.packages = {pkgid: pkg}

    with patch("mirror.socket.worker.is_worker_running", return_value=False), \
         patch("mirror.logger.get", return_value=None), \
         patch("time.sleep", side_effect=KeyboardInterrupt), \
         patch("sys.exit"):
        try:
            daemon("config.json")
        except KeyboardInterrupt:
            pass

    pkg.set_status.assert_not_called()


def test_grace_period_triggers_error_transition_when_sync_is_stale(
    mock_master_server,
    mock_dependencies,
):
    """A package in SYNC status with timestamp > SETUP_GRACE_SECONDS ago must become ERROR.

    When the worker reports no active job and the sync was started more than
    SETUP_GRACE_SECONDS ago, the daemon loop must transition the package to ERROR.

    Two iterations are needed: the first records the mismatch, the second (after
    MISMATCH_GRACE_SECONDS have elapsed) escalates to ERROR.
    """
    pkgid = "grace-pkg-stale"
    _mismatch_first_seen.clear()
    # Timestamp set to 70 seconds ago (past the grace window).
    stale_timestamp_ms = (time.time() - (SETUP_GRACE_SECONDS + 10)) * 1000
    pkg = _make_syncing_pkg(pkgid, timestamp_ms=stale_timestamp_ms)
    mirror.packages = {pkgid: pkg}

    # Iteration counter: first call to time.sleep lets the loop run a second
    # iteration; second call raises KeyboardInterrupt to stop the daemon.
    # We also need time.time() to advance by > MISMATCH_GRACE_SECONDS between
    # the two iterations so the mismatch is considered persistent.
    iteration = [0]
    base_time = time.time()
    time_values = [
        base_time,                              # first iteration: note_mismatch records now
        base_time + MISMATCH_GRACE_SECONDS + 1, # second iteration: now - first_seen > grace
        base_time + MISMATCH_GRACE_SECONDS + 1, # second call inside the lock block (sync_age)
    ]
    time_index = [0]

    def fake_time():
        val = time_values[min(time_index[0], len(time_values) - 1)]
        time_index[0] += 1
        return val

    sleep_calls = [0]

    def fake_sleep(_):
        sleep_calls[0] += 1
        if sleep_calls[0] >= 2:
            raise KeyboardInterrupt

    with patch("mirror.socket.worker.is_worker_running", return_value=False), \
         patch("mirror.logger.get", return_value=None), \
         patch("time.sleep", side_effect=fake_sleep), \
         patch("time.time", side_effect=fake_time), \
         patch("sys.exit"):
        try:
            daemon("config.json")
        except KeyboardInterrupt:
            pass

    _mismatch_first_seen.clear()
    pkg.set_status.assert_called_once_with("ERROR")
