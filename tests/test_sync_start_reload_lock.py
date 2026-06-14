"""Tests that start() acquires _reload_state_lock around set_status calls."""
import threading
from unittest.mock import MagicMock, patch, call

import pytest

import mirror
import mirror.config
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

    def _set_status(status, logfile=None):
        pkg.status = status

    pkg.set_status = MagicMock(side_effect=_set_status)
    pkg.is_syncing.side_effect = lambda: pkg.status == "SYNC"
    return pkg


def test_start_acquires_reload_state_lock():
    """start() must hold _reload_state_lock when calling set_status("SYNC")."""
    pkg = _make_pkg("lock-test-1")

    lock_entered_before_set_status = []
    real_lock = mirror.config._reload_state_lock

    # Track whether the real lock was acquired when set_status is called.
    original_set_status = pkg.set_status.side_effect

    def _tracked_set_status(status, logfile=None):
        # RLock.acquire(blocking=False) returns False if the lock is not held
        # by any thread, True if it IS held (and we can re-enter because it's an RLock).
        # We use _is_owned() which is a CPython internal but reliable in tests.
        # Alternative: try non-blocking acquire from a *different* thread.
        acquired = real_lock.acquire(blocking=False)
        if acquired:
            # We got it, meaning no other thread held it exclusively — but since
            # this function is called from the same thread that holds the lock,
            # the RLock allows re-entry. The real check is done via a side-channel
            # thread below.
            real_lock.release()
        lock_entered_before_set_status.append(acquired)
        if original_set_status:
            original_set_status(status, logfile=logfile)

    pkg.set_status = MagicMock(side_effect=_tracked_set_status)
    pkg.is_syncing.side_effect = lambda: pkg.status == "SYNC"

    # Use a separate thread to verify the lock is held by the calling thread
    # when set_status runs. We record whether a competing thread was blocked.
    lock_was_held = threading.Event()

    original_set_status_side = _tracked_set_status

    def _contending_thread_check():
        """Try to acquire the lock non-blocking while set_status is running."""
        pass  # actual check is inline below via event coordination

    acquired_events: list[bool] = []

    # Patch set_status to signal a contending thread and check lock ownership.
    contender_go = threading.Event()
    contender_done = threading.Event()
    contender_result: list[bool] = []

    def _patched_set_status(status, logfile=None):
        # Signal contender and wait for its result.
        contender_go.set()
        contender_done.wait(timeout=2.0)
        pkg.status = status

    pkg.set_status = MagicMock(side_effect=_patched_set_status)
    pkg.is_syncing.side_effect = lambda: pkg.status == "SYNC"

    def _contender():
        contender_go.wait(timeout=2.0)
        # Try to acquire _reload_state_lock from a different thread.
        # If start() holds it, this should fail (non-blocking).
        got = real_lock.acquire(blocking=False)
        contender_result.append(got)
        if got:
            real_lock.release()
        contender_done.set()

    contender_thread = threading.Thread(target=_contender, daemon=True)
    contender_thread.start()

    fake_record = MagicMock()
    fake_record.execute = MagicMock(side_effect=lambda *a, **kw: None)
    fake_record.on_sync_done = None

    with patch.dict("mirror.plugin._registry", {"rsync": fake_record}, clear=False), \
         patch("mirror.logger.create_logger", return_value=MagicMock(handlers=[])), \
         patch.object(mirror, "sync", sync_mod):
        start(pkg)

    contender_thread.join(timeout=3.0)

    # contender_result[0] == False means the lock was held (contender blocked) —
    # which is what we want. If True, the lock was NOT held during set_status.
    assert len(contender_result) == 1
    assert contender_result[0] is False, (
        "_reload_state_lock was NOT held during set_status('SYNC') in start()"
    )


def test_failure_path_acquires_reload_state_lock():
    """The failure-path set_status('ERROR') in start() must hold _reload_state_lock."""
    pkg = _make_pkg("lock-test-2")

    real_lock = mirror.config._reload_state_lock

    contender_go = threading.Event()
    contender_done = threading.Event()
    contender_result: list[bool] = []

    def _patched_set_status(status, logfile=None):
        if status == "ERROR":
            # Signal contender and wait for its result.
            contender_go.set()
            contender_done.wait(timeout=2.0)
        pkg.status = status

    pkg.set_status = MagicMock(side_effect=_patched_set_status)
    pkg.is_syncing.side_effect = lambda: pkg.status == "SYNC"

    def _contender():
        contender_go.wait(timeout=2.0)
        got = real_lock.acquire(blocking=False)
        contender_result.append(got)
        if got:
            real_lock.release()
        contender_done.set()

    contender_thread = threading.Thread(target=_contender, daemon=True)
    contender_thread.start()

    # Patch create_logger to raise so the failure path (started=False) executes.
    with patch("mirror.logger.create_logger", side_effect=RuntimeError("logger failed")), \
         patch.dict("mirror.plugin._registry", {"rsync": MagicMock()}, clear=False), \
         patch.object(mirror, "sync", sync_mod):
        # create_logger raises (under lock), the in-lock except block then calls
        # set_status("ERROR") while still holding _reload_state_lock.
        # We need pkg to start as not-syncing so start() doesn't reject.
        pkg.status = "UNKNOWN"
        with pytest.raises(RuntimeError, match="logger failed"):
            start(pkg)

    contender_thread.join(timeout=3.0)

    assert len(contender_result) == 1
    assert contender_result[0] is False, (
        "_reload_state_lock was NOT held during set_status('ERROR') in failure path of start()"
    )
