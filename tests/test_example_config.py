import pytest
import json
import os
import sys
from pathlib import Path

# Add project root to sys.path to allow importing the mirror module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import mirror
import mirror.config
import mirror.structure

# Mocking Dependencies
@pytest.fixture(autouse=True)
def mock_dependencies():
    """
    Mocks external dependencies of the mirror package.
    """
    # 1. Mock Logger
    if hasattr(mirror, 'logger'):
        mirror.logger.info = lambda x: None
        mirror.logger.warning = lambda x: None
        mirror.logger.error = lambda x: None
    else:
        class MockLogger:
            def info(self, msg): pass
            def warning(self, msg): pass
            def error(self, msg): pass
        mirror.logger = MockLogger()

    # 2. Mock Toolbox (especially iso_duration_parser)
    # config-example.json contains non-standard values like "" and "PUSH"
    # Actual parser might raise errors.
    original_parser = None
    if hasattr(mirror, 'toolbox') and hasattr(mirror.toolbox, 'iso_duration_parser'):
        original_parser = mirror.toolbox.iso_duration_parser
        
    class MockToolbox:
        def iso_duration_parser(self, duration_str):
            if duration_str == "":
                return 0
            if duration_str == "PUSH":
                return -1 # PUSH is treated as a special value
            # Otherwise, attempt simple parsing or call actual parser (handle simply here)
            # PT10M -> 600
            if duration_str == "PT10M":
                return 600
            return 0
            
        def iso_duration_maker(self, seconds):
            if seconds == -1:
                return "PUSH"
            if seconds == 0:
                return ""
            if seconds == 600:
                return "PT10M"
            return ""
            
    mirror.toolbox = MockToolbox()

    # 3. Mock Sync Methods
    # To pass the synctype check when loaded
    if not hasattr(mirror, 'sync'):
        class MockSync: pass
        mirror.sync = MockSync()
    
    mirror.sync.methods = ['local', 'ftpsync', 'rsync']

    yield

    # Teardown (optional restoration)
    # if original_parser: mirror.toolbox.iso_duration_parser = original_parser

@pytest.fixture
def setup_example_env(tmp_path):
    """
    Reads config-example.json, modifies paths for the test environment, and saves it as a temporary file.
    """
    root_dir = Path(__file__).parent.parent
    example_config_path = root_dir / 'config-example.json'
    
    if not example_config_path.exists():
        pytest.fail(f"config-example.json not found at {example_config_path}")

    content = json.loads(example_config_path.read_text())

    # Modify paths (to a temporary directory for testing)
    content['settings']['logfolder'] = str(tmp_path / 'logs')
    content['settings']['webroot'] = str(tmp_path / 'webroot')
    content['settings']['statusfile'] = str(tmp_path / 'status.json')
    # Add statfile as it's missing in config-example.json
    content['settings']['statfile'] = str(tmp_path / 'stat.json')

    # Directory creation is handled by mirror.config.load or logic.
    # However, the statfile must exist for the load logic to run smoothly (initial value creation).
    (tmp_path / 'stat.json').write_text(json.dumps({"packages": {}}))
    (tmp_path / 'status.json').write_text(json.dumps({}))

    test_config_path = tmp_path / 'config.json'
    test_config_path.write_text(json.dumps(content, indent=4))

    return test_config_path, content

def test_load_config_example(setup_example_env):
    """
    Tests if config-example.json is loaded correctly based on its content.
    """
    config_path, expected_content = setup_example_env

    # Execute config load
    mirror.config.load(config_path)

    # 1. Verify basic settings
    assert mirror.conf.name == expected_content['mirrorname']
    assert mirror.conf.hostname == expected_content['hostname']
    assert str(mirror.conf.logfolder) == expected_content['settings']['logfolder']
    
    # 2. Verify package loading
    loaded_packages = mirror.packages.keys()
    expected_packages = expected_content['packages'].keys()
    
    # Check if all packages are loaded
    for pkg_id in expected_packages:
        assert pkg_id in loaded_packages

    # 3. Verify individual package attributes
    
    # - Geoul (synctype: local, syncrate: "")
    geoul = getattr(mirror.packages, 'geoul')
    assert geoul.name == "Geoul"
    assert geoul.synctype == "local"
    assert geoul.syncrate == 0 # Mock parser returns 0 for ""
    
    # - Debian (synctype: ftpsync, syncrate: "PUSH")
    debian = getattr(mirror.packages, 'debian')
    assert debian.name == "Debian"
    assert debian.synctype == "ftpsync"
    assert debian.syncrate == -1 # Mock parser returns -1 for "PUSH"
    
    # - Rocky Linux (synctype: rsync, syncrate: "PT10M")
    rocky = getattr(mirror.packages, 'rocky-linux')
    assert rocky.name == "Rocky Linux"
    assert rocky.synctype == "rsync"
    assert rocky.syncrate == 600 # Mock parser returns 600 for "PT10M"
    assert rocky.settings.src == "rsync://msync.rockylinux.org/rocky-linux"
    
    print("\n[Success] config-example.json loaded and validated successfully.")

