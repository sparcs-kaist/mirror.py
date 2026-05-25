"""Tests for runninglog population and cleanup in mirror.sync.start()."""
import logging
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.config
import mirror.plugin
import mirror.sync as sync_mod
from mirror.sync import _start_lock, start


@pytest.fixture(autouse=True)
def _clear_sync_state():
    with _start_lock:
        sync_mod._extra_args.clear()
        sync_mod._watchdog_fired.clear()
    yield
    with _start_lock:
        sync_mod._extra_args.clear()
        sync_mod._watchdog_fired.clear()


@pytest.fixture(autouse=True)
def _mock_mirror_log(monkeypatch):
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)


def _make_pkg(pkgid: str = "test-pkg"):
    pkg = MagicMock()
    pkg.pkgid = pkgid
    pkg.name = f"Pkg {pkgid}"
    pkg.synctype = "rsync"
    pkg.status = "UNKNOWN"
    pkg.statusinfo = MagicMock()
    pkg.statusinfo.runninglog = None

    def _set_status(status, logfile=None):
        pkg.status = status

    pkg.set_status = MagicMock(side_effect=_set_status)
    pkg.is_syncing.side_effect = lambda: pkg.status == "SYNC"
    return pkg


def _make_pkg_logger(log_path: str) -> logging.Logger:
    """Build a minimal mock Logger with a FileHandler pointing to log_path."""
    pkg_logger = MagicMock(spec=logging.Logger)
    handler = MagicMock(spec=logging.FileHandler)
    handler.baseFilename = log_path
    pkg_logger.handlers = [handler]
    return pkg_logger


def test_start_sets_runninglog_to_log_file_path():
    """start() must set package.statusinfo.runninglog to the log file path."""
    pkg = _make_pkg("rl-test-1")
    log_path = "/tmp/test-pkg.log"
    pkg_logger = _make_pkg_logger(log_path)

    fake_record = MagicMock()
    fake_record.execute = MagicMock(return_value=None)
    fake_record.on_sync_done = None

    with patch.dict("mirror.plugin._registry", {"rsync": fake_record}, clear=False), \
         patch("mirror.logger.create_logger", return_value=pkg_logger), \
         patch("mirror.logger.get_log_path", return_value=log_path), \
         patch("mirror.config.save_stat_data") as mock_save, \
         patch.object(mirror, "sync", sync_mod):
        start(pkg)

    assert pkg.statusinfo.runninglog == log_path
    mock_save.assert_called()


def test_start_failure_clears_runninglog():
    """start() failure path must clear runninglog and set status to ERROR."""
    pkg = _make_pkg("rl-test-2")
    pkg.statusinfo.runninglog = "/tmp/leftover"

    fake_record = MagicMock()
    fake_record.execute = MagicMock(return_value=None)
    fake_record.on_sync_done = None

    with patch.dict("mirror.plugin._registry", {"rsync": fake_record}, clear=False), \
         patch("mirror.logger.create_logger", return_value=MagicMock(handlers=[])), \
         patch("mirror.logger.get_log_path", return_value=None), \
         patch("mirror.logger.get", return_value=MagicMock(handlers=[])), \
         patch("mirror.logger.close_logger", return_value=None), \
         patch("mirror.config.save_stat_data"), \
         patch.object(mirror, "sync", sync_mod):
        with patch.object(mirror.plugin, "get_record", return_value=None):
            with pytest.raises(RuntimeError):
                start(pkg)

    assert pkg.statusinfo.runninglog is None
    assert pkg.status == "ERROR"
