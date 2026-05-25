"""Tests for the daemon loop mismatch-grace logic (Fix C).

Verifies that transient is_syncing/is_worker_running mismatches are not
immediately escalated to ERROR or SYNC, and that persistent mismatches are
escalated only after MISMATCH_GRACE_SECONDS, with an under-lock re-check.
"""
import time
from unittest.mock import MagicMock, patch, call

import pytest

import mirror
import mirror.sync
from mirror.command.daemon import (
    MISMATCH_GRACE_SECONDS,
    SETUP_GRACE_SECONDS,
    daemon,
    _mismatch_first_seen,
)


# ---------------------------------------------------------------------------
# Fixtures (mirror patterns from test_daemon_setup_grace.py)
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
    """Build a Package stub in SYNC status.

    Args:
        pkgid(str): Package identifier.
        timestamp_ms(float): Value of package.timestamp (milliseconds since epoch).

    Return:
        package(MagicMock): Stub where is_syncing() returns True.
    """
    pkg = MagicMock()
    pkg.pkgid = pkgid
    pkg.status = "SYNC"
    pkg.timestamp = timestamp_ms
    pkg.is_disabled.return_value = False
    pkg.is_syncing.return_value = True
    pkg.set_status = MagicMock()
    return pkg


def _make_active_pkg(pkgid: str) -> MagicMock:
    """Build a Package stub in ACTIVE status (not syncing).

    Args:
        pkgid(str): Package identifier.

    Return:
        package(MagicMock): Stub where is_syncing() returns False.
    """
    pkg = MagicMock()
    pkg.pkgid = pkgid
    pkg.status = "ACTIVE"
    pkg.timestamp = time.time() * 1000
    pkg.is_disabled.return_value = False
    pkg.is_syncing.return_value = False
    pkg.set_status = MagicMock()
    return pkg


def _run_daemon_n_iterations(n: int, extra_patches: list) -> None:
    """Run the daemon loop for exactly n iterations, then stop.

    Args:
        n(int): Number of loop iterations (time.sleep calls) before stopping.
        extra_patches(list): List of active patch context managers already applied.
    """
    sleep_calls = [0]

    def fake_sleep(_):
        sleep_calls[0] += 1
        if sleep_calls[0] >= n:
            raise KeyboardInterrupt

    with patch("time.sleep", side_effect=fake_sleep), \
         patch("sys.exit"):
        try:
            daemon("config.json")
        except KeyboardInterrupt:
            pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_first_branch_grace_skips_transient_mismatch(
    mock_master_server,
    mock_dependencies,
):
    """status=SYNC, worker has no job, only one iteration → set_status NOT called.

    The mismatch is observed for the first time (first_seen == now, delta == 0),
    which is less than MISMATCH_GRACE_SECONDS, so the daemon skips the ERROR
    transition.
    """
    _mismatch_first_seen.clear()
    pkgid = "mismatch-transient-first"
    stale_timestamp_ms = (time.time() - (SETUP_GRACE_SECONDS + 10)) * 1000
    pkg = _make_syncing_pkg(pkgid, timestamp_ms=stale_timestamp_ms)
    mirror.packages = {pkgid: pkg}

    with patch("mirror.socket.worker.is_worker_running", return_value=False), \
         patch("mirror.logger.get", return_value=None):
        _run_daemon_n_iterations(1, [])

    _mismatch_first_seen.clear()
    pkg.set_status.assert_not_called()


