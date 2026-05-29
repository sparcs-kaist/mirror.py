"""Tests for the stale/duplicate-completion guard in mirror.sync.on_sync_done.

on_sync_done must be idempotent with respect to the package lifecycle: if the
package already left SYNC (e.g. the daemon reconciliation marked it ERROR after
a lost notification, or a duplicate notification is delivered), a late
completion must NOT overwrite the resolved status, close the logger, or update
lastsync. It must only drop the in-flight bookkeeping so the next sync is clean.
"""
from unittest.mock import MagicMock

import pytest

import mirror
import mirror.logger
import mirror.structure
import mirror.sync
from mirror.sync import on_sync_done, _extra_args, _watchdog_fired, _start_lock


@pytest.fixture(autouse=True)
def _stub_event(monkeypatch):
    monkeypatch.setattr("mirror.event.post_event", lambda *a, **kw: None)


@pytest.fixture(autouse=True)
def _mock_mirror_log(monkeypatch):
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)


@pytest.fixture(autouse=True)
def _clear_sync_state():
    with _start_lock:
        _extra_args.clear()
        _watchdog_fired.clear()
    yield
    with _start_lock:
        _extra_args.clear()
        _watchdog_fired.clear()


def _make_package(status: str) -> mirror.structure.Package:
    settings = mirror.structure.PackageSettings(hidden=False, src="x", dst="y", options={})
    pkg = mirror.structure.Package(
        pkgid="pkg",
        name="pkg",
        status=status,
        href="/pkg",
        synctype="rsync",
        syncrate=60,
        link=[],
        settings=settings,
    )
    pkg.statusinfo.runninglog = None
    return pkg


@pytest.mark.parametrize("resolved_status", ["ERROR", "ACTIVE"])
def test_on_sync_done_ignores_stale_completion(resolved_status, monkeypatch):
    """A completion for a package no longer in SYNC must not mutate its status."""
    pkg = _make_package(resolved_status)
    pkg.lastsync = 12345.0
    monkeypatch.setattr(mirror, "packages", {"pkg": pkg}, raising=False)

    monkeypatch.setattr(mirror.logger, "get", lambda name: MagicMock())
    close_mock = MagicMock()
    monkeypatch.setattr(mirror.logger, "close_logger", close_mock)

    with _start_lock:
        _extra_args["pkg"] = {"FOO": "BAR"}
        _watchdog_fired.add("pkg")

    on_sync_done("pkg", success=True, returncode=0)

    # Resolved status and timestamps are left untouched.
    assert pkg.status == resolved_status
    assert pkg.lastsync == 12345.0
    close_mock.assert_not_called()

    # In-flight bookkeeping is cleared so the next sync starts clean.
    with _start_lock:
        assert "pkg" not in _extra_args
        assert "pkg" not in _watchdog_fired
