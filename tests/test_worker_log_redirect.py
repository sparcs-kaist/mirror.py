import time
import pytest
from pathlib import Path
from mirror.worker import process

def test_set_log_path(tmp_path):
    worker_id = "log_test_worker"
    log_file = tmp_path / "worker.log"
    
    # 1. 워커 생성 (출력을 생성하는 echo 명령어 사용)
    # 여러 줄을 출력하도록 함
    command = ["/bin/sh", "-c", "echo 'Line 1'; sleep 0.1; echo 'Line 2'; sleep 0.1; echo 'Line 3'"]
    worker = process.create(worker_id, command, {}, None, None, 0)
    
    # 2. 로그 경로 설정 (백그라운드 쓰레드 시작)
    process.set_log_path(worker_id, log_file)
    
    # 3. 워커가 끝날 때까지 대기
    # 쓰레드가 파이프를 다 읽을 때까지 기다려야 함
    max_retries = 20
    while worker.is_running and max_retries > 0:
        time.sleep(0.1)
        max_retries -= 1
        
    worker.stop()
    
    # 로그 파일 쓰기가 완료될 시간을 조금 줌 (쓰레드 flush)
    time.sleep(0.5)
    
    # 4. 로그 파일 확인
    assert log_file.exists()
    content = log_file.read_text()
    
    print(f"Log content:\n{content}")
    
    assert "Line 1" in content
    assert "Line 2" in content
    assert "Line 3" in content

    # 5. Master 프로세스도 동시에 Append 할 수 있는지 확인
    with open(log_file, "a") as f:
        f.write("Master appended line\n")
        
    final_content = log_file.read_text()
    assert "Master appended line" in final_content
    
    process.prune_finished()

if __name__ == "__main__":
    pytest.main([__file__])
