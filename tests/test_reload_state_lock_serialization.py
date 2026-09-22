"""Tests that _reload_state_lock serializes save_stat_data and _perform_reload."""
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import mirror
import mirror.config
import mirror.structure
import mirror.sync


@pytest.fixture()
def lock_env(tmp_path, monkeypatch, reload_config_factory, reload_package_factory):
    """Set up mirror.config globals with a known package for lock tests."""
    config_path = tmp_path / "config.json"
    stat_path = tmp_path / "stat.json"
    status_path = tmp_path / "status.json"
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    initial_cfg = reload_config_factory(tmp_path, default_pkgid="pkg-lock")
    config_path.write_text(json.dumps(initial_cfg))
    stat_path.write_text(json.dumps({"packages": {}}))
    status_path.write_text(json.dumps({}))

    monkeypatch.setattr(mirror.config, "CONFIG_PATH", config_path, raising=False)
    monkeypatch.setattr(mirror.config, "STAT_DATA_PATH", stat_path, raising=False)
    monkeypatch.setattr(mirror.config, "STATUS_PATH", status_path, raising=False)
    monkeypatch.setattr(mirror.config, "SOCKET_PATH", str(tmp_path / "mirror.sock"), raising=False)
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)

    mirror.conf = mirror.structure.Config.load_from_dict(initial_cfg)
    mirror.packages = mirror.structure.Packages(
        {
            "pkg-lock": {
                **reload_package_factory("pkg-lock"),
                "status": {"status": "UNKNOWN", "statusinfo": {"errorcount": 0, "lastsync": 0.0}},
            }
        }
    )

    with mirror.sync._start_lock:
        mirror.sync._extra_args.clear()
        mirror.sync._watchdog_fired.clear()

    yield {
        "tmp_path": tmp_path,
        "config_path": config_path,
        "stat_path": stat_path,
    }

    with mirror.sync._start_lock:
        mirror.sync._extra_args.clear()
        mirror.sync._watchdog_fired.clear()


# ---------------------------------------------------------------------------
# Test 1: save_stat_data blocks while _reload_state_lock is held by another thread
# ---------------------------------------------------------------------------

def test_save_stat_data_serializes_with_reload_lock(lock_env):
    """save_stat_data() blocks until _reload_state_lock is released by a holder."""
    lock = mirror.config._reload_state_lock

    holder_entered = threading.Event()
    holder_release = threading.Event()
    caller_started = threading.Event()
    caller_done = threading.Event()

    timeline: list[str] = []

    def _holder():
        with lock:
            timeline.append("holder_acquired")
            holder_entered.set()
            # Hold the lock until signalled.
            holder_release.wait(timeout=5.0)
            timeline.append("holder_releasing")

    def _caller():
        caller_started.set()
        mirror.config.save_stat_data()
        timeline.append("caller_done")
        caller_done.set()

    holder_thread = threading.Thread(target=_holder, daemon=True)
    caller_thread = threading.Thread(target=_caller, daemon=True)

    holder_thread.start()
    # Wait for the holder to acquire before starting the caller.
    assert holder_entered.wait(timeout=2.0), "holder did not acquire lock"

    caller_thread.start()
    assert caller_started.wait(timeout=2.0)

    # Give the caller a moment to attempt entry — it should be blocked.
    time.sleep(0.1)
    assert "caller_done" not in timeline, "save_stat_data should be blocked by the lock"

    # Release the holder.
    holder_release.set()
    holder_thread.join(timeout=2.0)

    # Now the caller should complete.
    assert caller_done.wait(timeout=3.0), "save_stat_data did not complete after lock release"
    caller_thread.join(timeout=2.0)

    # Order must be: holder acquired → holder releasing → caller done.
    assert timeline.index("holder_releasing") < timeline.index("caller_done")


# ---------------------------------------------------------------------------
# Test 2: _perform_reload and on_sync_done are serialized by _reload_state_lock
# ---------------------------------------------------------------------------

def test_perform_reload_and_on_sync_done_are_serialized(
    lock_env,
    monkeypatch,
    reload_config_factory,
):
    """on_sync_done blocks while _reload_state_lock is held inside _perform_reload."""
    # We intercept the lock acquisition inside _load_from_dict to pause mid-reload
    # and measure that on_sync_done blocks for that duration.
    lock = mirror.config._reload_state_lock

    reload_locked = threading.Event()
    reload_release = threading.Event()
    on_done_started = threading.Event()
    on_done_done = threading.Event()

    timeline: list[str] = []

    # Patch _load_from_dict to pause while holding the lock.
    original_load_from_dict = mirror.config._load_from_dict

    def _patched_load_from_dict(config_dict, *, source_path=None, load_plugins=True):
        with lock:
            timeline.append("reload_inside_lock")
            reload_locked.set()
            # Hold until signalled.
            reload_release.wait(timeout=5.0)
            timeline.append("reload_releasing")
        # Call the real implementation AFTER the extra lock block exits
        # so the global state actually updates (prevents assertion errors
        # in _perform_reload's own lock usage which also calls _load_from_dict).

    monkeypatch.setattr(mirror.config, "_load_from_dict", _patched_load_from_dict)

    # Write a valid (no-change) config so _perform_reload reaches _load_from_dict.
    env = lock_env
    new_cfg = reload_config_factory(env["tmp_path"], default_pkgid="pkg-lock")
    env["config_path"].write_text(json.dumps(new_cfg))

    # Set up on_sync_done with an empty packages (simulating unknown pkg).
    fake_pkgs = MagicMock()
    fake_pkgs.get = MagicMock(return_value=None)
    monkeypatch.setattr(mirror, "packages", fake_pkgs, raising=False)
    monkeypatch.setattr(mirror.logger, "get", lambda name: None)

    def _reload_worker():
        mirror.config._perform_reload()

    def _on_done_worker():
        on_done_started.set()
        mirror.sync.on_sync_done("some-pkg", success=True, returncode=0)
        timeline.append("on_sync_done_done")
        on_done_done.set()

    reload_thread = threading.Thread(target=_reload_worker, daemon=True)
    reload_thread.start()

    # Wait until _perform_reload has entered the lock.
    assert reload_locked.wait(timeout=3.0), "_perform_reload did not enter lock"

    on_done_thread = threading.Thread(target=_on_done_worker, daemon=True)
    on_done_thread.start()
    assert on_done_started.wait(timeout=2.0)

    # Give on_sync_done time to try (and be blocked by) the lock.
    time.sleep(0.1)
    assert "on_sync_done_done" not in timeline, "on_sync_done should be blocked by reload's lock"

    # Release the reload lock.
    reload_release.set()
    reload_thread.join(timeout=3.0)

    # on_sync_done should now complete.
    assert on_done_done.wait(timeout=3.0), "on_sync_done did not complete after reload released lock"
    on_done_thread.join(timeout=2.0)

    assert timeline.index("reload_releasing") < timeline.index("on_sync_done_done")
