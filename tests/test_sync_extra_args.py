"""Tests for extra_args lifecycle in mirror.sync."""
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.sync as sync_mod
from mirror.sync import (
    _extra_args,
    _start_lock,
    _validate_extra_args,
    get_extra_args,
    on_sync_done,
    start,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_registries():
    with _start_lock:
        _extra_args.clear()
    yield
    with _start_lock:
        _extra_args.clear()


@pytest.fixture(autouse=True)
def _mock_mirror_log(monkeypatch):
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)


def _make_pkg(pkgid: str = "testpkg") -> MagicMock:
    pkg = MagicMock()
    pkg.pkgid = pkgid
    pkg.name = f"Pkg {pkgid}"
    pkg.synctype = "rsync"
    pkg.status = "UNKNOWN"

    def _set_status(status, logfile=None):
        pkg.status = status

    pkg.set_status = MagicMock(side_effect=_set_status)
    pkg.is_syncing.side_effect = lambda: pkg.status == "SYNC"
    return pkg


def _make_fake_record() -> MagicMock:
    record = MagicMock()
    record.execute = MagicMock(return_value=None)
    record.on_sync_done = None
    return record


# ---------------------------------------------------------------------------
# (a) _validate_extra_args
# ---------------------------------------------------------------------------

def test_validate_extra_args_valid():
    result = _validate_extra_args({"K": "V"})
    assert result == {"K": "V"}


def test_validate_extra_args_key_with_equals_raises():
    with pytest.raises(ValueError):
        _validate_extra_args({"K=Y": "v"})


def test_validate_extra_args_value_with_nul_raises():
    with pytest.raises(ValueError):
        _validate_extra_args({"K": "v\x00"})


def test_validate_extra_args_empty_key_raises():
    with pytest.raises(ValueError):
        _validate_extra_args({"": "v"})


def test_validate_extra_args_non_str_value_raises():
    with pytest.raises(ValueError):
        _validate_extra_args({"K": 1})  # type: ignore[dict-item]


def test_validate_extra_args_non_str_key_raises():
    with pytest.raises(ValueError):
        _validate_extra_args({1: "v"})  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# (b) start registers extra_args; on_sync_done clears them
# ---------------------------------------------------------------------------

def test_start_registers_extra_args_and_on_sync_done_clears():
    import threading

    pkg = _make_pkg("pkg_b")
    pkgid = pkg.pkgid

    execute_started = threading.Event()
    execute_gate = threading.Event()

    def blocking_execute(*a, **kw):
        execute_started.set()
        execute_gate.wait(timeout=5.0)

    fake_record = MagicMock()
    fake_record.execute = MagicMock(side_effect=blocking_execute)
    fake_record.on_sync_done = None

    fake_pkg_logger = MagicMock(handlers=[])
    fake_packages = MagicMock()
    fake_packages.get = MagicMock(return_value=pkg)

    with patch.dict("mirror.plugin._registry", {"rsync": fake_record}, clear=False), \
         patch("mirror.logger.create_logger", return_value=fake_pkg_logger), \
         patch("mirror.logger.close_logger", return_value="/tmp/fake.log"), \
         patch("mirror.logger.get", return_value=fake_pkg_logger), \
         patch.object(mirror, "packages", fake_packages, create=True), \
         patch.object(mirror, "sync", sync_mod):

        start(pkg, extra_args={"K": "V"})

        # Wait until the runner thread is inside execute (extra_args must still be registered).
        execute_started.wait(timeout=2.0)
        assert get_extra_args(pkgid) == {"K": "V"}

        # Release the blocking execute, then call on_sync_done manually.
        execute_gate.set()

        on_sync_done(pkgid, success=True, returncode=0)

    assert get_extra_args(pkgid) == {}
    assert not pkg.is_syncing()


# ---------------------------------------------------------------------------
# (c) Stale clear: extra_args evicted when start is called with extra_args=None
# ---------------------------------------------------------------------------

def test_start_clears_stale_extra_args():
    pkg = _make_pkg("pkg_c")
    pkgid = pkg.pkgid
    fake_record = _make_fake_record()

    # Seed stale entry (pkgid is NOT syncing)
    with _start_lock:
        _extra_args[pkgid] = {"OLD": "X"}

    fake_pkg_logger = MagicMock(handlers=[])

    with patch.dict("mirror.plugin._registry", {"rsync": fake_record}, clear=False), \
         patch("mirror.logger.create_logger", return_value=fake_pkg_logger), \
         patch.object(mirror, "sync", sync_mod):

        start(pkg, extra_args=None)
        # After start (even before thread finishes), the stale entry was evicted
        # inside the lock when we took ownership of the slot.
        assert get_extra_args(pkgid) == {}


# ---------------------------------------------------------------------------
# (d) Failure-path scoped clear: RuntimeError from missing plugin clears state
# ---------------------------------------------------------------------------

def test_start_failure_clears_extra_args(monkeypatch):
    pkg = _make_pkg("pkg_d")
    pkgid = pkg.pkgid

    fake_pkg_logger = MagicMock(handlers=[])

    # Make get_record return None so start() raises RuntimeError inside the try block.
    with patch("mirror.plugin.get_record", return_value=None), \
         patch("mirror.logger.create_logger", return_value=fake_pkg_logger), \
         patch.object(mirror, "sync", sync_mod):

        with pytest.raises(RuntimeError):
            start(pkg, extra_args={"K": "V"})

    assert get_extra_args(pkgid) == {}
    # start() failure path transitions status to ERROR.
    assert pkg.status == "ERROR"


# ---------------------------------------------------------------------------
# (e) Already-running preserves the live sync's env
# ---------------------------------------------------------------------------

def test_already_running_preserves_extra_args():
    pkg = _make_pkg("pkg_e")
    pkgid = pkg.pkgid

    # Seed: pretend a sync is already running with its own extra_args.
    # Set status to SYNC so start() treats the package as in-flight.
    pkg.status = "SYNC"
    with _start_lock:
        _extra_args[pkgid] = {"RUNNING": "yes"}

    try:
        with pytest.raises(RuntimeError):
            start(pkg, extra_args=None)

        # Live sync's data must be preserved.
        assert get_extra_args(pkgid) == {"RUNNING": "yes"}
    finally:
        pkg.status = "UNKNOWN"
        with _start_lock:
            _extra_args.pop(pkgid, None)


# ---------------------------------------------------------------------------
# (f) Bad input does not mutate state
# ---------------------------------------------------------------------------

def test_bad_input_does_not_mutate_state():
    pkg = _make_pkg("pkg_f")

    with _start_lock:
        assert not _extra_args

    with pytest.raises(ValueError):
        start(pkg, extra_args={"K=BAD": "v"})

    with _start_lock:
        assert not _extra_args

    with pytest.raises(ValueError):
        start(pkg, extra_args={"K": "v\x00"})

    with _start_lock:
        assert not _extra_args
