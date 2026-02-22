import pytest
import tempfile
import time
import sys
import os
import importlib.util
from pathlib import Path

# Load socket modules directly to avoid circular import
def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

_socket_dir = Path(__file__).parent.parent / "mirror" / "socket"

# Load modules in order
_init_module = _load_module("mirror.socket", _socket_dir / "__init__.py")
_master_module = _load_module("mirror.socket.master", _socket_dir / "master.py")
_worker_module = _load_module("mirror.socket.worker", _socket_dir / "worker.py")

BaseServer = _init_module.BaseServer
BaseClient = _init_module.BaseClient
HandshakeInfo = _init_module.HandshakeInfo
PROTOCOL_VERSION = _init_module.PROTOCOL_VERSION
APP_NAME = _init_module.APP_NAME

MasterServer = _master_module.MasterServer
MasterClient = _master_module.MasterClient

WorkerServer = _worker_module.WorkerServer
WorkerClient = _worker_module.WorkerClient


class TestHandshake:
    """Test handshake protocol"""

    def test_successful_handshake(self):
        """Test successful handshake between server and client"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            server = MasterServer(socket_path)
            server.set_version("1.0.0")
            server.start()
            time.sleep(0.1)

            try:
                client = MasterClient(socket_path)
                client.set_version("1.0.0")
                server_info = client.connect()

                assert server_info.app_name == APP_NAME
                assert server_info.protocol_version == PROTOCOL_VERSION
                assert server_info.role == "master"
                assert server_info.is_server == True
            finally:
                client.disconnect()
                server.stop()

    def test_handshake_version_exchange(self):
        """Test version info is exchanged during handshake"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            server = MasterServer(socket_path)
            server.set_version("2.0.0")
            server.start()
            time.sleep(0.1)

            try:
                client = MasterClient(socket_path)
                client.set_version("1.5.0")
                server_info = client.connect()

                assert server_info.app_version == "2.0.0"
            finally:
                client.disconnect()
                server.stop()


