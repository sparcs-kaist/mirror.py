"""Tests for the watchdog pure helper and idempotency registry in mirror.sync."""
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.sync as sync_mod
from mirror.sync import (
    _extra_args,
    _start_lock,
    _watchdog_fired,
    mark_watchdog_fired,
    on_sync_done,
    release_watchdog_fired,
    should_kill_for_max_runtime,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_registries():
    with _start_lock:
        _extra_args.clear()
        _watchdog_fired.clear()
    yield
    with _start_lock:
        _extra_args.clear()
        _watchdog_fired.clear()


@pytest.fixture(autouse=True)
def _mock_mirror_log(monkeypatch):
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)


# ---------------------------------------------------------------------------
# should_kill_for_max_runtime
# ---------------------------------------------------------------------------

def test_should_kill_uptime_none():
    assert should_kill_for_max_runtime(None, 60) is False


def test_should_kill_cap_zero():
    assert should_kill_for_max_runtime(30.0, 0) is False


def test_should_kill_under_cap():
    assert should_kill_for_max_runtime(30.0, 60) is False


def test_should_kill_over_cap():
    assert should_kill_for_max_runtime(61.0, 60) is True


def test_should_kill_negative_cap():
    assert should_kill_for_max_runtime(120.0, -5) is False


# ---------------------------------------------------------------------------
# mark_watchdog_fired / release_watchdog_fired
# ---------------------------------------------------------------------------

def test_mark_watchdog_fired_first_call_returns_true():
    assert mark_watchdog_fired("p") is True


def test_mark_watchdog_fired_second_call_returns_false():
    mark_watchdog_fired("p")
    assert mark_watchdog_fired("p") is False


def test_release_watchdog_fired_allows_remarking():
    mark_watchdog_fired("p")
    release_watchdog_fired("p")
    assert mark_watchdog_fired("p") is True


def test_release_watchdog_fired_nonexistent_is_noop():
    # Must not raise
    release_watchdog_fired("nonexistent")


# ---------------------------------------------------------------------------
# on_sync_done clears _watchdog_fired
# ---------------------------------------------------------------------------

def _make_pkg(pkgid: str = "testpkg") -> MagicMock:
    pkg = MagicMock()
    pkg.pkgid = pkgid
    pkg.name = f"Pkg {pkgid}"
    pkg.synctype = "rsync"
    pkg.set_status = MagicMock()
    pkg.is_syncing.return_value = False
    return pkg


def test_on_sync_done_clears_watchdog_fired():
    pkgid = "p"
    pkg = _make_pkg(pkgid)

    fake_pkg_logger = MagicMock(handlers=[])
    fake_packages = MagicMock()
    fake_packages.get = MagicMock(return_value=pkg)

    # Seed state: pretend a sync is finishing with the watchdog marker set.
    with _start_lock:
        _watchdog_fired.add(pkgid)

    with patch("mirror.plugin.get_record", return_value=None), \
         patch("mirror.logger.get", return_value=fake_pkg_logger), \
         patch("mirror.logger.close_logger", return_value="/tmp/fake.log"), \
         patch.object(mirror, "packages", fake_packages, create=True), \
         patch.object(mirror, "sync", sync_mod):

        on_sync_done(pkgid, success=False, returncode=None)

    with _start_lock:
        assert pkgid not in _watchdog_fired
