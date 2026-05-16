import pytest
from unittest.mock import MagicMock

from mirror.command.daemon import should_auto_sync


def make_package(syncrate: int, lastsync: float, status: str) -> MagicMock:
    """Build a minimal Package stub for should_auto_sync tests.

    Args:
        syncrate(int): Sync interval in seconds; negative means PUSH mode.
        lastsync(float): Epoch seconds of the last sync.
        status(str): Current package status string.

    Return:
        package(MagicMock): Stub exposing syncrate, lastsync, and status.
    """
    pkg = MagicMock()
    pkg.syncrate = syncrate
    pkg.lastsync = lastsync
    pkg.status = status
    return pkg


def test_push_mode_active_returns_false():
    """PUSH package (syncrate=-1) with ACTIVE status must never auto-sync."""
    now = 1000.0
    pkg = make_package(syncrate=-1, lastsync=0.0, status="ACTIVE")
    assert should_auto_sync(pkg, now, errorcontinuetime=60) is False


def test_push_mode_error_past_errorcontinuetime_returns_false():
    """PUSH package in ERROR state must not auto-retry even when far past errorcontinuetime."""
    now = 10000.0
    pkg = make_package(syncrate=-1, lastsync=0.0, status="ERROR")
    assert should_auto_sync(pkg, now, errorcontinuetime=60) is False


def test_zero_syncrate_active_returns_false():
    """syncrate=0 is manual-only and must not auto-sync."""
    now = 1000.0
    pkg = make_package(syncrate=0, lastsync=0.0, status="ACTIVE")
    assert should_auto_sync(pkg, now, errorcontinuetime=60) is False


def test_zero_syncrate_error_returns_false():
    """syncrate=0 must not auto-retry even in ERROR state."""
    now = 10000.0
    pkg = make_package(syncrate=0, lastsync=0.0, status="ERROR")
    assert should_auto_sync(pkg, now, errorcontinuetime=60) is False


def test_syncrate_not_elapsed_returns_false():
    """Package whose syncrate has not elapsed and is not in ERROR must not sync."""
    now = 1000.0
    pkg = make_package(syncrate=60, lastsync=now - 30, status="ACTIVE")
    assert should_auto_sync(pkg, now, errorcontinuetime=60) is False


def test_syncrate_elapsed_returns_true():
    """Package whose syncrate has elapsed should be synced."""
    now = 1000.0
    pkg = make_package(syncrate=60, lastsync=now - 90, status="ACTIVE")
    assert should_auto_sync(pkg, now, errorcontinuetime=60) is True


def test_error_retry_within_syncrate_returns_true():
    """ERROR package past errorcontinuetime but within syncrate should retry."""
    now = 1000.0
    # lastsync=now-120 < syncrate=600 but > errorcontinuetime=60
    pkg = make_package(syncrate=600, lastsync=now - 120, status="ERROR")
    assert should_auto_sync(pkg, now, errorcontinuetime=60) is True
