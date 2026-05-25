"""Tests for runninglog clearing and reattach logic in mirror.sync.on_sync_done()."""
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.config
import mirror.logger
import mirror.sync as sync_mod
from mirror.sync import _start_lock, on_sync_done


@pytest.fixture(autouse=True)
def _mock_mirror_log(monkeypatch):
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)


@pytest.fixture(autouse=True)
def _clear_sync_state():
    with _start_lock:
        sync_mod._extra_args.clear()
        sync_mod._watchdog_fired.clear()
    yield
    with _start_lock:
        sync_mod._extra_args.clear()
        sync_mod._watchdog_fired.clear()


def _make_pkg(pkgid: str = "pkgid"):
    pkg = MagicMock()
    pkg.pkgid = pkgid
    pkg.name = f"Pkg {pkgid}"
    pkg.synctype = "rsync"
    pkg.status = "SYNC"
    pkg.statusinfo = MagicMock()
    pkg.statusinfo.runninglog = None

    def _set_status(status, logfile=None):
        pkg.status = status

    pkg.set_status = MagicMock(side_effect=_set_status)
    return pkg


def _make_pkg_logger_with_handler() -> logging.Logger:
    """Return a mock logger that has a FileHandler (exists() returns True)."""
    pkg_logger = MagicMock(spec=logging.Logger)
    handler = MagicMock(spec=logging.FileHandler)
    pkg_logger.handlers = [handler]
    return pkg_logger


def _make_pkg_logger_no_handler() -> logging.Logger:
    """Return a mock logger with no handlers (exists() returns False)."""
    pkg_logger = MagicMock(spec=logging.Logger)
    pkg_logger.handlers = []
    return pkg_logger


def test_on_sync_done_clears_runninglog():
    """on_sync_done must set package.statusinfo.runninglog to None on completion."""
    pkg = _make_pkg("pkgid")
    pkg.statusinfo.runninglog = "/tmp/x.log"
    pkg_logger = _make_pkg_logger_with_handler()

    fake_packages = MagicMock()
    fake_packages.get = MagicMock(return_value=pkg)

    with patch.object(mirror, "packages", fake_packages, create=True), \
         patch("mirror.logger.get", return_value=pkg_logger), \
         patch("mirror.logger.exists", return_value=True), \
         patch("mirror.logger.close_logger", return_value="/tmp/x.log"), \
         patch.object(mirror, "sync", sync_mod):
        on_sync_done("pkgid", True, 0)

    assert pkg.statusinfo.runninglog is None


def test_on_sync_done_reattaches_when_no_handlers():
    """on_sync_done must call reattach_logger when the logger has no handlers."""
    pkg = _make_pkg("pkgid")
    pkg.statusinfo.runninglog = "/tmp/x.log"
    pkg_logger = _make_pkg_logger_no_handler()

    fake_packages = MagicMock()
    fake_packages.get = MagicMock(return_value=pkg)

    with patch.object(mirror, "packages", fake_packages, create=True), \
         patch("mirror.logger.get", return_value=pkg_logger), \
         patch("mirror.logger.exists", return_value=False), \
         patch("mirror.logger.reattach_logger") as mock_reattach, \
         patch("mirror.logger.close_logger", return_value="/tmp/x.log"), \
         patch.object(mirror, "sync", sync_mod):
        on_sync_done("pkgid", True, 0)

    mock_reattach.assert_called_once_with(
        pkg_logger, Path("/tmp/x.log"), "pkgid"
    )


def test_on_sync_done_skips_reattach_when_handlers_present():
    """on_sync_done must NOT call reattach_logger when the logger already has handlers."""
    pkg = _make_pkg("pkgid")
    pkg.statusinfo.runninglog = "/tmp/x.log"
    pkg_logger = _make_pkg_logger_with_handler()

    fake_packages = MagicMock()
    fake_packages.get = MagicMock(return_value=pkg)

    with patch.object(mirror, "packages", fake_packages, create=True), \
         patch("mirror.logger.get", return_value=pkg_logger), \
         patch("mirror.logger.exists", return_value=True), \
         patch("mirror.logger.reattach_logger") as mock_reattach, \
         patch("mirror.logger.close_logger", return_value="/tmp/x.log"), \
         patch.object(mirror, "sync", sync_mod):
        on_sync_done("pkgid", True, 0)

    mock_reattach.assert_not_called()
