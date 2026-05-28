"""Tests for mirror.config._perform_reload diff classification and sanitization."""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import mirror
import mirror.config
import mirror.structure
import mirror.sync


# ---------------------------------------------------------------------------
# Shared config builders
# ---------------------------------------------------------------------------

def _make_settings(tmp_path: Path, **overrides) -> dict:
    base = {
        "logfolder": str(tmp_path / "logs"),
        "webroot": str(tmp_path / "web"),
        "statusfile": str(tmp_path / "status.json"),
        "statfile": str(tmp_path / "stat.json"),
        "socket_path": str(tmp_path / "mirror.sock"),
        "uid": 1000,
        "gid": 1000,
        "localtimezone": "UTC",
        "errorcontinuetime": 60,
        "maintainer": {"name": "Test", "email": "t@t.com"},
        "logger": {
            "level": "INFO",
            "packagelevel": "ERROR",
            "format": "[%(asctime)s] %(levelname)s # %(message)s",
            "packageformat": "[%(asctime)s][{package}] %(levelname)s # %(message)s",
            "fileformat": {
                "base": str(tmp_path / "logs"),
                "folder": "{year}/{month}",
                "filename": "{year}-{month}-{day}.log",
                "gzip": False,
            },
            "packagefileformat": {
                "base": str(tmp_path / "logs" / "packages"),
                "folder": "{year}/{month}/{day}",
                "filename": "{packageid}.{hour}.log",
                "gzip": False,
            },
        },
        "ftpsync": {
            "maintainer": "M",
            "sponsor": "S",
            "country": "KR",
            "location": "Seoul",
            "throughput": "1G",
        },
        "plugins": [],
    }
    base.update(overrides)
    return base


def _make_pkg_entry(pkgid: str, src: str = "rsync://src/a", syncrate: str = "PT1H") -> dict:
    return {
        "id": pkgid,
        "name": pkgid,
        "href": f"/{pkgid}",
        "synctype": "rsync",
        "syncrate": syncrate,
        "link": [],
        "settings": {
            "hidden": False,
            "src": src,
            "dst": "/tmp/" + pkgid,
            "options": {},
        },
    }


def _make_config(tmp_path: Path, packages: dict, **settings_overrides) -> dict:
    return {
        "mirrorname": "TestMirror",
        "hostname": "test.local",
        "settings": _make_settings(tmp_path, **settings_overrides),
        "packages": packages,
    }


# ---------------------------------------------------------------------------
# Per-test fixture: set up a known starting state in mirror.config globals
# ---------------------------------------------------------------------------

@pytest.fixture()
def reload_env(tmp_path, monkeypatch):
    """Initialize mirror.config global state with a single known package."""
    # Restore mirror.toolbox to the real implementation in case a previous test
    # replaced it with a mock (e.g. test_example_config.py does this globally).
    # Use sys.modules directly — import-as returns the attribute (which may be a mock);
    # sys.modules["mirror.toolbox"] always returns the real module object.
    import sys as _sys
    _real_toolbox = _sys.modules["mirror.toolbox"]
    monkeypatch.setattr(mirror, "toolbox", _real_toolbox, raising=False)

    # Paths
    config_path = tmp_path / "config.json"
    stat_path = tmp_path / "stat.json"
    status_path = tmp_path / "status.json"
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    # Seed one package in the running state.
    initial_pkgs = {"pkg-one": _make_pkg_entry("pkg-one")}
    initial_cfg = _make_config(tmp_path, initial_pkgs)
    config_path.write_text(json.dumps(initial_cfg))
    stat_path.write_text(json.dumps({"packages": {}}))
    status_path.write_text(json.dumps({}))

    monkeypatch.setattr(mirror.config, "CONFIG_PATH", config_path, raising=False)
    monkeypatch.setattr(mirror.config, "STAT_DATA_PATH", stat_path, raising=False)
    monkeypatch.setattr(mirror.config, "STATUS_PATH", status_path, raising=False)
    monkeypatch.setattr(mirror.config, "SOCKET_PATH", str(tmp_path / "mirror.sock"), raising=False)
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)

    # Build a real mirror.conf and mirror.packages from the initial config.
    mirror.conf = mirror.structure.Config.load_from_dict(initial_cfg)
    mirror.packages = mirror.structure.Packages(
        {
            "pkg-one": {
                **_make_pkg_entry("pkg-one"),
                "status": {"status": "UNKNOWN", "statusinfo": {"errorcount": 0, "lastsync": 0.0}},
            }
        }
    )

    # Ensure sync auxiliary registries are clean.
    with mirror.sync._start_lock:
        mirror.sync._extra_args.clear()
        mirror.sync._watchdog_fired.clear()

    yield {
        "tmp_path": tmp_path,
        "config_path": config_path,
        "stat_path": stat_path,
        "status_path": status_path,
        "initial_cfg": initial_cfg,
        "initial_pkgs": initial_pkgs,
    }

    # Clean up sync state.
    with mirror.sync._start_lock:
        mirror.sync._extra_args.clear()
        mirror.sync._watchdog_fired.clear()


