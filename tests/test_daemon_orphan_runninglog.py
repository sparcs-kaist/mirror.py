"""Tests for the daemon orphan-ERROR transition with runninglog handling.

Verifies that when a package is stuck in SYNC with no worker, the daemon:
- reattaches the FileHandler if logger has no handlers but runninglog is set
- closes the logger
- clears runninglog
- transitions to ERROR
"""
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.sync
from mirror.command.daemon import (
    MISMATCH_GRACE_SECONDS,
    SETUP_GRACE_SECONDS,
    daemon,
    _mismatch_first_seen,
)


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


def _make_orphan_pkg(pkgid: str, stale_ms: float, runninglog: str) -> MagicMock:
    """Build a Package stub stuck in SYNC with a runninglog path set.

    Args:
        pkgid(str): Package identifier.
        stale_ms(float): Package timestamp in milliseconds (old enough to exceed SETUP_GRACE_SECONDS).
        runninglog(str): Path string stored in statusinfo.runninglog.

    Return:
        package(MagicMock): Stub where is_syncing() always returns True.
    """
    pkg = MagicMock()
    pkg.pkgid = pkgid
    pkg.status = "SYNC"
    pkg.timestamp = stale_ms
    pkg.is_disabled.return_value = False
    pkg.is_syncing.return_value = True
    pkg.set_status = MagicMock()
    pkg.statusinfo = MagicMock()
    pkg.statusinfo.runninglog = runninglog
    return pkg


def test_daemon_orphan_error_clears_runninglog_and_reattaches(
    mock_master_server,
    mock_dependencies,
):
    """Orphan SYNC package: reattach logger, close it, clear runninglog, set ERROR.

    Setup: package status=SYNC, statusinfo.runninglog set, timestamp old enough
    that sync_age > SETUP_GRACE_SECONDS. The daemon must reattach the logger
    (no active handlers), close it, clear runninglog, and call set_status("ERROR").
    """
    _mismatch_first_seen.clear()

    pkgid = "orphan-test-pkg"
    runninglog_path = "/tmp/orphan-x.log"
    stale_ms = (time.time() - (SETUP_GRACE_SECONDS + 10)) * 1000
    pkg = _make_orphan_pkg(pkgid, stale_ms, runninglog_path)
    mirror.packages = {pkgid: pkg}

    # pkg_logger returned by mirror.logger.get; simulate no active handlers
    # so the reattach branch fires. reattach_logger adds a handler in practice,
    # so the side_effect simulates that so close_logger's guard passes.
    fake_pkg_logger = MagicMock()
    fake_pkg_logger.handlers = []

    def _fake_reattach(logger, path, pkgid):
        logger.handlers.append(MagicMock())
        return True

    mock_reattach = MagicMock(side_effect=_fake_reattach)
    mock_close = MagicMock()

    base_time = time.time()
    time_values = [
        base_time,                                # iteration 1: note_mismatch
        base_time + MISMATCH_GRACE_SECONDS + 1,   # iteration 2: now (branch check)
        base_time + MISMATCH_GRACE_SECONDS + 1,   # iteration 2: now (sync_age)
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
         patch("mirror.logger.get", return_value=fake_pkg_logger), \
         patch("mirror.logger.exists", return_value=False), \
         patch("mirror.logger.reattach_logger", mock_reattach), \
         patch("mirror.logger.close_logger", mock_close), \
         patch("time.time", side_effect=fake_time), \
         patch("time.sleep", side_effect=fake_sleep), \
         patch("sys.exit"):
        try:
            daemon("config.json")
        except KeyboardInterrupt:
            pass

    _mismatch_first_seen.clear()

    mock_reattach.assert_called_once_with(
        fake_pkg_logger, Path(runninglog_path), pkgid
    )
    mock_close.assert_called_once()
    assert pkg.statusinfo.runninglog is None
    pkg.set_status.assert_called_once_with("ERROR")