def test_first_branch_error_after_persistent_mismatch(
    mock_master_server,
    mock_dependencies,
):
    """status=SYNC, worker absent, mismatch persists > MISMATCH_GRACE_SECONDS → ERROR.

    Two iterations: the first records first_seen, the second fires after the
    grace window and transitions the package to ERROR.
    """
    _mismatch_first_seen.clear()
    pkgid = "mismatch-persistent-first"
    stale_timestamp_ms = (time.time() - (SETUP_GRACE_SECONDS + 10)) * 1000
    pkg = _make_syncing_pkg(pkgid, timestamp_ms=stale_timestamp_ms)
    mirror.packages = {pkgid: pkg}

    base_time = time.time()
    time_values = [
        base_time,                               # iteration 1: note_mismatch
        base_time + MISMATCH_GRACE_SECONDS + 1,  # iteration 2: now (branch check)
        base_time + MISMATCH_GRACE_SECONDS + 1,  # iteration 2: now (sync_age)
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
         patch("time.time", side_effect=fake_time), \
         patch("time.sleep", side_effect=fake_sleep), \
         patch("sys.exit"):
        try:
            daemon("config.json")
        except KeyboardInterrupt:
            pass

    _mismatch_first_seen.clear()
    pkg.set_status.assert_called_once_with("ERROR")


def test_first_branch_skip_when_status_flipped_under_lock(
    mock_master_server,
    mock_dependencies,
):
    """Persistent mismatch, but is_syncing() returns False under the lock → set_status NOT called.

    Simulates the race where on_sync_done flips status to ACTIVE between the
    branch-entry check and the under-lock re-check.
    """
    _mismatch_first_seen.clear()
    pkgid = "mismatch-lock-flip-first"
    stale_timestamp_ms = (time.time() - (SETUP_GRACE_SECONDS + 10)) * 1000

    # is_syncing() call sites within the first branch path:
    #   iter 1: branch-entry (line 214) -> True; grace not exceeded -> continue
    #   iter 2: branch-entry (line 214) -> True; grace exceeded;
    #           under-lock recheck (line 232) -> False -> skip set_status
    pkg = MagicMock()
    pkg.pkgid = pkgid
    pkg.status = "SYNC"
    pkg.timestamp = stale_timestamp_ms
    pkg.is_disabled.return_value = False
    pkg.is_syncing.side_effect = [True, True, False]
    pkg.set_status = MagicMock()
    mirror.packages = {pkgid: pkg}

    base_time = time.time()
    time_values = [
        base_time,                               # iteration 1: note_mismatch
        base_time + MISMATCH_GRACE_SECONDS + 1,  # iteration 2: now (branch check)
        base_time + MISMATCH_GRACE_SECONDS + 1,  # iteration 2: now (sync_age)
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
         patch("time.time", side_effect=fake_time), \
         patch("time.sleep", side_effect=fake_sleep), \
         patch("sys.exit"):
        try:
            daemon("config.json")
        except KeyboardInterrupt:
            pass

    _mismatch_first_seen.clear()
    pkg.set_status.assert_not_called()


def test_elif_branch_grace_skips_transient(
    mock_master_server,
    mock_dependencies,
):
    """status=ACTIVE, worker has a job, one iteration → set_status NOT called.

    The mismatch is first observed, delta == 0 < MISMATCH_GRACE_SECONDS, so
    the daemon defers the SYNC transition.
    """
    _mismatch_first_seen.clear()
    pkgid = "mismatch-elif-transient"
    pkg = _make_active_pkg(pkgid)
    mirror.packages = {pkgid: pkg}

    with patch("mirror.socket.worker.is_worker_running", return_value=True), \
         patch("mirror.logger.get", return_value=None):
        _run_daemon_n_iterations(1, [])

    _mismatch_first_seen.clear()
    pkg.set_status.assert_not_called()


def test_elif_branch_sets_sync_after_persistence(
    mock_master_server,
    mock_dependencies,
):
    """status=ACTIVE, worker has a job, mismatch persists > MISMATCH_GRACE_SECONDS → SYNC.

    Two iterations: the first records first_seen, the second fires after the
    grace window and transitions the package to SYNC.
    """
    _mismatch_first_seen.clear()
    pkgid = "mismatch-elif-persistent"
    pkg = _make_active_pkg(pkgid)
    mirror.packages = {pkgid: pkg}

    base_time = time.time()
    time_values = [
        base_time,                               # iteration 1: note_mismatch
        base_time + MISMATCH_GRACE_SECONDS + 1,  # iteration 2: now (branch check)
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

    with patch("mirror.socket.worker.is_worker_running", return_value=True), \
         patch("mirror.logger.get", return_value=None), \
         patch("time.time", side_effect=fake_time), \
         patch("time.sleep", side_effect=fake_sleep), \
         patch("sys.exit"):
        try:
            daemon("config.json")
        except KeyboardInterrupt:
            pass

    _mismatch_first_seen.clear()
    pkg.set_status.assert_called_once_with("SYNC")
