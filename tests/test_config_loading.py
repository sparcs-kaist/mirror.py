import os
import json
from pathlib import Path
import pytest

# Add project root path to sys.path (temporary)
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load the actual mirror module for mock objects
# This part must be handled carefully for test isolation.
# For testing similarly to the actual application's behavior.
import mirror
import mirror.config
import mirror.structure
import mirror.logger

# Setup temporary directory for tests
@pytest.fixture(scope="module")
def temp_config_env(tmp_path_factory):
    # Create a temporary root directory for testing
    temp_dir = tmp_path_factory.mktemp("mirror_config_test")
    
    # Set temporary configuration file path
    test_config_path = temp_dir / "config.json"
    test_stat_path = temp_dir / "stat.json"
    test_status_path = temp_dir / "status.json"

    # Test configuration content (simplified based on mirror/config/config.py's DEFAULT_CONFIG)
    dummy_config_content = {
        "mirrorname": "Test Mirror",
        "settings": {
            "logfolder": str(temp_dir / "logs"),
            "webroot": str(temp_dir / "web"),
            "statusfile": str(test_status_path),
            "statfile": str(test_stat_path), # Add statfile
            "gid": 1000,
            "uid": 1000,
            "localtimezone": "Asia/Seoul",
            "maintainer": {
                "name": "Test User",
                "email": "test@example.com"
            },
            "logger": {
                "level": "INFO",
                "packagelevel": "ERROR",
                "format": "[%(asctime)s] %(levelname)s # %(message)s",
                "packageformat": "[%(asctime)s][{package}] %(levelname)s # %(message)s",
                "fileformat": {
                    "base": str(temp_dir / "logs" / "files"),
                    "folder": "{year}/{month}/{day}",
                    "filename": "{hour}:{minute}:{second}.{microsecond}.{packageid}.log",
                    "gzip": True,
                }
            },
            "ftpsync": {
                "maintainer": "Test Maintainer",
                "sponsor": "Test Sponsor",
                "country": "KR",
                "location": "Seoul",
                "throughput": "1G",
                "include": "",
                "exclude": "",
            },
            "plugins": []
        },
        "packages": {
            "test_pkg_1": {
                "name": "Test Package 1",
                "id": "test_pkg_1",
                "href": "/test_pkg_1",
                "synctype": "rsync", # Use previously defined synctype
                "syncrate": "PT1H",
                "link": [
                    {"rel": "HOME", "href": "http://test.example.com"}
                ],
                "settings": {
                    "hidden": False,
                    "src": "rsync://test.src/test_pkg_1",
                    "dst": str(temp_dir / "data" / "test_pkg_1"),
                    "options": {}
                }
            }
        }
    }

    # Create dummy configuration file
    test_config_path.write_text(json.dumps(dummy_config_content, indent=4))
    
    # Create dummy stat file (initial state)
    # Since the load function creates or updates the stat file, leave it empty or with minimal content.
    test_stat_path.write_text(json.dumps({"packages": {}}, indent=4))

    # Create dummy status file (load function will update it)
    test_status_path.write_text(json.dumps({"some_old_status": "data"}, indent=4))


    # Set global variables in mirror.config to point to the test file paths
    # This is similar to how CONFIG_PATH etc. are set in the actual app.
    mirror.config.CONFIG_PATH = test_config_path
    mirror.config.STAT_DATA_PATH = test_stat_path
    mirror.config.STATUS_PATH = test_status_path
    mirror.config.SOCKET_PATH = str(temp_dir / "socket.sock") # dummy socket path

    # Return fixture
    yield {
        "config_path": test_config_path,
        "stat_path": test_stat_path,
        "status_path": test_status_path,
        "dummy_config_content": dummy_config_content,
        "temp_dir": temp_dir
    }

    # Cleanup after fixture ends
    # shutil.rmtree(temp_dir) # tmp_path_factory handles this automatically

