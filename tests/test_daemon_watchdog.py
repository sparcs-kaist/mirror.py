"""Tests for the _watchdog_check helper in mirror.command.daemon."""
import pytest
from unittest.mock import MagicMock, call

import mirror
import mirror.sync as sync_mod
from mirror.sync import _watchdog_fired, _start_lock
from mirror.command.daemon import _watchdog_check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_package(pkgid: str = "testpkg") -> MagicMock:
    """Build a minimal Package stub for _watchdog_check tests.

    Args:
        pkgid(str): Package identifier.

    Return:
        package(MagicMock): Stub exposing pkgid only. The watchdog cap now
            lives on `mirror.conf.max_runtime_seconds`, set per-test.
    """
    pkg = MagicMock()
    pkg.pkgid = pkgid
    return pkg


def _set_max_runtime(monkeypatch, seconds: int) -> None:
    """Install a fake mirror.conf with the given max_runtime_seconds."""
    fake_conf = MagicMock()
    fake_conf.max_runtime_seconds = seconds
    monkeypatch.setattr(mirror, "conf", fake_conf, raising=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_watchdog_fired():
    with _start_lock:
        _watchdog_fired.clear()
    yield
    with _start_lock:
        _watchdog_fired.clear()


@pytest.fixture(autouse=True)
def _mock_mirror_log(monkeypatch):
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_watchdog_disabled_when_max_runtime_zero(monkeypatch):
    """_watchdog_check must not call get_progress when max_runtime_seconds == 0."""
    _set_max_runtime(monkeypatch, 0)
    pkg = make_package()

    monkeypatch.setattr(
        "mirror.socket.worker.get_progress",
        lambda *a, **k: pytest.fail("get_progress should not be called"),
    )

    # Must return without error or calling get_progress.
    _watchdog_check(pkg)


def _make_stop_recorder(stop_calls, status="stopped"):
    """Return a stop_command stub that records calls and returns the given status."""
    def stop(job_id):
        stop_calls.append(job_id)
        return {"status": status, "job_id": job_id}
    return stop


def test_watchdog_kills_when_uptime_exceeds(monkeypatch):
    """_watchdog_check must call stop_command when uptime exceeds max_runtime."""
    pkgid = "pkg-over"
    _set_max_runtime(monkeypatch, 60)
    pkg = make_package(pkgid=pkgid)

    monkeypatch.setattr(
        "mirror.socket.worker.get_progress",
        lambda job_id: {"syncing": True, "info": {"uptime": 120.0}},
    )

    stop_calls = []
    monkeypatch.setattr("mirror.socket.worker.stop_command", _make_stop_recorder(stop_calls))

    _watchdog_check(pkg)

    assert len(stop_calls) == 1
    assert stop_calls[0] == pkgid
    with _start_lock:
        assert pkgid in _watchdog_fired


def test_watchdog_idempotent_on_repeat(monkeypatch):
    """A second _watchdog_check call in the same sync window must not call stop_command again."""
    pkgid = "pkg-idempotent"
    _set_max_runtime(monkeypatch, 60)
    pkg = make_package(pkgid=pkgid)

    monkeypatch.setattr(
        "mirror.socket.worker.get_progress",
        lambda job_id: {"syncing": True, "info": {"uptime": 120.0}},
    )

    stop_calls = []
    monkeypatch.setattr("mirror.socket.worker.stop_command", _make_stop_recorder(stop_calls))

    _watchdog_check(pkg)
    _watchdog_check(pkg)

    assert len(stop_calls) == 1


def test_watchdog_handles_get_progress_error(monkeypatch):
    """_watchdog_check must swallow get_progress exceptions without raising."""
    pkgid = "pkg-conn-err"
    _set_max_runtime(monkeypatch, 60)
    pkg = make_package(pkgid=pkgid)

    monkeypatch.setattr(
        "mirror.socket.worker.get_progress",
        lambda job_id: (_ for _ in ()).throw(ConnectionError("nope")),
    )

    stop_calls = []
    monkeypatch.setattr(
        "mirror.socket.worker.stop_command",
        lambda job_id: stop_calls.append(job_id),
    )

    # Must not raise.
    _watchdog_check(pkg)

    assert stop_calls == []
    with _start_lock:
        assert pkgid not in _watchdog_fired


def test_watchdog_skips_when_not_syncing(monkeypatch):
    """_watchdog_check must do nothing when get_progress reports syncing=False."""
    pkgid = "pkg-not-syncing"
    _set_max_runtime(monkeypatch, 60)
    pkg = make_package(pkgid=pkgid)

    monkeypatch.setattr(
        "mirror.socket.worker.get_progress",
        lambda job_id: {"syncing": False},
    )

    stop_calls = []
    monkeypatch.setattr(
        "mirror.socket.worker.stop_command",
        lambda job_id: stop_calls.append(job_id),
    )

    _watchdog_check(pkg)

    assert stop_calls == []
    with _start_lock:
        assert pkgid not in _watchdog_fired


def test_watchdog_under_cap(monkeypatch):
    """_watchdog_check must not kill when uptime is below the cap."""
    pkgid = "pkg-under-cap"
    _set_max_runtime(monkeypatch, 60)
    pkg = make_package(pkgid=pkgid)

    monkeypatch.setattr(
        "mirror.socket.worker.get_progress",
        lambda job_id: {"syncing": True, "info": {"uptime": 30.0}},
    )

    stop_calls = []
    monkeypatch.setattr(
        "mirror.socket.worker.stop_command",
        lambda job_id: stop_calls.append(job_id),
    )

    _watchdog_check(pkg)

    assert stop_calls == []
    with _start_lock:
        assert pkgid not in _watchdog_fired


def test_watchdog_releases_marker_on_stop_failure(monkeypatch):
    """When stop_command raises, the watchdog marker must be released so a retry is possible."""
    pkgid = "pkg-transient"
    _set_max_runtime(monkeypatch, 60)
    pkg = make_package(pkgid=pkgid)

    monkeypatch.setattr(
        "mirror.socket.worker.get_progress",
        lambda job_id: {"syncing": True, "info": {"uptime": 120.0}},
    )

    # First call: stop_command raises a transient error.
    monkeypatch.setattr(
        "mirror.socket.worker.stop_command",
        lambda job_id: (_ for _ in ()).throw(ConnectionError("transient")),
    )

    # Must not raise.
    _watchdog_check(pkg)

    # Marker must be released after stop_command failure.
    with _start_lock:
        assert pkgid not in _watchdog_fired

    # Second call: stop_command succeeds — watchdog retries.
    stop_calls = []
    monkeypatch.setattr("mirror.socket.worker.stop_command", _make_stop_recorder(stop_calls))

    _watchdog_check(pkg)

    assert len(stop_calls) == 1
    assert stop_calls[0] == pkgid


def test_watchdog_releases_marker_on_not_found_response(monkeypatch):
    """When stop_command returns a non-stopped status, the watchdog marker must be released.

    Worker may legitimately return {"status": "not_found"} if the job has
    already been pruned. In that case the kill is moot, but the marker should
    not stay claimed forever.
    """
    pkgid = "pkg-not-found"
    _set_max_runtime(monkeypatch, 60)
    pkg = make_package(pkgid=pkgid)

    monkeypatch.setattr(
        "mirror.socket.worker.get_progress",
        lambda job_id: {"syncing": True, "info": {"uptime": 120.0}},
    )

    stop_calls = []
    monkeypatch.setattr(
        "mirror.socket.worker.stop_command",
        _make_stop_recorder(stop_calls, status="not_found"),
    )

    _watchdog_check(pkg)

    assert stop_calls == [pkgid]
    with _start_lock:
        assert pkgid not in _watchdog_fired
