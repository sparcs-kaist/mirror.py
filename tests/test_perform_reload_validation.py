"""Tests for _perform_reload validation: malformed/invalid config returns error without state mutation."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import mirror
import mirror.config
import mirror.structure
import mirror.sync


# ---------------------------------------------------------------------------
# Config builder helpers (same shape as test_perform_reload_diff.py)
# ---------------------------------------------------------------------------

def _make_settings(tmp_path: Path) -> dict:
    return {
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


def _make_valid_pkg(pkgid: str) -> dict:
    return {
        "id": pkgid,
        "name": pkgid,
        "href": f"/{pkgid}",
        "synctype": "rsync",
        "syncrate": "PT1H",
        "link": [],
        "settings": {
            "hidden": False,
            "src": "rsync://src/a",
            "dst": "/tmp/" + pkgid,
            "options": {},
        },
    }


def _valid_config(tmp_path: Path, packages: dict | None = None) -> dict:
    return {
        "mirrorname": "TestMirror",
        "hostname": "test.local",
        "settings": _make_settings(tmp_path),
        "packages": packages or {"pkg-alpha": _make_valid_pkg("pkg-alpha")},
    }


# ---------------------------------------------------------------------------
# Per-test fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def val_env(tmp_path, monkeypatch):
    """Seed mirror.config globals with a single known package and clean paths."""
    config_path = tmp_path / "config.json"
    stat_path = tmp_path / "stat.json"
    status_path = tmp_path / "status.json"
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    initial_cfg = _valid_config(tmp_path)
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
            "pkg-alpha": {
                **_make_valid_pkg("pkg-alpha"),
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
# Test 1: malformed JSON → error, no state change
# ---------------------------------------------------------------------------

def test_perform_reload_malformed_json_returns_error(val_env):
    """Garbage config.json → _perform_reload returns error without mutating state."""
    config_path: Path = val_env["config_path"]
    stat_path: Path = val_env["stat_path"]

    # Snapshot before.
    pkg_keys_before = list(mirror.packages.keys())
    stat_before = stat_path.read_text()

    # Write garbage.
    config_path.write_text("{ this is not valid json !!!}")

    result = mirror.config._perform_reload()

    assert result["status"] == "error"
    assert "error" in result

    # State must be unchanged.
    assert list(mirror.packages.keys()) == pkg_keys_before
    assert stat_path.read_text() == stat_before


# ---------------------------------------------------------------------------
# Test 2: valid JSON but missing "settings" key → error, no state change
# ---------------------------------------------------------------------------

def test_perform_reload_missing_settings_block_returns_error(val_env):
    """Config missing 'settings' dict → error, state unchanged."""
    config_path: Path = val_env["config_path"]
    stat_path: Path = val_env["stat_path"]

    pkg_keys_before = list(mirror.packages.keys())
    stat_before = stat_path.read_text()

    bad_cfg = {
        "mirrorname": "TestMirror",
        # "settings" is intentionally absent.
        "packages": {"pkg-alpha": _make_valid_pkg("pkg-alpha")},
    }
    config_path.write_text(json.dumps(bad_cfg))

    result = mirror.config._perform_reload()

    assert result["status"] == "error"
    assert "error" in result

    assert list(mirror.packages.keys()) == pkg_keys_before
    assert stat_path.read_text() == stat_before


# ---------------------------------------------------------------------------
# Test 3: reserved pkgid → error, no state change
# ---------------------------------------------------------------------------


def test_perform_reload_invalid_pkgid_returns_error(val_env):
    """Config with a reserved pkgid (starts with '_') → error, state unchanged."""
    config_path: Path = val_env["config_path"]
    stat_path: Path = val_env["stat_path"]

    pkg_keys_before = list(mirror.packages.keys())
    stat_before = stat_path.read_text()

    bad_cfg = _valid_config(
        val_env["tmp_path"],
        packages={"_priv": _make_valid_pkg("_priv")},
    )
    # _priv starts with '_' → Packages._validate_id will reject it.
    bad_cfg["packages"]["_priv"]["id"] = "_priv"
    config_path.write_text(json.dumps(bad_cfg))

    result = mirror.config._perform_reload()

    assert result["status"] == "error"
    assert "error" in result

    assert list(mirror.packages.keys()) == pkg_keys_before
    assert stat_path.read_text() == stat_before


# ---------------------------------------------------------------------------
# Test 4: invalid synctype → error, stat file byte-identical (no write)
# ---------------------------------------------------------------------------

def test_perform_reload_invalid_synctype_no_state_change(val_env):
    """Config with an invalid synctype → error returned, packages and stat unchanged."""
    config_path: Path = val_env["config_path"]
    stat_path: Path = val_env["stat_path"]

    pkg_keys_before = list(mirror.packages.keys())
    pkg_state_before = {
        pkgid: mirror.packages.get(pkgid).to_dict() for pkgid in pkg_keys_before
    }
    stat_before = stat_path.read_bytes()

    bad_pkg = _make_valid_pkg("pkg-bad")
    bad_pkg["synctype"] = "nonexistent_method"

    bad_cfg = _valid_config(
        val_env["tmp_path"],
        packages={"pkg-bad": bad_pkg},
    )
    config_path.write_text(json.dumps(bad_cfg))

    result = mirror.config._perform_reload()

    assert result["status"] == "error"
    assert "error" in result
    assert "valid" in result["error"].lower() or "sync" in result["error"].lower()

    # In-memory package set AND full per-package state must be unchanged.
    assert list(mirror.packages.keys()) == pkg_keys_before
    pkg_state_after = {
        pkgid: mirror.packages.get(pkgid).to_dict() for pkgid in pkg_keys_before
    }
    assert pkg_state_after == pkg_state_before
    # stat.json must be byte-identical (no write occurred).
    assert stat_path.read_bytes() == stat_before