def test_mirror_config_loading(temp_config_env):
    config_path = temp_config_env["config_path"]
    stat_path = temp_config_env["stat_path"]
    status_path = temp_config_env["status_path"]
    dummy_config_content = temp_config_env["dummy_config_content"]

    # --- Call config loading function ---
    mirror.config.load(config_path)
    
    # Initialize logger for tests as mirror.config uses it
    mirror.logger.setup_logger()

    # --- Verify mirror.conf ---
    assert mirror.conf.name == dummy_config_content["mirrorname"]
    assert mirror.conf.uid == dummy_config_content["settings"]["uid"]
    assert mirror.conf.gid == dummy_config_content["settings"]["gid"]
    assert Path(mirror.conf.logfolder) == Path(dummy_config_content["settings"]["logfolder"])
    assert Path(mirror.conf.webroot) == Path(dummy_config_content["settings"]["webroot"])
    
    # Verify ftpsync settings
    assert mirror.conf.ftpsync.maintainer == dummy_config_content["settings"]["ftpsync"]["maintainer"]
    
    # --- Verify mirror.packages ---
    assert len(mirror.packages.keys()) == 1
    pkg_id = "test_pkg_1"
    assert pkg_id in mirror.packages.keys()
    
    pkg = getattr(mirror.packages, pkg_id)
    assert pkg.name == dummy_config_content["packages"][pkg_id]["name"]
    assert pkg.href == dummy_config_content["packages"][pkg_id]["href"]
    assert pkg.synctype == dummy_config_content["packages"][pkg_id]["synctype"]
    assert pkg.syncrate == mirror.toolbox.parse_iso_duration(dummy_config_content["packages"][pkg_id]["syncrate"])
    assert pkg.settings.src == dummy_config_content["packages"][pkg_id]["settings"]["src"]
    
    # --- Verify Stat file ---
    loaded_stat = json.loads(stat_path.read_text())
    assert loaded_stat["mirrorname"] == dummy_config_content["mirrorname"]
    assert pkg_id in loaded_stat["packages"]
    assert loaded_stat["packages"][pkg_id]["name"] == dummy_config_content["packages"][pkg_id]["name"]
    assert loaded_stat["packages"][pkg_id]["status"]["status"] == "UNKNOWN" # UNKNOWN on initial load

    # --- Verify Web Status file (check creation and saving) ---
    mirror.config.generate_and_save_web_status()
    loaded_web_status = json.loads(status_path.read_text())
    assert loaded_web_status["mirrorname"] == dummy_config_content["mirrorname"]
    assert pkg_id in loaded_web_status
    assert loaded_web_status[pkg_id]["status"] == "UNKNOWN" # Initialized to UNKNOWN when Config is loaded
    assert "lastupdate" in loaded_web_status

    print("\nConfig loading test passed successfully.")

def test_default_config_has_statfile():
    """DEFAULT_CONFIG must include statfile so setup-time bootstrap works."""
    from mirror.config.config import DEFAULT_CONFIG
    assert "statfile" in DEFAULT_CONFIG["settings"]


def test_default_config_has_uid_gid():
    """DEFAULT_CONFIG must expose runtime user and group IDs."""
    from mirror.config.config import DEFAULT_CONFIG
    assert "uid" in DEFAULT_CONFIG["settings"]
    assert "gid" in DEFAULT_CONFIG["settings"]


def test_socket_path_from_config_overrides_default(tmp_path, monkeypatch):
    """When config sets socket_path, master/worker socket defaults reflect it."""
    import json
    import mirror

    custom_dir = tmp_path / "custom_sockets"
    custom_dir.mkdir()

    cfg = {
        "mirrorname": "TestMirror",
        "hostname": "test.local",
        "settings": {
            "logfolder": str(tmp_path / "logs"),
            "webroot": str(tmp_path / "web"),
            "statusfile": str(tmp_path / "status.json"),
            "statfile": str(tmp_path / "stat.json"),
            "socket_path": str(custom_dir),
            "errorcontinuetime": 60,
            "localtimezone": "UTC",
            "logger": {
                "level": "INFO",
                "format": "[%(asctime)s] %(levelname)s # %(message)s",
                "fileformat": {"base": str(tmp_path / "logs"), "folder": "{year}", "filename": "{day}.log", "gzip": False},
            },
            "plugins": [],
            "ftpsync": {
                "maintainer": "x", "sponsor": "y", "country": "KR",
                "location": "Seoul", "throughput": "1G",
            },
        },
        "packages": {},
    }
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg))

    (tmp_path / "stat.json").write_text(json.dumps({"packages": {}}))
    (tmp_path / "status.json").write_text(json.dumps({}))

    import mirror.config
    try:
        mirror.config.load(cfg_path)
        assert mirror.config.SOCKET_PATH == str(custom_dir)

        from mirror.socket.master import _default_master_socket_path
        from mirror.socket.worker import _default_worker_socket_path
        assert str(_default_master_socket_path()) == str(custom_dir / "master.sock")
        assert str(_default_worker_socket_path()) == str(custom_dir / "worker.sock")
    finally:
        import mirror.config
        # Restore SOCKET_PATH to an unset state so subsequent tests use
        # WorkerServer/MasterServer defaults (mirror.RUN_PATH/...sock).
        if hasattr(mirror.config, "SOCKET_PATH"):
            try:
                del mirror.config.SOCKET_PATH
            except AttributeError:
                pass