def test_config_roundtrip(setup_example_env):
    """
    When the loaded configuration (mirror.packages) is converted back to JSON (dict),
    it verifies consistency with the 'packages' section of the original input (config-example.json).
    """
    config_path, expected_content = setup_example_env
    mirror.config.load(config_path)

    # 1. Convert mirror.packages to a dictionary
    # mirror.packages.to_dict() returns a {pkg_id: pkg_dict, ...} format
    exported_packages = mirror.packages.to_dict()

    # 2. Prepare for comparison (packages section of config-example.json)
    expected_packages = expected_content['packages']

    # 3. Compare differences
    # Note: For the 'geoul' package, 'options' might be created after loading if not present in the original file.
    # However, we assume 'options' exists in the provided reference or
    # consider options generated by default_factory in the code.
    
    # Here, we compare the equality of the JSON structure.
    # Dictionary comparison is safe as order might differ.
    
    # 'lastsync', 'errorcount', 'status', etc., runtime fields may be added to exported_packages.
    # These are absent in config-example.json, so they must be removed or ignored.
    
    # However, looking at the implementation of mirror.structure.Package.to_dict():
    # All fields of the Package class are included (status, lastsync, errorcount, etc.).
    
    # Therefore, for comparison, runtime-specific fields must be excluded from exported_packages or
    # these fields might be missing in the original data (expected_pkg), so they are added for comparison.
    
    for pkg_id, pkg_data in exported_packages.items():
        assert pkg_id in expected_packages
        expected_pkg = expected_content['packages'][pkg_id] # Use original expected_pkg for modification
        
        # Add runtime fields to expected_pkg for comparison
        if "status" not in expected_pkg: expected_pkg["status"] = "UNKNOWN"
        if "lastsync" not in expected_pkg: expected_pkg["lastsync"] = 0.0
        if "errorcount" not in expected_pkg: expected_pkg["errorcount"] = 0
        if "disabled" not in expected_pkg: expected_pkg["disabled"] = False

        # Handle geoul's options (consider as empty dict if absent)
        if "options" not in expected_pkg.get("settings", {}):
             # Modify if settings is a dict
             if "settings" in expected_pkg:
                 expected_pkg["settings"]["options"] = {}
        
        # Special handling for debian's auth in settings.options
        if pkg_id == "debian" and "auth" in expected_pkg.get("settings", {}):
            auth_val = expected_pkg["settings"].pop("auth")
            if "options" not in expected_pkg["settings"]:
                expected_pkg["settings"]["options"] = {}
            expected_pkg["settings"]["options"]["auth"] = auth_val
        
        # Compare to_dict() result with expected values
        # assert pkg_data == expected_pkg # Full comparison
        
        # Detailed comparison for debugging
        assert pkg_data['name'] == expected_pkg['name']
        assert pkg_data['id'] == expected_pkg['id']
        assert pkg_data['synctype'] == expected_pkg['synctype']
        assert pkg_data['syncrate'] == expected_pkg['syncrate']
        assert pkg_data['href'] == expected_pkg['href']
        
        # Settings comparison
        assert pkg_data['settings']['src'] == expected_pkg['settings']['src']
        assert pkg_data['settings']['dst'] == expected_pkg['settings']['dst']
        assert pkg_data['settings']['hidden'] == expected_pkg['settings']['hidden']
        assert pkg_data['settings']['options'] == expected_pkg.get('settings', {}).get('options', {})

        # Link comparison (list order might be important)
        # pkg_data['link'] is in the format [{'rel':..., 'href':...}, ...]
        # expected_pkg['link'] is also in the same format
        assert len(pkg_data['link']) == len(expected_pkg['link'])
        for i, link in enumerate(pkg_data['link']):
             assert link['rel'] == expected_pkg['link'][i]['rel']
             assert link['href'] == expected_pkg['link'][i]['href']

    print("\n[Success] Round-trip JSON conversion validated.")
