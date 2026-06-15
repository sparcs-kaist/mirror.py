"""Tests for _perform_reload socket restart-only behavior.

settings.socket is bound once at daemon startup and must never be updated
live. A changed socket block must produce a warning and leave mirror.conf.socket
unchanged.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import mirror
import mirror.config
import mirror.structure
import mirror.sync


# ---------------------------------------------------------------------------
# Config builder helpers (mirrors test_perform_reload_diff.py style)
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


def _make_pkg_entry(pkgid: str) -> dict:
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


def _make_config(tmp_path: Path, packages: dict, socket: dict | None = None, **settings_overrides) -> dict:
    settings = _make_settings(tmp_path, **settings_overrides)
    if socket is not None:
        settings["socket"] = socket
    return {
        "mirrorname": "TestMirror",
        "hostname": "test.local",
        "settings": settings,
        "packages": packages,
    }


# ---------------------------------------------------------------------------
# Per-test fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def socket_env(tmp_path, monkeypatch):
    """Initialize mirror state with a config that has a socket block."""
    import sys as _sys
    _real_toolbox = _sys.modules["mirror.toolbox"]
    monkeypatch.setattr(mirror, "toolbox", _real_toolbox, raising=False)

    config_path = tmp_path / "config.json"
    stat_path = tmp_path / "stat.json"
    status_path = tmp_path / "status.json"
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    initial_socket = {"gid": 1000, "mode": "0770"}
    initial_pkgs = {"pkg-one": _make_pkg_entry("pkg-one")}
    initial_cfg = _make_config(tmp_path, initial_pkgs, socket=initial_socket)

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
            "pkg-one": {
                **_make_pkg_entry("pkg-one"),
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
        "initial_pkgs": initial_pkgs,
        "initial_socket": initial_socket,
    }

    with mirror.sync._start_lock:
        mirror.sync._extra_args.clear()
        mirror.sync._watchdog_fired.clear()


# ---------------------------------------------------------------------------
# Test 1: changed socket block → warning emitted, live socket unchanged
# ---------------------------------------------------------------------------

def test_reload_socket_change_warns_and_keeps_current(socket_env):
    """Changing settings.socket emits a restart-required warning and the live
    socket settings are not updated."""
    config_path: Path = socket_env["config_path"]
    tmp_path: Path = socket_env["tmp_path"]
    initial_pkgs = socket_env["initial_pkgs"]

    # Confirm initial state was loaded correctly.
    assert mirror.conf.socket.gid == 1000
    assert mirror.conf.socket.mode == 0o770

    # Rewrite config with a different socket block.
    new_cfg = _make_config(
        tmp_path,
        initial_pkgs,
        socket={"gid": 2000, "mode": "0750"},
    )
    config_path.write_text(json.dumps(new_cfg))

    result = mirror.config._perform_reload()

    assert result["status"] == "ok"

    warned = " ".join(result.get("warnings", []))
    assert "socket change requires daemon restart" in warned

    # Live socket must NOT have changed.
    assert mirror.conf.socket.gid == 1000
    assert mirror.conf.socket.mode == 0o770


# ---------------------------------------------------------------------------
# Test 2: same socket block → no socket-related warning
# ---------------------------------------------------------------------------

def test_reload_same_socket_no_warning(socket_env):
    """Reloading with the identical socket settings produces no socket warning."""
    config_path: Path = socket_env["config_path"]
    tmp_path: Path = socket_env["tmp_path"]
    initial_pkgs = socket_env["initial_pkgs"]

    # Write the same socket settings that are already live.
    new_cfg = _make_config(
        tmp_path,
        initial_pkgs,
        socket={"gid": 1000, "mode": "0770"},
    )
    config_path.write_text(json.dumps(new_cfg))

    result = mirror.config._perform_reload()

    assert result["status"] == "ok"

    warned = " ".join(result.get("warnings", []))
    assert "socket change requires daemon restart" not in warned
