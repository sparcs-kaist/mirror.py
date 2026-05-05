"""Sync start race rejection + leak-free cleanup on early failure."""
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

import mirror
from mirror.sync import _in_progress, _start_lock, start
import mirror.sync as sync_mod


@pytest.fixture(autouse=True)
def _clear_in_progress():
    with _start_lock:
        _in_progress.clear()
    yield
    with _start_lock:
        _in_progress.clear()


@pytest.fixture(autouse=True)
def _mock_mirror_log(monkeypatch):
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)


def _make_pkg(pkgid: str = "race"):
    pkg = MagicMock()
    pkg.pkgid = pkgid
    pkg.name = f"Pkg {pkgid}"
    pkg.synctype = "rsync"
    pkg.set_status = MagicMock()
    return pkg


def test_concurrent_start_rejects_second_call():
    pkg = _make_pkg("race1")
    started_event = threading.Event()
    results = []
    results_lock = threading.Lock()

    def block_execute(*a, **kw):
        started_event.set()
        time.sleep(0.3)

    import mirror.plugin
    fake_record = MagicMock()
    fake_record.execute = MagicMock(side_effect=block_execute)
    fake_record.on_sync_done = None

    import mirror
    with patch.dict("mirror.plugin._registry", {"rsync": fake_record}, clear=False), \
         patch("mirror.logger.create_logger") as mk, \
         patch.object(mirror, "sync", sync_mod):
        mk.return_value = MagicMock(handlers=[])

        # Start thread 1 and wait until it is executing (i.e., pkgid in _in_progress).
        start(pkg)
        with results_lock:
            results.append("started")

        started_event.wait(timeout=2.0)

        # Now thread 1's _runner is inside execute; try to start again.
        try:
            start(pkg)
            with results_lock:
                results.append("started_again")
        except RuntimeError as e:
            with results_lock:
                results.append(f"rejected: {e}")

        # Wait for the background thread to finish.
        time.sleep(0.5)

    assert "started" in results
    assert any(r.startswith("rejected:") for r in results), results


def test_in_progress_cleared_when_execute_raises_immediately():
    pkg = _make_pkg("race2")
    import mirror.plugin
    fake_record = MagicMock()
    fake_record.execute = MagicMock(side_effect=RuntimeError("boom"))
    fake_record.on_sync_done = None

    import mirror
    with patch.dict("mirror.plugin._registry", {"rsync": fake_record}, clear=False), \
         patch("mirror.logger.create_logger") as mk, \
         patch.object(mirror, "sync", sync_mod):
        mk.return_value = MagicMock(handlers=[])
        start(pkg)
        # Give the daemon thread a moment to fail.
        time.sleep(0.2)

    with _start_lock:
        assert pkg.pkgid not in _in_progress, _in_progress
