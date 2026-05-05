import os
import sys
import pytest
from pathlib import Path
import mirror
import mirror.sync

# 전역 상수로 설정
EXPECTED_SYNC_METHODS = {"rsync", "ftpsync"}


@pytest.fixture(autouse=True)
def restore_sync_module_state():
    """Defend against sys.modules contamination from other tests.

    `tests/test_socket.py` (and others) historically swapped
    `sys.modules["mirror.sync"]` with mocks; cleanup was best-effort. We
    re-bind from sys.modules to undo any leftover swap. We avoid calling
    `load_sync_methods()` proactively because that creates fresh module
    objects on `mirror.sync.<name>` while leaving `sys.modules['mirror.sync.<name>']`
    pointing to the older instances — the divergence breaks `mock.patch`
    targets in sibling tests. Only repopulate when `methods` is empty.
    """
    real_sync = sys.modules.get("mirror.sync")
    if real_sync is not None:
        mirror.sync = real_sync
    snapshot = list(mirror.sync.methods)
    if not snapshot:
        mirror.sync.load_sync_methods(mirror.sync.BasicMethodPath)
        snapshot = list(mirror.sync.methods)
    yield
    mirror.sync.methods[:] = snapshot
    sys.modules.pop("mock_sync", None)
    if hasattr(mirror.sync, "mock_sync"):
        delattr(mirror.sync, "mock_sync")

def test_default_methods_detection():
    """Check if .py files in the default sync directory are correctly included in the methods list"""
    # 전역 상수를 사용하여 expected_methods 설정
    expected_methods = EXPECTED_SYNC_METHODS
    actual_methods = set(mirror.sync.methods)
    
    assert expected_methods.issubset(actual_methods), f"Not all expected sync methods loaded. Missing: {expected_methods - actual_methods}"
    assert "_ftpsync_script" not in actual_methods # Ensure private script is not loaded

def test_default_modules_loaded():
    """Check if default modules are actually loaded as attributes of mirror.sync"""
    # EXPECTED_SYNC_METHODS에 있는 모듈만 확인
    for method in EXPECTED_SYNC_METHODS:
        assert hasattr(mirror.sync, method), f"Module {method} was not loaded into mirror.sync"
        module = getattr(mirror.sync, method)
        # Verify if the loaded object is a module (e.g., rsync should have an execute function)
        if method == "rsync":
            assert hasattr(module, "execute")

def test_dynamic_loader(tmp_path):
    """Check if the loader function correctly loads modules from an arbitrary path"""
    # Create a fake sync module in a temporary directory
    custom_sync_dir = tmp_path / "custom_sync"
    custom_sync_dir.mkdir()
    
    mock_content = """
def execute(package, logger):
    return "mock_executed"
name = "mock_module"
"""
    mock_file = custom_sync_dir / "mock_sync.py"
    mock_file.write_text(mock_content)
    
    # Execute loader
    mirror.sync.load_sync_methods(custom_sync_dir)
    
    # Check loading
    assert hasattr(mirror.sync, "mock_sync")
    assert mirror.sync.mock_sync.name == "mock_module"
    assert mirror.sync.mock_sync.execute(None, None) == "mock_executed"

def test_get_module():
    """Check if the get_module function correctly returns registered modules"""
    rsync_mod = mirror.sync.get_module("rsync")
    assert rsync_mod is not None
    assert rsync_mod.__name__.endswith("rsync")
