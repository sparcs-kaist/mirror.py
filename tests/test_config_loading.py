import os
import json
import shutil
from pathlib import Path
import pytest
import datetime

# 프로젝트 루트 경로를 sys.path에 추가 (임시)
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 모의(mock) 객체를 위해 실제 mirror 모듈을 로드
# 이 부분은 test isolation을 위해 조심스럽게 다뤄야 합니다.
# 실제 애플리케이션의 동작과 유사하게 테스트하기 위함입니다.
import mirror
import mirror.config
import mirror.structure

# 테스트를 위한 임시 디렉토리 설정
@pytest.fixture(scope="module")
def temp_config_env(tmp_path_factory):
    # 테스트에 사용할 임시 루트 디렉토리 생성
    temp_dir = tmp_path_factory.mktemp("mirror_config_test")
    
    # 임시 설정 파일 경로 설정
    test_config_path = temp_dir / "config.json"
    test_stat_path = temp_dir / "stat.json"
    test_status_path = temp_dir / "status.json"

    # 테스트 설정 내용 (mirror/config/config.py의 DEFAULT_CONFIG 기반으로 단순화)
    dummy_config_content = {
        "mirrorname": "Test Mirror",
        "settings": {
            "logfolder": str(temp_dir / "logs"),
            "webroot": str(temp_dir / "web"),
            "statusfile": str(test_status_path),
            "statfile": str(test_stat_path), # statfile 추가
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
                "synctype": "bandersnatch", # 이전에 정의된 synctype 사용
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

    # 더미 설정 파일 생성
    test_config_path.write_text(json.dumps(dummy_config_content, indent=4))
    
    # 더미 stat 파일 생성 (초기 상태)
    # load 함수가 stat 파일을 생성하거나 업데이트하므로, 빈 파일을 두거나 최소한의 내용으로 둠.
    test_stat_path.write_text(json.dumps({"packages": {}}, indent=4))

    # 더미 status 파일 생성 (load 함수가 업데이트할 것임)
    test_status_path.write_text(json.dumps({"some_old_status": "data"}, indent=4))


    # mirror.config의 전역 변수들이 테스트 파일 경로를 가리키도록 설정
    # 이는 실제 앱에서 CONFIG_PATH 등을 설정하는 방식과 유사합니다.
    mirror.config.CONFIG_PATH = test_config_path
    mirror.config.STAT_DATA_PATH = test_stat_path
    mirror.config.STATUS_PATH = test_status_path
    mirror.config.SOCKET_PATH = str(temp_dir / "socket.sock") # dummy socket path

    # mirror.sync.methods가 비어있을 수 있으므로 임시로 추가
    # 실제 환경에서는 이들이 로드되어 있어야 합니다.
    if not hasattr(mirror.sync, 'methods') or not mirror.sync.methods:
        mirror.sync.methods = ['bandersnatch', 'rsync'] # 테스트를 위한 임시 메서드 추가
    
    # 픽스쳐 반환
    yield {
        "config_path": test_config_path,
        "stat_path": test_stat_path,
        "status_path": test_status_path,
        "dummy_config_content": dummy_config_content,
        "temp_dir": temp_dir
    }

    # 픽스쳐 종료 후 정리
    # shutil.rmtree(temp_dir) # tmp_path_factory가 자동으로 처리해줌

def test_mirror_config_loading(temp_config_env):
    config_path = temp_config_env["config_path"]
    stat_path = temp_config_env["stat_path"]
    status_path = temp_config_env["status_path"]
    dummy_config_content = temp_config_env["dummy_config_content"]

    # --- 설정 로드 함수 호출 ---
    mirror.config.load(config_path)

    # --- mirror.conf 검증 ---
    assert mirror.conf.name == dummy_config_content["mirrorname"]
    assert mirror.conf.uid == dummy_config_content["settings"]["uid"]
    assert mirror.conf.gid == dummy_config_content["settings"]["gid"]
    assert Path(mirror.conf.logfolder) == Path(dummy_config_content["settings"]["logfolder"])
    assert Path(mirror.conf.webroot) == Path(dummy_config_content["settings"]["webroot"])
    
    # ftpsync 설정 검증
    assert mirror.conf.ftpsync.maintainer == dummy_config_content["settings"]["ftpsync"]["maintainer"]
    
    # --- mirror.packages 검증 ---
    assert len(mirror.packages.keys()) == 1
    pkg_id = "test_pkg_1"
    assert pkg_id in mirror.packages.keys()
    
    pkg = getattr(mirror.packages, pkg_id)
    assert pkg.name == dummy_config_content["packages"][pkg_id]["name"]
    assert pkg.href == dummy_config_content["packages"][pkg_id]["href"]
    assert pkg.synctype == dummy_config_content["packages"][pkg_id]["synctype"]
    assert pkg.syncrate == mirror.toolbox.iso_duration_parser(dummy_config_content["packages"][pkg_id]["syncrate"]) # 파싱된 값 비교
    assert pkg.settings.src == dummy_config_content["packages"][pkg_id]["settings"]["src"]
    
    # --- Stat 파일 검증 ---
    loaded_stat = json.loads(stat_path.read_text())
    assert loaded_stat["mirrorname"] == dummy_config_content["mirrorname"]
    assert pkg_id in loaded_stat["packages"]
    assert loaded_stat["packages"][pkg_id]["name"] == dummy_config_content["packages"][pkg_id]["name"]
    assert loaded_stat["packages"][pkg_id]["status"]["status"] == "UNKNOWN" # 초기 로드 시 UNKNOWN

    # --- Web Status 파일 검증 (생성 및 저장 확인) ---
    mirror.config.generate_and_save_web_status()
    loaded_web_status = json.loads(status_path.read_text())
    assert loaded_web_status["mirrorname"] == dummy_config_content["mirrorname"]
    assert pkg_id in loaded_web_status
    assert loaded_web_status[pkg_id]["status"] == "UNKNOWN" # Config 로드 시 UNKNOWN으로 초기화되므로
    assert "lastupdate" in loaded_web_status

    print("\nConfig loading test passed successfully.")

# mirror.toolbox.iso_duration_parser 함수가 필요하므로 임시로 정의 (실제 모듈이 로드되지 않을 경우 대비)
if not hasattr(mirror, 'toolbox') or not hasattr(mirror.toolbox, 'iso_duration_parser'):
    class MockToolbox:
        def iso_duration_parser(self, duration_str):
            # 간단한 PT1H -> 3600 (초) 변환만 처리
            if duration_str == "PT1H":
                return 3600
            return 0 # 다른 값은 일단 0으로 가정
        def iso_duration_maker(self, seconds):
            if seconds == 3600:
                return "PT1H"
            return ""
    mirror.toolbox = MockToolbox()

# mirror.logger 모듈의 함수들을 모의(mock) 함수로 대체
if hasattr(mirror, 'logger'):
    mirror.logger.info = lambda msg: None
    mirror.logger.warning = lambda msg: None
    mirror.logger.error = lambda msg: None
else:
    # mirror.logger가 아직 로드되지 않았다면 더미 클래스로 생성
    class MockLoggerModule:
        def info(self, msg): pass
        def warning(self, msg): pass
        def error(self, msg): pass
    mirror.logger = MockLoggerModule()

# mirror.conf, mirror.packages 초기화 (테스트 실행마다 초기 상태 보장)
# pytest fixture를 사용하므로 이 부분은 필요 없을 수 있으나, 만약을 위해.
mirror.conf = None
mirror.packages = None
