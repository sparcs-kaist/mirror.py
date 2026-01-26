import os
import sys
import pytest
from pathlib import Path
import mirror.sync

def test_default_methods_detection():
    """기본 sync 디렉토리의 .py 파일들이 메서드 목록에 잘 포함되는지 확인"""
    # _로 시작하지 않는 파일들이 목록에 있어야 함
    assert "rsync" in mirror.sync.methods
    assert "ftpsync" in mirror.sync.methods
    assert "lftp" in mirror.sync.methods
    assert "_ftpsync_script" not in mirror.sync.methods

def test_default_modules_loaded():
    """기본 모듈들이 mirror.sync의 속성으로 실제로 로드되었는지 확인"""
    for method in mirror.sync.methods:
        assert hasattr(mirror.sync, method), f"Module {method} was not loaded into mirror.sync"
        module = getattr(mirror.sync, method)
        # 로드된 객체가 모듈인지 확인 (rsync 등은 execute 함수를 가지고 있어야 함)
        if method == "rsync":
            assert hasattr(module, "execute")

def test_dynamic_loader(tmp_path):
    """loader 함수가 임의의 경로에서 모듈을 올바르게 로드하는지 확인"""
    # 임시 디렉토리에 가짜 sync 모듈 생성
    custom_sync_dir = tmp_path / "custom_sync"
    custom_sync_dir.mkdir()
    
    mock_content = """
def execute(package, logger):
    return "mock_executed"
name = "mock_module"
"""
    mock_file = custom_sync_dir / "mock_sync.py"
    mock_file.write_text(mock_content)
    
    # 로더 실행
    mirror.sync.loader(custom_sync_dir)
    
    # 로드 확인
    assert hasattr(mirror.sync, "mock_sync")
    assert mirror.sync.mock_sync.name == "mock_module"
    assert mirror.sync.mock_sync.execute(None, None) == "mock_executed"

def test_get_module():
    """get_module 함수가 등록된 모듈을 잘 반환하는지 확인"""
    rsync_mod = mirror.sync.get_module("rsync")
    assert rsync_mod is not None
    assert rsync_mod.__name__.endswith("rsync")
