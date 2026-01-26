import pytest
import json
import os
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가하여 mirror 모듈을 임포트할 수 있게 함
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import mirror
import mirror.config
import mirror.structure

# Mocking Dependencies
@pytest.fixture(autouse=True)
def mock_dependencies():
    """
    mirror 패키지의 외부 의존성들을 Mocking합니다.
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

    # 2. Mock Toolbox (특히 iso_duration_parser)
    # config-example.json에는 ""와 "PUSH" 같은 비표준 값이 포함되어 있어
    # 실제 parser를 쓰면 에러가 날 수 있음.
    original_parser = None
    if hasattr(mirror, 'toolbox') and hasattr(mirror.toolbox, 'iso_duration_parser'):
        original_parser = mirror.toolbox.iso_duration_parser
        
    class MockToolbox:
        def iso_duration_parser(self, duration_str):
            if duration_str == "":
                return 0
            if duration_str == "PUSH":
                return -1 # PUSH는 특별한 값으로 취급
            # 그 외에는 간단한 파싱 또는 실제 parser 호출 시도 (여기선 간단히 처리)
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
    # 로드될 때 synctype 검사를 통과하기 위해
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
    config-example.json을 읽고 테스트 환경에 맞게 경로를 수정하여 임시 파일로 저장합니다.
    """
    root_dir = Path(__file__).parent.parent
    example_config_path = root_dir / 'config-example.json'
    
    if not example_config_path.exists():
        pytest.fail(f"config-example.json not found at {example_config_path}")

    content = json.loads(example_config_path.read_text())

    # 경로 수정 (테스트용 임시 디렉터리로)
    content['settings']['logfolder'] = str(tmp_path / 'logs')
    content['settings']['webroot'] = str(tmp_path / 'webroot')
    content['settings']['statusfile'] = str(tmp_path / 'status.json')
    # config-example.json에는 statfile이 없으므로 추가
    content['settings']['statfile'] = str(tmp_path / 'stat.json')

    # 필요한 디렉터리 생성은 mirror.config.load가 알아서 하거나 로직에 맡김
    # 다만 statfile은 존재해야 로드 로직이 원활히 돌 수 있음 (초기값 생성)
    (tmp_path / 'stat.json').write_text(json.dumps({"packages": {}}))
    (tmp_path / 'status.json').write_text(json.dumps({}))

    test_config_path = tmp_path / 'config.json'
    test_config_path.write_text(json.dumps(content, indent=4))

    return test_config_path, content

def test_load_config_example(setup_example_env):
    """
    config-example.json 파일을 기반으로 설정 로드가 정상적으로 되는지 테스트합니다.
    """
    config_path, expected_content = setup_example_env

    # 설정 로드 실행
    mirror.config.load(config_path)

    # 1. 기본 설정 검증
    assert mirror.conf.name == expected_content['mirrorname']
    assert mirror.conf.hostname == expected_content['hostname']
    assert str(mirror.conf.logfolder) == expected_content['settings']['logfolder']
    
    # 2. 패키지 로드 검증
    loaded_packages = mirror.packages.keys()
    expected_packages = expected_content['packages'].keys()
    
    # 모든 패키지가 로드되었는지 확인
    for pkg_id in expected_packages:
        assert pkg_id in loaded_packages

    # 3. 개별 패키지 속성 검증
    
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
    로드된 설정(mirror.packages)을 다시 JSON(dict)으로 변환했을 때,
    원본 입력(config-example.json)의 packages 섹션과 일치하는지 검증합니다.
    """
    config_path, expected_content = setup_example_env
    mirror.config.load(config_path)

    # 1. mirror.packages를 딕셔너리로 변환
    # mirror.packages.to_dict()는 {pkg_id: pkg_dict, ...} 형태를 반환함
    exported_packages = mirror.packages.to_dict()

    # 2. 비교 대상 준비 (config-example.json의 packages 섹션)
    expected_packages = expected_content['packages']

    # 3. 차이점 비교
    # 주의: geoul 패키지의 경우 원본 파일에 'options'가 없으면 로드 후에는 생길 수 있음.
    # 하지만 사용자가 제공한 레퍼런스에는 options가 있다고 가정하거나,
    # 코드에서 default_factory로 생성된 options를 감안해야 함.
    
    # 여기서는 JSON 구조의 동등성을 비교합니다.
    # 순서가 다를 수 있으므로 dict 비교를 사용하면 안전합니다.
    
    # exported_packages에는 'lastsync', 'errorcount', 'status' 등의 런타임 필드가 추가될 수 있음.
    # config-example.json에는 이것들이 없으므로 제거하거나 무시해야 함.
    
    # 하지만 mirror.structure.Package.to_dict() 구현을 보면:
    # package_dict = asdict(self) ...
    # Package 클래스 필드들이 모두 포함됨 (status, lastsync, errorcount 등).
    
    # 따라서, 비교를 위해서는 exported_packages에서 런타임 전용 필드를 제외하거나
    # expected_packages에 해당 필드의 기본값을 채워넣어야 함.
    
    for pkg_id, pkg_data in exported_packages.items():
        assert pkg_id in expected_packages
        expected_pkg = expected_packages[pkg_id]
        
        # 런타임 필드 검증 제외 또는 값 보정
        # Config에서 로드할 때 기본값: status="UNKNOWN", lastsync=0.0, errorcount=0
        # Package.from_dict에서 이 값들을 설정함.
        
        # 원본 데이터(expected_pkg)에는 이 필드들이 없을 수 있으므로, 비교를 위해 추가해줌
        if "status" not in expected_pkg: expected_pkg["status"] = "UNKNOWN"
        if "lastsync" not in expected_pkg: expected_pkg["lastsync"] = 0.0
        if "errorcount" not in expected_pkg: expected_pkg["errorcount"] = 0
        if "disabled" not in expected_pkg: expected_pkg["disabled"] = False

        # geoul의 options 처리 (없으면 빈 dict로 간주)
        if "options" not in expected_pkg.get("settings", {}):
             # settings가 dict라면 수정
             if "settings" in expected_pkg:
                 expected_pkg["settings"]["options"] = {}
        
        # to_dict() 결과와 예상치 비교
        # assert pkg_data == expected_pkg # 전체 비교
        
        # 디버깅을 위해 상세 비교
        assert pkg_data['name'] == expected_pkg['name']
        assert pkg_data['id'] == expected_pkg['id']
        assert pkg_data['synctype'] == expected_pkg['synctype']
        assert pkg_data['syncrate'] == expected_pkg['syncrate']
        assert pkg_data['href'] == expected_pkg['href']
        
        # Settings 비교
        assert pkg_data['settings']['src'] == expected_pkg['settings']['src']
        assert pkg_data['settings']['dst'] == expected_pkg['settings']['dst']
        assert pkg_data['settings']['hidden'] == expected_pkg['settings']['hidden']
        assert pkg_data['settings']['options'] == expected_pkg['settings'].get('options', {})

        # Link 비교 (리스트 순서는 중요할 수 있음)
        # pkg_data['link']는 [{'rel':..., 'href':...}, ...] 형태
        # expected_pkg['link']도 동일 형태
        assert len(pkg_data['link']) == len(expected_pkg['link'])
        for i, link in enumerate(pkg_data['link']):
             assert link['rel'] == expected_pkg['link'][i]['rel']
             assert link['href'] == expected_pkg['link'][i]['href']

    print("\n[Success] Round-trip JSON conversion validated.")