# ---------------------------------------------------------------------------
# Helper: write a new config to disk
# ---------------------------------------------------------------------------

def _write_config(env: dict, packages: dict, **settings_overrides) -> None:
    tmp_path = env["tmp_path"]
    new_cfg = _make_config(tmp_path, packages, **settings_overrides)
    env["config_path"].write_text(json.dumps(new_cfg))


# ---------------------------------------------------------------------------
# Test 1: no changes → empty diff
# ---------------------------------------------------------------------------

def test_perform_reload_no_changes(reload_env):
    """Identical config → added/removed/modified are all empty, status=ok."""
    _write_config(reload_env, reload_env["initial_pkgs"])

    result = mirror.config._perform_reload()

    assert result["status"] == "ok"
    assert result["added"] == []
    assert result["removed"] == []
    assert result["modified"] == []


# ---------------------------------------------------------------------------
# Test 2: detect added package
# ---------------------------------------------------------------------------

def test_perform_reload_detects_added_package(reload_env):
    """Config with a new pkgid → result['added'] contains it."""
    new_pkgs = {
        **reload_env["initial_pkgs"],
        "pkg-new": _make_pkg_entry("pkg-new"),
    }
    _write_config(reload_env, new_pkgs)

    result = mirror.config._perform_reload()

    assert result["status"] == "ok"
    assert "pkg-new" in result["added"]
    assert result["removed"] == []


# ---------------------------------------------------------------------------
# Test 3: detect removed package (idle)
# ---------------------------------------------------------------------------

def test_perform_reload_detects_removed_package(reload_env):
    """Config without an existing idle pkg → result['removed'] contains it, no kill."""
    _write_config(reload_env, {})  # remove pkg-one

    result = mirror.config._perform_reload()

    assert result["status"] == "ok"
    assert "pkg-one" in result["removed"]
    assert result["added"] == []
    # Not in-flight → killed_inflight must not contain it.
    assert "pkg-one" not in result.get("killed_inflight", [])


# ---------------------------------------------------------------------------
# Test 4: detect modified package
# ---------------------------------------------------------------------------

def test_perform_reload_detects_modified_package(reload_env):
    """Changing syncrate of an existing pkg → result['modified'] contains it."""
    modified_pkgs = {"pkg-one": _make_pkg_entry("pkg-one", syncrate="PT2H")}
    _write_config(reload_env, modified_pkgs)

    result = mirror.config._perform_reload()

    assert result["status"] == "ok"
    assert "pkg-one" in result["modified"]


def test_perform_reload_detects_disabled_flip(reload_env):
    """Flipping only the disabled flag must be reported in result['modified']."""
    flipped = {"pkg-one": {**_make_pkg_entry("pkg-one"), "disabled": True}}
    _write_config(reload_env, flipped)

    result = mirror.config._perform_reload()

    assert result["status"] == "ok"
    assert "pkg-one" in result["modified"]
    assert mirror.packages.get("pkg-one").is_disabled() is True


def test_perform_reload_preserves_lastsync_and_applies_config_changes(reload_env):
    """Reload must keep runtime stat fields without hiding config edits."""
    reload_env["stat_path"].write_text(json.dumps({
        "packages": {
            "pkg-one": {
                **_make_pkg_entry("pkg-one", src="rsync://old/a", syncrate="PT1H"),
                "lastsync": 2222.0,
                "timestamp": 3333.0,
                "status": {"status": "ACTIVE", "statusinfo": {"errorcount": 0}},
            }
        }
    }))
    modified_pkgs = {
        "pkg-one": _make_pkg_entry("pkg-one", src="rsync://new/a", syncrate="PT2H")
    }
    _write_config(reload_env, modified_pkgs)

    result = mirror.config._perform_reload()

    assert result["status"] == "ok"
    pkg = mirror.packages.get("pkg-one")
    assert pkg.lastsync == 2222.0
    assert pkg.timestamp == 3333.0
    assert pkg.status == "ACTIVE"
    assert pkg.syncrate == 7200
    assert pkg.settings.src == "rsync://new/a"


# ---------------------------------------------------------------------------
# Test 5: path-setting change → warns and ignores
# ---------------------------------------------------------------------------

