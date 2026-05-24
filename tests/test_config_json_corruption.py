"""Tests for JSON-corruption guards in mirror/config/__init__.py."""
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import mirror
import mirror.config
import mirror.structure
import mirror.sync


# ---------------------------------------------------------------------------
# Config builder helpers (same shape as test_perform_reload_validation.py)
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


def _valid_config(tmp_path: Path) -> dict:
    return {
        "mirrorname": "TestMirror",
        "hostname": "test.local",
        "settings": _make_settings(tmp_path),
        "packages": {"pkg-alpha": _make_valid_pkg("pkg-alpha")},
    }


# ---------------------------------------------------------------------------
# Shared fixture that resets global state between tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def corruption_env(tmp_path, monkeypatch):
    """Set up file paths and reset mirror globals before each test."""
    config_path = tmp_path / "config.json"
    stat_path = tmp_path / "stat.json"
    status_path = tmp_path / "status.json"
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(mirror.config, "CONFIG_PATH", config_path, raising=False)
    monkeypatch.setattr(mirror.config, "STAT_DATA_PATH", stat_path, raising=False)
    monkeypatch.setattr(mirror.config, "STATUS_PATH", status_path, raising=False)
    monkeypatch.setattr(mirror.config, "SOCKET_PATH", str(tmp_path / "mirror.sock"), raising=False)
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)

    yield {
        "tmp_path": tmp_path,
        "config_path": config_path,
        "stat_path": stat_path,
        "status_path": status_path,
    }


# ---------------------------------------------------------------------------
# Test 1: corrupt config.json raises ValueError
# ---------------------------------------------------------------------------

def test_load_raises_on_corrupt_config_json(corruption_env):
    """mirror.config.load() must raise ValueError when config.json is not valid JSON."""
    config_path: Path = corruption_env["config_path"]
    stat_path: Path = corruption_env["stat_path"]
    status_path: Path = corruption_env["status_path"]

    config_path.write_text("{")
    stat_path.write_text(json.dumps({"packages": {}}))
    status_path.write_text(json.dumps({}))

    with pytest.raises(ValueError) as exc_info:
        mirror.config.load(config_path)

    assert str(config_path) in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 2: corrupt stat.json raises RuntimeError on load
# ---------------------------------------------------------------------------

def test_load_raises_on_corrupt_stat_json(corruption_env):
    """mirror.config.load() must raise RuntimeError when stat.json contains garbage."""
    config_path: Path = corruption_env["config_path"]
    stat_path: Path = corruption_env["stat_path"]
    status_path: Path = corruption_env["status_path"]

    config_path.write_text(json.dumps(_valid_config(corruption_env["tmp_path"])))
    stat_path.write_text("{garbage}")
    status_path.write_text(json.dumps({}))

    with pytest.raises(RuntimeError) as exc_info:
        mirror.config.load(config_path)

    assert str(stat_path) in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 3: zero-byte stat.json also raises RuntimeError
# ---------------------------------------------------------------------------

def test_load_empty_stat_treated_as_corrupt(corruption_env):
    """A zero-byte stat.json must raise RuntimeError, not silently produce empty data."""
    config_path: Path = corruption_env["config_path"]
    stat_path: Path = corruption_env["stat_path"]
    status_path: Path = corruption_env["status_path"]

    config_path.write_text(json.dumps(_valid_config(corruption_env["tmp_path"])))
    stat_path.write_bytes(b"")
    status_path.write_text(json.dumps({}))

    with pytest.raises(RuntimeError) as exc_info:
        mirror.config.load(config_path)

    assert str(stat_path) in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 4: corrupt status.json is tolerated; load succeeds, mirror.status == {}
# ---------------------------------------------------------------------------

def test_load_succeeds_with_corrupt_status_json(corruption_env, caplog):
    """corrupt status.json must not abort load; mirror.status must be set to {}."""
    config_path: Path = corruption_env["config_path"]
    stat_path: Path = corruption_env["stat_path"]
    status_path: Path = corruption_env["status_path"]

    config_path.write_text(json.dumps(_valid_config(corruption_env["tmp_path"])))
    stat_path.write_text(json.dumps({"packages": {}}))
    status_path.write_text("{not_json")

    # Use a real logger so caplog can capture the warning.
    import logging
    real_log = logging.getLogger("mirror")
    monkeypatch_log = MagicMock(wraps=real_log)
    monkeypatch_log.warning = real_log.warning
    mirror.log = monkeypatch_log

    with caplog.at_level(logging.WARNING, logger="mirror"):
        mirror.config.load(config_path)

    assert mirror.status == {}

    corrupt_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "corrupt" in r.message.lower()
    ]
    assert corrupt_warnings, "expected a warning about the corrupt status.json"
    assert str(status_path) in corrupt_warnings[0].message


# ---------------------------------------------------------------------------
# Test 5: _perform_reload returns error dict when stat.json is corrupt
# ---------------------------------------------------------------------------

def test_validate_candidate_packages_raises_on_corrupt_stat(corruption_env, monkeypatch):
    """_perform_reload must return {status: error} when stat.json becomes corrupt mid-run."""
    config_path: Path = corruption_env["config_path"]
    stat_path: Path = corruption_env["stat_path"]
    status_path: Path = corruption_env["status_path"]
    tmp_path: Path = corruption_env["tmp_path"]

    cfg = _valid_config(tmp_path)
    config_path.write_text(json.dumps(cfg))
    stat_path.write_text(json.dumps({"packages": {}}))
    status_path.write_text(json.dumps({}))

    mirror.config.load(config_path)

    # Set up in-memory state the same way val_env does in the reload tests.
    monkeypatch.setattr(mirror.config, "CONFIG_PATH", config_path, raising=False)
    monkeypatch.setattr(mirror.config, "STAT_DATA_PATH", stat_path, raising=False)
    monkeypatch.setattr(mirror.config, "STATUS_PATH", status_path, raising=False)
    monkeypatch.setattr(mirror.config, "SOCKET_PATH", str(tmp_path / "mirror.sock"), raising=False)

    mirror.conf = mirror.structure.Config.load_from_dict(cfg)
    mirror.packages = mirror.structure.Packages(
        {
            "pkg-alpha": {
                **_make_valid_pkg("pkg-alpha"),
                "status": {"status": "UNKNOWN", "statusinfo": {"errorcount": 0}},
            }
        }
    )

    with mirror.sync._start_lock:
        mirror.sync._extra_args.clear()
        mirror.sync._watchdog_fired.clear()

    # Corrupt stat.json after initial load.
    stat_path.write_bytes(b"")

    result = mirror.config._perform_reload()

    assert result["status"] == "error"
    assert str(stat_path) in result.get("error", "")