def test_load_preserves_runtime_stat_fields(tmp_path, monkeypatch):
    """Config load must preserve lastsync/timestamp while applying config changes."""
    stat_path = tmp_path / "stat.json"
    status_path = tmp_path / "status.json"
    config = {
        "mirrorname": "TestMirror",
        "hostname": "test.local",
        "settings": _make_settings_config({
            "statfile": str(stat_path),
            "statusfile": str(status_path),
            "socket_path": str(tmp_path / "sockets"),
        })["settings"],
        "packages": {
            "pkg1": _make_pkg_config({
                "syncrate": "PT2H",
                "settings": {"hidden": False, "src": "rsync://new/pkg1", "dst": "/tmp/new"},
            })
        },
    }
    stat_path.write_text(json.dumps({
        "packages": {
            "pkg1": {
                **_make_pkg_config({
                    "syncrate": "PT1H",
                    "settings": {"hidden": False, "src": "rsync://old/pkg1", "dst": "/tmp/old"},
                }),
                "lastsync": 1234.5,
                "timestamp": 5678.0,
                "status": {"status": "ACTIVE", "statusinfo": {"errorcount": 0}},
            }
        }
    }))
    status_path.write_text(json.dumps({}))
    monkeypatch.setattr(mirror, "STATE_PATH", tmp_path / "state", raising=False)
    monkeypatch.setattr(mirror, "log", type("Log", (), {"warning": lambda *a, **k: None, "error": lambda *a, **k: None})(), raising=False)

    mirror.config._load_from_dict(config, source_path=tmp_path / "config.json", load_plugins=False)

    pkg = mirror.packages.get("pkg1")
    assert pkg.lastsync == 1234.5
    assert pkg.timestamp == 5678.0
    assert pkg.status == "ACTIVE"
    assert pkg.syncrate == 7200
    assert pkg.settings.src == "rsync://new/pkg1"

    saved = json.loads(stat_path.read_text())["packages"]["pkg1"]
    assert saved["lastsync"] == 1234.5
    assert saved["timestamp"] == 5678.0
    assert saved["settings"]["src"] == "rsync://new/pkg1"


def test_load_promotes_legacy_statusinfo_lastsync(tmp_path, monkeypatch):
    """Legacy status.statusinfo.lastsync is migrated to top-level lastsync."""
    stat_path = tmp_path / "stat.json"
    status_path = tmp_path / "status.json"
    config = {
        "mirrorname": "TestMirror",
        "hostname": "test.local",
        "settings": _make_settings_config({
            "statfile": str(stat_path),
            "statusfile": str(status_path),
        })["settings"],
        "packages": {"pkg1": _make_pkg_config()},
    }
    stat_path.write_text(json.dumps({
        "packages": {
            "pkg1": {
                **_make_pkg_config(),
                "status": {
                    "status": "ACTIVE",
                    "statusinfo": {"errorcount": 0, "lastsync": 4321.0},
                },
            }
        }
    }))
    status_path.write_text(json.dumps({}))
    monkeypatch.setattr(mirror, "STATE_PATH", tmp_path / "state", raising=False)
    monkeypatch.setattr(mirror, "log", type("Log", (), {"warning": lambda *a, **k: None, "error": lambda *a, **k: None})(), raising=False)

    mirror.config._load_from_dict(config, source_path=tmp_path / "config.json", load_plugins=False)

    assert mirror.packages.get("pkg1").lastsync == 4321.0
    saved = json.loads(stat_path.read_text())["packages"]["pkg1"]
    assert saved["lastsync"] == 4321.0


