import os
import json
import shutil
from pathlib import Path
import pytest
import datetime

# Add project root path to sys.path (temporary)
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load the actual mirror module for mock objects
# This part must be handled carefully for test isolation.
# For testing similarly to the actual application's behavior.
import mirror
import mirror.config
import mirror.structure

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
    assert pkg.syncrate == mirror.toolbox.iso_duration_parser(dummy_config_content["packages"][pkg_id]["syncrate"]) # Compare parsed values
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

# Define mirror.toolbox.iso_duration_parser temporarily as it's needed (in case the actual module is not loaded)
if not hasattr(mirror, 'toolbox') or not hasattr(mirror.toolbox, 'iso_duration_parser'):
    class MockToolbox:
        def iso_duration_parser(self, duration_str):
            # Handle simple PT1H -> 3600 (seconds) conversion only
            if duration_str == "PT1H":
                return 3600
            return 0 # Assume other values are 0 for now
        def iso_duration_maker(self, seconds):
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
