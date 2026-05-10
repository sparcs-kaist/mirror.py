"""Tests for the resilient mirror.sync.on_sync_done path when pkgid is unknown."""
import time
import logging
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.config
import mirror.logger
import mirror.structure
import mirror.sync
from mirror.sync import on_sync_done, _in_progress, _extra_args, _watchdog_fired, _start_lock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_mirror_log(monkeypatch):
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)


@pytest.fixture(autouse=True)
def _clear_sync_state():
    """Clean _in_progress / _extra_args / _watchdog_fired before and after each test."""
    with _start_lock:
        _in_progress.clear()
        _extra_args.clear()
        _watchdog_fired.clear()
    yield
    with _start_lock:
        _in_progress.clear()
        _extra_args.clear()
        _watchdog_fired.clear()


@pytest.fixture()
def empty_packages(monkeypatch):
    """Provide an empty Packages collection so 'ghost' is not found."""
    fake_pkgs = MagicMock()
    fake_pkgs.get = MagicMock(return_value=None)
    monkeypatch.setattr(mirror, "packages", fake_pkgs, raising=False)
    return fake_pkgs


# ---------------------------------------------------------------------------
# Test 1: unknown pkgid does not raise
# ---------------------------------------------------------------------------

def test_on_sync_done_with_unknown_pkgid_does_not_raise(empty_packages, monkeypatch):
    """on_sync_done with a pkgid absent from mirror.packages must not raise."""
    monkeypatch.setattr(mirror.logger, "get", lambda name: None)

    # Must not raise.
    on_sync_done("nonexistent", success=True, returncode=0)


# ---------------------------------------------------------------------------
# Test 2: unknown pkgid clears _in_progress / _extra_args / _watchdog_fired
# ---------------------------------------------------------------------------

def test_on_sync_done_unknown_pkgid_clears_in_progress(empty_packages, monkeypatch):
    """on_sync_done for an unknown pkg clears all three registries."""
    monkeypatch.setattr(mirror.logger, "get", lambda name: None)

    with _start_lock:
        _in_progress.add("ghost")
        _extra_args["ghost"] = {"FOO": "BAR"}
        _watchdog_fired.add("ghost")

    on_sync_done("ghost", success=True, returncode=0)

    with _start_lock:
        assert "ghost" not in _in_progress
        assert "ghost" not in _extra_args
        assert "ghost" not in _watchdog_fired


# ---------------------------------------------------------------------------
# Test 3: unknown pkgid closes the logger (no handlers afterwards)
# ---------------------------------------------------------------------------

def test_on_sync_done_unknown_pkgid_closes_logger(empty_packages, tmp_path, monkeypatch):
    """on_sync_done for unknown pkg closes the associated per-package logger."""
    # We need mirror.conf to be populated for create_logger.
    logger_cfg = {
        "packageformat": "[%(asctime)s][{package}] %(levelname)s # %(message)s",
        "packagelevel": "ERROR",
        "packagefileformat": {
            "base": str(tmp_path / "pkglogs"),
            "folder": "{year}/{month}/{day}",
            "filename": "{packageid}.{hour}.{minute}.{second}.log",
            "gzip": False,
        },
    }
    fake_conf = MagicMock()
    fake_conf.logger = logger_cfg
    fake_conf.logfolder = tmp_path / "logs"
    monkeypatch.setattr(mirror, "conf", fake_conf, raising=False)

    (tmp_path / "pkglogs").mkdir(parents=True, exist_ok=True)

    pkg_logger = mirror.logger.create_logger("ghost", time.time())
    assert len(pkg_logger.handlers) > 0, "logger must have handlers after creation"

    monkeypatch.setattr(mirror.logger, "get", lambda name: pkg_logger)

    on_sync_done("ghost", success=False, returncode=1)

    assert len(pkg_logger.handlers) == 0


# ---------------------------------------------------------------------------
# Test 4: unknown pkgid does NOT call save_stat_data
# ---------------------------------------------------------------------------

def test_on_sync_done_unknown_pkgid_does_not_call_save_stat_data(empty_packages, monkeypatch):
    """on_sync_done for unknown pkg must not call save_stat_data (no resurrection)."""
    monkeypatch.setattr(mirror.logger, "get", lambda name: None)

    save_calls: list[str] = []

    def _fake_save():
        save_calls.append("called")

    monkeypatch.setattr(mirror.config, "save_stat_data", _fake_save)

    on_sync_done("ghost", success=True, returncode=0)

    assert save_calls == [], "save_stat_data must not be called for an unknown pkgid"