def test_save_stat_data_lastsync_roundtrip(tmp_path, monkeypatch):
    """save_stat_data output must reload without resetting lastsync."""
    stat_path = tmp_path / "stat.json"
    status_path = tmp_path / "status.json"
    config = {
        "mirrorname": "TestMirror",
        "hostname": "test.local",
        "settings": _make_settings_config({
            "statfile": str(stat_path),
            "statusfile": str(status_path),
        })["settings"],
        "packages": {"pkg1": _make_pkg_config()},
    }
    stat_path.write_text(json.dumps({"packages": {}}))
    status_path.write_text(json.dumps({}))
    monkeypatch.setattr(mirror, "STATE_PATH", tmp_path / "state", raising=False)
    monkeypatch.setattr(mirror, "log", type("Log", (), {"warning": lambda *a, **k: None, "error": lambda *a, **k: None})(), raising=False)

    mirror.config._load_from_dict(config, source_path=tmp_path / "config.json", load_plugins=False)
    mirror.packages.get("pkg1").lastsync = 9876.5
    mirror.config.save_stat_data()
    mirror.config._load_from_dict(config, source_path=tmp_path / "config.json", load_plugins=False)

    assert mirror.packages.get("pkg1").lastsync == 9876.5


def test_package_disabled_field_loads_from_config(tmp_path, monkeypatch):
    """Package.disabled is config-owned and should be reflected in memory."""
    stat_path = tmp_path / "stat.json"
    status_path = tmp_path / "status.json"
    config = {
        "mirrorname": "TestMirror",
        "hostname": "test.local",
        "settings": _make_settings_config({
            "statfile": str(stat_path),
            "statusfile": str(status_path),
        })["settings"],
        "packages": {"pkg1": _make_pkg_config({"disabled": True})},
    }
    stat_path.write_text(json.dumps({"packages": {}}))
    status_path.write_text(json.dumps({}))
    monkeypatch.setattr(mirror, "STATE_PATH", tmp_path / "state", raising=False)
    monkeypatch.setattr(mirror, "log", type("Log", (), {"warning": lambda *a, **k: None, "error": lambda *a, **k: None})(), raising=False)

    mirror.config._load_from_dict(config, source_path=tmp_path / "config.json", load_plugins=False)

    assert mirror.packages.get("pkg1").is_disabled() is True
    saved = json.loads(stat_path.read_text())["packages"]["pkg1"]
    assert "disabled" not in saved


def test_package_to_dict_includes_disabled(tmp_path, monkeypatch):
    """Package.to_dict must carry disabled so the RPC payload preserves it."""
    stat_path = tmp_path / "stat.json"
    status_path = tmp_path / "status.json"
    config = {
        "mirrorname": "TestMirror",
        "hostname": "test.local",
        "settings": _make_settings_config({
            "statfile": str(stat_path),
            "statusfile": str(status_path),
        })["settings"],
        "packages": {
            "pkg1": _make_pkg_config({"disabled": True}),
            "pkg2": _make_pkg_config({"id": "pkg2"}),
        },
    }
    stat_path.write_text(json.dumps({"packages": {}}))
    status_path.write_text(json.dumps({}))
    monkeypatch.setattr(mirror, "STATE_PATH", tmp_path / "state", raising=False)
    monkeypatch.setattr(mirror, "log", type("Log", (), {"warning": lambda *a, **k: None, "error": lambda *a, **k: None})(), raising=False)

    mirror.config._load_from_dict(config, source_path=tmp_path / "config.json", load_plugins=False)

    assert mirror.packages.get("pkg1").to_dict()["disabled"] is True
    assert mirror.packages.get("pkg2").to_dict()["disabled"] is False


def test_save_stat_data_omits_disabled(tmp_path, monkeypatch):
    """disabled is config-owned and should not be persisted as runtime stat."""
    stat_path = tmp_path / "stat.json"
    status_path = tmp_path / "status.json"
    config = {
        "mirrorname": "TestMirror",
        "hostname": "test.local",
        "settings": _make_settings_config({
            "statfile": str(stat_path),
            "statusfile": str(status_path),
        })["settings"],
        "packages": {"pkg1": _make_pkg_config({"disabled": True})},
    }
    stat_path.write_text(json.dumps({"packages": {}}))
    status_path.write_text(json.dumps({}))
    monkeypatch.setattr(mirror, "STATE_PATH", tmp_path / "state", raising=False)
    monkeypatch.setattr(mirror, "log", type("Log", (), {"warning": lambda *a, **k: None, "error": lambda *a, **k: None})(), raising=False)

    mirror.config._load_from_dict(config, source_path=tmp_path / "config.json", load_plugins=False)
    mirror.config.save_stat_data()

    saved = json.loads(stat_path.read_text())["packages"]["pkg1"]
    assert "disabled" not in saved
    assert mirror.packages.get("pkg1").is_disabled() is True


