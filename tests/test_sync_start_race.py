"""Sync start race rejection + leak-free cleanup on early failure."""
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

import mirror
from mirror.sync import _start_lock, start
import mirror.sync as sync_mod


@pytest.fixture(autouse=True)
def _clear_sync_extras():
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


def _make_pkg(pkgid: str = "race"):
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

        # Start thread 1 and wait until it is executing (i.e., status is SYNC).
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


def test_status_error_when_execute_raises_immediately():
    pkg = _make_pkg("race2")
    pkgid = pkg.pkgid
    import mirror.plugin
    fake_record = MagicMock()
    fake_record.execute = MagicMock(side_effect=RuntimeError("boom"))
    fake_record.on_sync_done = None

    fake_packages = MagicMock()
    fake_packages.get = MagicMock(return_value=pkg)

    import mirror
    with patch.dict("mirror.plugin._registry", {"rsync": fake_record}, clear=False), \
         patch("mirror.logger.create_logger") as mk, \
         patch("mirror.logger.get", return_value=MagicMock(handlers=[])), \
         patch("mirror.logger.close_logger", return_value="/tmp/fake.log"), \
         patch.object(mirror, "packages", fake_packages, create=True), \
         patch.object(mirror, "sync", sync_mod):
        mk.return_value = MagicMock(handlers=[])
        start(pkg)
        # Give the daemon thread a moment to fail.
        time.sleep(0.2)

    # After execute raises, on_sync_done is called which transitions status to ERROR.
    assert pkg.status == "ERROR", (
        f"expected status ERROR after execute failure, got {pkg.status!r}"
    )