def test_perform_reload_path_setting_change_warns_and_ignores(reload_env):
    """Changed socket_path/logfolder emits warnings but runtime values are unchanged."""
    original_socket = mirror.config.SOCKET_PATH
    original_logfolder = str(mirror.conf.logfolder)

    _write_config(
        reload_env,
        reload_env["initial_pkgs"],
        socket_path="/tmp/different.sock",
        logfolder="/tmp/different_logs",
    )

    result = mirror.config._perform_reload()

    # Warnings must mention each changed field.
    warned_keys = " ".join(result.get("warnings", []))
    assert "socket_path" in warned_keys
    assert "logfolder" in warned_keys

    # Runtime values must NOT have changed.
    assert mirror.config.SOCKET_PATH == original_socket
    assert str(mirror.conf.logfolder) == original_logfolder


# ---------------------------------------------------------------------------
# Test 6: plugin-config change → warns and ignores
# ---------------------------------------------------------------------------

def test_perform_reload_plugin_change_warns_and_ignores(reload_env):
    """Changed plugins block emits a warning; the plugin registry is untouched."""
    import mirror.plugin

    registry_before = dict(mirror.plugin._registry)

    # Write config with a non-empty plugins dict (different from current empty state).
    new_cfg = _make_config(
        reload_env["tmp_path"],
        reload_env["initial_pkgs"],
    )
    new_cfg["settings"]["plugins"] = {"fake-plugin": {"enabled": True, "config": {}}}
    reload_env["config_path"].write_text(json.dumps(new_cfg))

    result = mirror.config._perform_reload()

    warned = " ".join(result.get("warnings", []))
    assert "plugins" in warned
    # Registry must be unchanged.
    assert mirror.plugin._registry == registry_before


# ---------------------------------------------------------------------------
# Test 7: kills removed in-flight package
# ---------------------------------------------------------------------------

def test_perform_reload_kills_removed_inflight(reload_env, monkeypatch):
    """Removed pkg with in-flight sync → stop_command called, killed_inflight set."""
    # Mark pkg-one as in-flight via package status.
    pkg_one = mirror.packages.get("pkg-one")
    pkg_one.set_status("SYNC")

    stop_calls: list[str] = []

    def _fake_stop_command(job_id: str):
        stop_calls.append(job_id)
        # Simulate job finishing: transition package to ACTIVE so the poll loop exits.
        p = mirror.packages.get(job_id)
        if p is not None:
            p.set_status("ACTIVE")

    def _fake_get_progress(job_id: str):
        pass

    monkeypatch.setattr("mirror.socket.worker.stop_command", _fake_stop_command, raising=False)
    monkeypatch.setattr("mirror.socket.worker.get_progress", _fake_get_progress, raising=False)

    # Write config WITHOUT pkg-one.
    _write_config(reload_env, {})

    result = mirror.config._perform_reload()

    assert result["status"] == "ok"
    assert "pkg-one" in stop_calls
    assert "pkg-one" in result.get("killed_inflight", [])
    assert result.get("killed_timeout", []) == []


# ---------------------------------------------------------------------------
# Test 8: in-flight kill timeout
# ---------------------------------------------------------------------------

def test_perform_reload_kills_inflight_timeout(reload_env, monkeypatch):
    """stop_command called but sync never clears → pkg appears in killed_timeout."""
    # Mark pkg-one as in-flight via package status.
    pkg_one = mirror.packages.get("pkg-one")
    pkg_one.set_status("SYNC")

    stop_calls: list[str] = []

    def _fake_stop_command(job_id: str):
        stop_calls.append(job_id)
        # Intentionally do NOT clear status to simulate stuck job.

    def _fake_get_progress(job_id: str):
        pass  # Never changes package status.

    monkeypatch.setattr("mirror.socket.worker.stop_command", _fake_stop_command, raising=False)
    monkeypatch.setattr("mirror.socket.worker.get_progress", _fake_get_progress, raising=False)

    # Shorten the deadline to 1 second so the test runs fast.

    original_time = time.time
    _start = original_time()

    # Patch time.time inside mirror.config to advance 11 seconds immediately
    # after the deadline is set (i.e., the first call in the while-loop body).
    call_count = [0]
    real_time_mod = __import__("time")

    def _fast_time():
        call_count[0] += 1
        # Let the first few calls be real so the deadline is set normally,
        # then jump past it.
        if call_count[0] > 3:
            return real_time_mod.time() + 20.0
        return real_time_mod.time()

    monkeypatch.setattr("mirror.config.time", MagicMock(
        time=_fast_time,
        sleep=lambda s: None,
        monotonic=real_time_mod.monotonic,
    ))

    _write_config(reload_env, {})

    result = mirror.config._perform_reload()

    assert "pkg-one" in stop_calls
    assert "pkg-one" in result.get("killed_timeout", [])
