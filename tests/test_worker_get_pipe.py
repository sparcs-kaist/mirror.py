import os
import subprocess
from mirror.worker import process

def test_get_pipe():
    worker_id = "fd_test_worker"
    command = ["ls"]
    
    worker = process.create(worker_id, command, {}, None, None, 0)
    
    try:
        # 각 파이프의 FD 가져오기
        stdin_fd = worker.get_pipe("stdin")
        stdout_fd = worker.get_pipe("stdout")
        stderr_fd = worker.get_pipe("stderr")
        
        print(f"FDs - stdin: {stdin_fd}, stdout: {stdout_fd}, stderr: {stderr_fd}")
        
        # FD가 유효한 정수인지 확인
        assert isinstance(stdin_fd, int)
        assert isinstance(stdout_fd, int)
        assert isinstance(stderr_fd, int)
        assert stdin_fd > 0
        assert stdout_fd > 0
        assert stderr_fd > 0
        
        # 실제로 OS 레벨에서 유효한 FD인지 확인 (fstat 사용)
        os.fstat(stdin_fd)
        os.fstat(stdout_fd)
        os.fstat(stderr_fd)
        
        print("FD verification success")
        
    finally:
        worker.stop()
        process.prune_finished()

if __name__ == "__main__":
    test_get_pipe()
