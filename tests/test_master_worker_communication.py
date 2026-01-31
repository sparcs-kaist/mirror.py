
import unittest
from unittest.mock import MagicMock, patch
import os
import time
import threading
from pathlib import Path
import socket

# PYTHONPATH 설정
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mirror.socket.worker import WorkerServer, WorkerClient
import mirror.worker.process

class TestMasterWorkerCommunication(unittest.TestCase):
    def setUp(self):
        self.socket_path = Path("/tmp/test_worker_comm.sock")
        if self.socket_path.exists():
            self.socket_path.unlink()
        
        # 워커 서버 초기화
        self.server = WorkerServer(socket_path=self.socket_path)
        self.server.set_version("1.0.0-test")
        
        # 서버를 별도 스레드에서 실행
        self.server_thread = threading.Thread(target=self.server.start, daemon=True)
        self.server_running = True
        
        # 프로세스 생성부 모킹
        self.mock_job = MagicMock()
        self.mock_job.pid = 1234
        
        # 실제 유효한 FD를 위해 파이프 생성
        self.r_pipe, self.w_pipe = os.pipe()
        self.mock_job.get_pipe.side_effect = lambda s: self.w_pipe if s == 'stdout' else None
        
    def tearDown(self):
        self.server.stop()
        os.close(self.r_pipe)
        try:
            os.close(self.w_pipe)
        except OSError:
            pass # 이미 닫혔을 수 있음
        if self.socket_path.exists():
            self.socket_path.unlink()

    @patch('mirror.worker.process.create')
    @patch('os.close') # 가짜 FD 닫기 방지
    def test_command_reaches_worker(self, mock_close, mock_create):
        # 1. 워커 서버 시작
        self.server.start()
        time.sleep(0.2) # 소켓 준비 대기
        
        # 2. 모킹 설정: WorkerServer가 start_sync를 받으면 mock_job을 반환하도록 함
        mock_create.return_value = self.mock_job
        
        # 3. 마스터 측에서 명령 전송 (WorkerClient 사용)
        client = WorkerClient(socket_path=self.socket_path)
        client.set_version("1.0.0-test")
        
        test_job_id = "test-debian"
        test_command = ["rsync", "-av", "/src", "/dst"]
        test_env = {"DEBUG": "1"}
        
        with client:
            # 명령 전송
            response = client.start_sync(
                job_id=test_job_id,
                sync_method="rsync",
                commandline=test_command,
                env=test_env,
                uid=os.getuid(),
                gid=os.getgid(),
                nice=10
            )
            
            # 4. 워커 응답 검증
            self.assertEqual(response["job_id"], test_job_id)
            self.assertEqual(response["status"], "started")
            self.assertEqual(response["job_pid"], 1234)
            
            # 5. 워커 서버 내부 로직 호출 검증 (명령어가 도착했는지 확인)
            mock_create.assert_called_once_with(
                job_id=test_job_id,
                commandline=test_command,
                env=test_env,
                uid=os.getuid(),
                gid=os.getgid(),
                nice=10,
                log_path=None
            )
            
            print(f"\n[SUCCESS] Command '{test_job_id}' reached worker successfully.")

if __name__ == '__main__':
    unittest.main()
