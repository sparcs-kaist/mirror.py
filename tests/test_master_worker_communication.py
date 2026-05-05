
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


def test_supervisor_reconnects_after_worker_restart(tmp_path):
    """The WorkerClientSupervisor must reconnect after the worker server restarts."""
    import threading
    import time
    from mirror.socket.worker import WorkerServer, WorkerClientSupervisor
    import mirror.socket.worker as worker_module

    sock = tmp_path / "supervisor.sock"

    server1 = WorkerServer(sock)
    server1.start()

    supervisor = WorkerClientSupervisor(socket_path=sock)
    supervisor.set_version("test")
    supervisor.start()

    try:
        # Wait for initial connect (poll up to 5s).
        for _ in range(50):
            if supervisor.is_connected:
                break
            time.sleep(0.1)
        assert supervisor.is_connected, "initial connect did not succeed"

        # Stop the server and ensure the supervisor flips disconnected.
        server1.stop()
        for _ in range(50):
            if not supervisor.is_connected:
                break
            time.sleep(0.1)
        assert not supervisor.is_connected, "supervisor did not detect disconnect"

        # Restart server; supervisor should reconnect within ~5s.
        # IMPORTANT: it backs off up to 30s, so for the test we wait long enough
        # for at least one retry. Initial backoff is 1s, so ~5s is plenty
        # if we restart immediately after first failure.
        server2 = WorkerServer(sock)
        server2.start()
        try:
            for _ in range(60):
                if supervisor.is_connected:
                    break
                time.sleep(0.5)
            assert supervisor.is_connected, "supervisor did not reconnect"
        finally:
            server2.stop()
    finally:
        supervisor.stop()


def test_supervisor_fires_reconnect_event_only_after_initial(tmp_path):
    """MASTER.WORKER_RECONNECTED must fire on reconnect, not on initial connect."""
    import threading
    import time
    from mirror.socket.worker import WorkerServer, WorkerClientSupervisor
    import mirror.event as event_mod

    sock = tmp_path / "reconnect_event.sock"

    seen = []
    def _listener(*args, **kwargs):
        seen.append(("MASTER.WORKER_RECONNECTED", args, kwargs))

    event_mod.on("MASTER.WORKER_RECONNECTED", _listener)
    try:
        server1 = WorkerServer(sock)
        server1.start()

        supervisor = WorkerClientSupervisor(socket_path=sock)
        supervisor.set_version("test")
        supervisor.start()

        try:
            for _ in range(50):
                if supervisor.is_connected:
                    break
                time.sleep(0.1)
            assert supervisor.is_connected
            time.sleep(0.5)
            assert seen == [], "event fired on initial connect"

            server1.stop()
            for _ in range(50):
                if not supervisor.is_connected:
                    break
                time.sleep(0.1)

            server2 = WorkerServer(sock)
            server2.start()
            try:
                for _ in range(60):
                    if supervisor.is_connected:
                        break
                    time.sleep(0.5)
                assert supervisor.is_connected
                # give event listener a moment
                time.sleep(0.5)
                assert len(seen) >= 1, "expected reconnect event"
            finally:
                server2.stop()
        finally:
            supervisor.stop()
    finally:
        event_mod.off("MASTER.WORKER_RECONNECTED", _listener)


def test_supervisor_stops_cleanly(tmp_path):
    """supervisor.stop() must terminate the thread within the join timeout."""
    import time
    from mirror.socket.worker import WorkerClientSupervisor

    # Point at a non-existent socket so it loops in backoff.
    supervisor = WorkerClientSupervisor(socket_path=tmp_path / "nope.sock")
    supervisor.start()
    time.sleep(0.5)
    supervisor.stop(join_timeout=10.0)
    assert supervisor._thread is not None
    assert not supervisor._thread.is_alive()
