
import unittest
from unittest.mock import MagicMock, patch
import os
import time
import threading
from pathlib import Path
import socket

# Set PYTHONPATH
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mirror.socket.worker import WorkerServer, WorkerClient
import mirror.worker.process

class TestMasterWorkerCommunication(unittest.TestCase):
    def setUp(self):
        self.socket_path = Path("/tmp/test_worker_comm.sock")
        if self.socket_path.exists():
            self.socket_path.unlink()
        
        # Initialize worker server
        self.server = WorkerServer(socket_path=self.socket_path)
        self.server.set_version("1.0.0-test")
        
        # Run server in a separate thread
        self.server_thread = threading.Thread(target=self.server.start, daemon=True)
        self.server_running = True
        
        # Mock process creation
        self.mock_job = MagicMock()
        self.mock_job.pid = 1234
        
        # Create a pipe for a valid FD
        self.r_pipe, self.w_pipe = os.pipe()
        self.mock_job.get_pipe.side_effect = lambda s: self.w_pipe if s == 'stdout' else None
        
    def tearDown(self):
        self.server.stop()
        os.close(self.r_pipe)
        try:
            os.close(self.w_pipe)
        except OSError:
            pass # Already closed
        if self.socket_path.exists():
            self.socket_path.unlink()

    @patch('mirror.worker.process.create')
    @patch('os.close') # Prevent closing fake FD
    def test_command_reaches_worker(self, mock_close, mock_create):
        # 1. Start worker server
        self.server.start()
        time.sleep(0.2) # Wait for socket preparation
        
        # 2. Setup mocking: Make WorkerServer return mock_job when receiving start_sync
        mock_create.return_value = self.mock_job
        
        # 3. Master side sends command (using WorkerClient)
        client = WorkerClient(socket_path=self.socket_path)
        client.set_version("1.0.0-test")
        
        test_job_id = "test-debian"
        test_command = ["rsync", "-av", "/src", "/dst"]
        test_env = {"DEBUG": "1"}
        
        with client:
            # Send command
            response = client.start_sync(
                job_id=test_job_id,
                sync_method="rsync",
                commandline=test_command,
                env=test_env,
                uid=os.getuid(),
                gid=os.getgid(),
                nice=10
            )
            
            # 4. Verify worker response
            self.assertEqual(response["job_id"], test_job_id)
            self.assertEqual(response["status"], "started")
            self.assertEqual(response["job_pid"], 1234)
            
            # 5. Verify worker server internal logic call (check if command arrived)
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