def _make_pkg_config(extra: dict = None) -> dict:
    cfg = {
        "id": "pkg1",
        "name": "Pkg 1",
        "href": "/pkg1",
        "synctype": "rsync",
        "syncrate": "PT1H",
        "link": [],
        "settings": {"hidden": False, "src": "rsync://example.com/pkg1", "dst": "/tmp/pkg1"},
    }
    if extra:
        cfg.update(extra)
    return cfg


def _make_settings_config(extra: dict = None) -> dict:
    settings = {
        "logfolder": "/tmp/mirror_logs",
        "webroot": "/tmp/mirror_web",
        "statusfile": "/tmp/mirror_status.json",
        "localtimezone": "Asia/Seoul",
        "ftpsync": {
            "maintainer": "X",
            "sponsor": "Y",
            "country": "KR",
            "location": "Daejeon",
            "throughput": "1G",
        },
        "logger": {},
    }
    if extra:
        settings.update(extra)
    return {"mirrorname": "M", "hostname": "h", "settings": settings}


def test_max_runtime_parses_to_seconds():
    """settings.max_runtime = PT12H yields conf.max_runtime_seconds == 43200."""
    import mirror.structure
    conf = mirror.structure.Config.load_from_dict(
        _make_settings_config({"max_runtime": "PT12H"})
    )
    assert conf.max_runtime_seconds == 43200


def test_max_runtime_missing_defaults_to_zero():
    """settings without max_runtime yields conf.max_runtime_seconds == 0."""
    import mirror.structure
    conf = mirror.structure.Config.load_from_dict(_make_settings_config())
    assert conf.max_runtime_seconds == 0


def test_max_runtime_below_6h_emits_warning(caplog):
    """A global max_runtime below 6h triggers a warning recommending 12h+."""
    import logging
    import mirror.structure

    with caplog.at_level(logging.WARNING, logger="mirror"):
        mirror.structure.Config.load_from_dict(
            _make_settings_config({"max_runtime": "PT3H"})
        )

    matched = [r for r in caplog.records if r.levelno == logging.WARNING and "max_runtime" in r.message]
    assert matched, "expected a warning about max_runtime below 6h"
    assert "below 6h" in matched[0].message
    assert "12h or more" in matched[0].message


def test_max_runtime_at_or_above_6h_is_silent(caplog):
    """settings.max_runtime >= 6h must not emit a warning."""
    import logging
    import mirror.structure

    for duration in ("PT6H", "PT12H", "P1D"):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="mirror"):
            mirror.structure.Config.load_from_dict(
                _make_settings_config({"max_runtime": duration})
            )
        matched = [r for r in caplog.records if "max_runtime" in r.message]
        assert not matched, f"expected no warning for max_runtime={duration}, got {matched}"


def test_max_runtime_zero_is_silent(caplog):
    """settings.max_runtime unset (==0) must not emit the watchdog warning."""
    import logging
    import mirror.structure

    with caplog.at_level(logging.WARNING, logger="mirror"):
        mirror.structure.Config.load_from_dict(_make_settings_config())

    matched = [r for r in caplog.records if "max_runtime" in r.message]
    assert not matched


# Define mirror.toolbox.parse_iso_duration temporarily as it's needed (in case the actual module is not loaded)
if not hasattr(mirror, 'toolbox') or not hasattr(mirror.toolbox, 'parse_iso_duration'):
    class MockToolbox:
        def parse_iso_duration(self, duration_str):
            # Handle simple PT1H -> 3600 (seconds) conversion only
            if duration_str == "PT1H":
                return 3600
            return 0
        def format_iso_duration(self, seconds):
            if seconds == 3600:
                return "PT1H"
            return ""
    mirror.toolbox = MockToolbox()

# Replace functions in mirror.logger module with mock functions
if hasattr(mirror, 'logger'):
    mirror.logger.info = lambda msg: None
    mirror.logger.warning = lambda msg: None
    mirror.logger.error = lambda msg: None
else:
    # If mirror.logger is not yet loaded, create a dummy class
    class MockLoggerModule:
        def info(self, msg): pass
        def warning(self, msg): pass
        def error(self, msg): pass
    mirror.logger = MockLoggerModule()

# Initialize mirror.conf, mirror.packages (ensure initial state for each test run)
# This might not be necessary due to pytest fixtures, but for safety.
mirror.conf = None
mirror.packages = None