class TestMasterServer:
    """Test Master server"""

    def test_master_ping(self):
        """Test ping command"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"

            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    result = client.ping()
                    assert result == {"message": "pong"}
            finally:
                server.stop()

    def test_master_status(self):
        """Test status command"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"

            server = MasterServer(socket_path)
            server.set_version("1.2.3")
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    result = client.status()
                    assert result["running"] == True
                    assert result["role"] == "master"
                    assert result["version"] == "1.2.3"
            finally:
                server.stop()

    def test_master_unknown_command(self):
        """Test error for unknown command"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"

            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    with pytest.raises(Exception) as excinfo:
                        client.send_command("unknown_command")
                    assert "404" in str(excinfo.value)
            finally:
                server.stop()

    def test_master_list_packages(self):
        """Test list_packages command"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"

            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    result = client.list_packages()
                    assert "packages" in result
                    assert isinstance(result["packages"], list)
            finally:
                server.stop()

    def test_master_package_ops(self):
        """Test package operations (start_sync, stop_sync, get_package)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"

            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    # start_sync
                    result = client.start_sync("pkg1")
                    assert result["package_id"] == "pkg1"
                    assert result["status"] == "started"

                    # stop_sync
                    result = client.stop_sync("pkg1")
                    assert result["package_id"] == "pkg1"
                    assert result["status"] == "stopped"

                    # get_package
                    result = client.get_package("pkg1")
                    assert result["package_id"] == "pkg1"
            finally:
                server.stop()


class TestWorkerServer:
    """Test Worker server"""

    def test_worker_ping(self):
        """Test ping command"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "worker.sock"

            server = WorkerServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with WorkerClient(socket_path) as client:
                    result = client.ping()
                    assert result == {"message": "pong"}
            finally:
                server.stop()

        def test_worker_start_sync(self):
            """Test start_sync command"""
            with tempfile.TemporaryDirectory() as tmpdir:
                socket_path = Path(tmpdir) / "worker.sock"
        
                server = WorkerServer(socket_path)
                server.start()
                time.sleep(0.1)
        
                try:
                    with WorkerClient(socket_path) as client:
                        result = client.start_sync(
                            job_id="test-job-start",
                            sync_method="test",
                            commandline=["ls"],
                            env={},
                            uid=os.getuid(),
                            gid=os.getgid()
                        )
                        assert result["job_id"] == "test-job-start"
                        assert result["status"] == "started"
                finally:
                    server.stop()
    def test_worker_status(self):
        """Test status command"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "worker.sock"

            server = WorkerServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with WorkerClient(socket_path) as client:
                    result = client.status()
                    assert result["running"] == True
                    assert result["role"] == "worker"
            finally:
                server.stop()

    def test_worker_stop_sync(self):
        """Test stop_sync command"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "worker.sock"

            server = WorkerServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with WorkerClient(socket_path) as client:
                    # First start a sync so we can stop it
                    client.start_sync(
                        job_id="test-job-stop",
                        sync_method="test",
                        commandline=["ls"],
                        env={},
                        uid=os.getuid(),
                        gid=os.getgid()
                    )
                    
                    result = client.stop_sync()
                    assert result["job_id"] == "test-job-stop"
                    assert result["status"] == "stopped"
            finally:
                server.stop()

    def test_worker_get_progress(self):
        """Test get_progress command"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "worker.sock"

            server = WorkerServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with WorkerClient(socket_path) as client:
                    # No sync running
                    result = client.get_progress()
                    assert result["syncing"] == False

                    # Start sync
                    client.start_sync(
                        job_id="test-job-progress",
                        sync_method="test",
                        commandline=["ls"],
                        env={},
                        uid=os.getuid(),
                        gid=os.getgid()
                    )

                    # Check progress (mocked)
                    result = client.get_progress()
                    assert result["syncing"] == True
                    assert result["job_id"] == "test-job-progress"
            finally:
                server.stop()


class TestMasterWorkerCommunication:
    """Test Master-Worker communication"""

    def test_master_to_worker(self):
        """Test Master sending commands to Worker"""
        with tempfile.TemporaryDirectory() as tmpdir:
            master_path = Path(tmpdir) / "master.sock"
            worker_path = Path(tmpdir) / "worker.sock"

            master = MasterServer(master_path)
            worker = WorkerServer(worker_path)
            master.start()
            worker.start()
            time.sleep(0.1)

            try:
                # Connect to worker and send sync command
                with WorkerClient(worker_path) as worker_client:
                    result = worker_client.start_sync(
                        job_id="test-master-job",
                        sync_method="test",
                        commandline=["ls"],
                        env={},
                        uid=os.getuid(),
                        gid=os.getgid()
                    )
                    assert result["job_id"] == "test-master-job"

                # Connect to master and check ping
                with MasterClient(master_path) as master_client:
                    assert master_client.ping() == {"message": "pong"}
            finally:
                master.stop()
                worker.stop()

    def test_multiple_requests(self):
        """Test multiple consecutive requests"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"

            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    for _ in range(10):
                        assert client.ping() == {"message": "pong"}
            finally:
                server.stop()

    def test_concurrent_clients(self):
        """Test multiple concurrent client connections"""
        import threading

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"

            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            results = []
            errors = []

            def client_request():
                try:
                    with MasterClient(socket_path) as client:
                        result = client.ping()
                        results.append(result)
                except Exception as e:
                    errors.append(e)

            try:
                threads = [threading.Thread(target=client_request) for _ in range(5)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=5)

                assert len(errors) == 0, f"Errors occurred: {errors}"
                assert len(results) == 5
                assert all(r == {"message": "pong"} for r in results)
            finally:
                server.stop()


class TestConnectionErrors:
    """Test connection errors"""

    def test_socket_not_found(self):
        """Test connection to non-existent socket"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "nonexistent.sock"
            client = MasterClient(socket_path)
            with pytest.raises(ConnectionError):
                client.connect()

    def test_server_stopped(self):
        """Test connection after server stopped"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"

            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            # Verify connection works
            with MasterClient(socket_path) as client:
                assert client.ping() == {"message": "pong"}

            # Stop server
            server.stop()
            time.sleep(0.1)

            # Connection should fail
            client2 = MasterClient(socket_path)
            with pytest.raises(ConnectionError):
                client2.connect()


class TestContextManager:
    """Test context manager usage"""

    def test_client_context_manager(self):
        """Test client as context manager"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"

            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    assert client.is_connected
                    assert client.ping() == {"message": "pong"}

                # After exiting context, client should be disconnected
                assert not client.is_connected
            finally:
                server.stop()