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
# Shared fixture that resets global state between tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def corruption_env(
    tmp_path,
    monkeypatch,
    reload_config_factory,
    reload_package_factory,
):
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
        "valid_config": lambda: reload_config_factory(tmp_path),
        "make_package": reload_package_factory,
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

    config_path.write_text(json.dumps(corruption_env["valid_config"]()))
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

    config_path.write_text(json.dumps(corruption_env["valid_config"]()))
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

    config_path.write_text(json.dumps(corruption_env["valid_config"]()))
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

    cfg = corruption_env["valid_config"]()
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
                **corruption_env["make_package"]("pkg-alpha"),
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
