import pytest
import tempfile
import time
import sys
import os
from pathlib import Path

# Use normal imports — earlier code used a `_load_module` helper that
# overwrote `sys.modules['mirror.socket.*']`, causing contamination for
# subsequent tests that patched those modules.
import mirror.socket.protocol as _protocol_module
import mirror.socket.base as _base_module
import mirror.socket as _init_module
import mirror.socket.master as _master_module
import mirror.socket.worker as _worker_module

BaseServer = _base_module.BaseServer
BaseClient = _base_module.BaseClient
HandshakeInfo = _protocol_module.HandshakeInfo
PROTOCOL_VERSION = _protocol_module.PROTOCOL_VERSION
APP_NAME = _protocol_module.APP_NAME

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
        from unittest.mock import MagicMock
        import mirror

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"

            pkg = MagicMock()
            pkg.to_dict.return_value = {"id": "test-pkg", "name": "Test Package"}

            mock_packages = MagicMock()
            mock_packages.values.return_value = [pkg]

            original = getattr(mirror, 'packages', None)
            mirror.packages = mock_packages

            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    result = client.list_packages()
                    assert "packages" in result
                    assert isinstance(result["packages"], list)
                    assert len(result["packages"]) == 1
                    assert result["packages"][0]["id"] == "test-pkg"
            finally:
                server.stop()
                mirror.packages = original

    def test_master_package_ops(self):
        """Test package operations (start_sync, stop_sync, get_package)"""
        from unittest.mock import MagicMock
        import mirror

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"

            pkg = MagicMock()
            pkg.is_disabled.return_value = False
            pkg.is_syncing.return_value = False
            pkg.to_dict.return_value = {"id": "pkg1", "name": "Package 1"}

            mock_packages = MagicMock()
            mock_packages.get.return_value = pkg

            mock_sync = MagicMock()
            mock_worker = MagicMock()

            original_packages = getattr(mirror, 'packages', None)
            original_sync_in_sysmod = sys.modules.get('mirror.sync')
            original_sync_attr = getattr(mirror, 'sync', None)
            original_worker_in_sysmod = sys.modules.get('mirror.socket.worker')
            original_worker_attr = getattr(mirror.socket, 'worker', None)

            mirror.packages = mock_packages
            sys.modules['mirror.sync'] = mock_sync
            mirror.sync = mock_sync
            sys.modules['mirror.socket.worker'] = mock_worker
            mirror.socket.worker = mock_worker

            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    # start_sync
                    result = client.start_sync("pkg1")
                    assert result["package_id"] == "pkg1"
                    assert result["status"] == "started"
                    mock_sync.start.assert_called_once()

                    # set syncing state for stop test
                    pkg.is_syncing.return_value = True

                    # stop_sync
                    result = client.stop_sync("pkg1")
                    assert result["package_id"] == "pkg1"
                    assert result["status"] == "stopped"
                    mock_worker.stop_command.assert_called_once_with(job_id="pkg1")

                    # get_package
                    result = client.get_package("pkg1")
                    assert result["id"] == "pkg1"
            finally:
                server.stop()
                mirror.packages = original_packages
                if original_sync_in_sysmod is not None:
                    sys.modules['mirror.sync'] = original_sync_in_sysmod
                else:
                    sys.modules.pop('mirror.sync', None)
                if original_sync_attr is not None:
                    mirror.sync = original_sync_attr
                else:
                    delattr(mirror, 'sync')
                if original_worker_in_sysmod is not None:
                    sys.modules['mirror.socket.worker'] = original_worker_in_sysmod
                else:
                    sys.modules.pop('mirror.socket.worker', None)
                if original_worker_attr is not None:
                    mirror.socket.worker = original_worker_attr
                else:
                    delattr(mirror.socket, 'worker')


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
        """Test stop_command (by job_id)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "worker.sock"

            server = WorkerServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with WorkerClient(socket_path) as client:
                    # First start a sync so we can stop it
                    client.execute_command(
                        job_id="test-job-stop",
                        sync_method="test",
                        commandline=["sleep", "60"],
                        env={},
                        uid=os.getuid(),
                        gid=os.getgid()
                    )

                    result = client.stop_command(job_id="test-job-stop")
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
                    client.execute_command(
                        job_id="test-job-progress",
                        sync_method="test",
                        commandline=["sleep", "60"],
                        env={},
                        uid=os.getuid(),
                        gid=os.getgid()
                    )

                    # Aggregate progress (no-arg): returns {"syncing", "jobs"}
                    result = client.get_progress()
                    assert result["syncing"] == True
                    assert "jobs" in result
                    assert isinstance(result["jobs"], dict)
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
                    result = worker_client.execute_command(
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


class TestMasterPushSync:
    """Test push_sync RPC on MasterServer"""

    def _make_fake_pkg(self, pkgid="test", disabled=False, syncing=False):
        class FakePkg:
            pass
        pkg = FakePkg()
        pkg.pkgid = pkgid
        pkg.synctype = "rsync"
        pkg.is_disabled = lambda: disabled
        pkg.is_syncing = lambda: syncing
        return pkg

    def test_push_sync_unknown_package(self, monkeypatch):
        """push_sync with unknown pkgid raises an error on the client side"""
        import mirror

        original = getattr(mirror, "packages", None)
        monkeypatch.setattr(mirror, "packages", {}, raising=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    with pytest.raises(Exception):
                        client.push_sync("nonexistent")
            finally:
                server.stop()

    def test_push_sync_disabled_package(self, monkeypatch):
        """push_sync for a disabled package raises an error on the client side"""
        import mirror

        pkg = self._make_fake_pkg(pkgid="disabled-pkg", disabled=True)
        monkeypatch.setattr(mirror, "packages", {"disabled-pkg": pkg}, raising=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    with pytest.raises(Exception):
                        client.push_sync("disabled-pkg")
            finally:
                server.stop()

    def test_push_sync_already_running(self, monkeypatch):
        """push_sync returns already_running when pkgid is in _in_progress"""
        import mirror
        import mirror.sync

        pkgid = "running-pkg"
        pkg = self._make_fake_pkg(pkgid=pkgid)
        monkeypatch.setattr(mirror, "packages", {pkgid: pkg}, raising=False)

        start_called = []

        def fake_start(package, trigger, extra_args=None):
            start_called.append(True)

        monkeypatch.setattr(mirror.sync, "start", fake_start)
        mirror.sync._in_progress.add(pkgid)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                socket_path = Path(tmpdir) / "master.sock"
                server = MasterServer(socket_path)
                server.start()
                time.sleep(0.1)

                try:
                    with MasterClient(socket_path) as client:
                        result = client.push_sync(pkgid)
                        assert result["package_id"] == pkgid
                        assert result["status"] == "already_running"
                        assert start_called == [], "mirror.sync.start must not be called"
                finally:
                    server.stop()
        finally:
            mirror.sync._in_progress.discard(pkgid)

    def test_push_sync_extra_args_none(self, monkeypatch):
        """push_sync with no extra_args passes None to mirror.sync.start"""
        import mirror
        import mirror.sync

        pkgid = "test-pkg"
        pkg = self._make_fake_pkg(pkgid=pkgid)
        monkeypatch.setattr(mirror, "packages", {pkgid: pkg}, raising=False)

        recorded = {}

        def fake_start(package, trigger, extra_args=None):
            recorded["extra_args"] = extra_args
            recorded["trigger"] = trigger

        monkeypatch.setattr(mirror.sync, "start", fake_start)

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    result = client.push_sync(pkgid)
                    assert result["status"] == "started"
                    assert result["package_id"] == pkgid
                    assert recorded["extra_args"] is None
                    assert recorded["trigger"] == "push"
            finally:
                server.stop()

    def test_push_sync_extra_args_valid(self, monkeypatch):
        """push_sync with valid extra_args dict forwards it to mirror.sync.start"""
        import mirror
        import mirror.sync

        pkgid = "test-pkg"
        pkg = self._make_fake_pkg(pkgid=pkgid)
        monkeypatch.setattr(mirror, "packages", {pkgid: pkg}, raising=False)

        recorded = {}

        def fake_start(package, trigger, extra_args=None):
            recorded["extra_args"] = extra_args

        monkeypatch.setattr(mirror.sync, "start", fake_start)

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    result = client.push_sync(pkgid, extra_args={"FOO": "bar"})
                    assert result["status"] == "started"
                    assert recorded["extra_args"] == {"FOO": "bar"}
            finally:
                server.stop()

    def test_push_sync_extra_args_non_string_value(self, monkeypatch):
        """push_sync with non-string value in extra_args raises an error on client side"""
        import mirror
        import mirror.sync

        pkgid = "test-pkg"
        pkg = self._make_fake_pkg(pkgid=pkgid)
        monkeypatch.setattr(mirror, "packages", {pkgid: pkg}, raising=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    with pytest.raises(Exception):
                        client.push_sync(pkgid, extra_args={"FOO": 1})
            finally:
                server.stop()

    def test_push_sync_extra_args_not_dict(self, monkeypatch):
        """push_sync with extra_args as a string raises an error on client side"""
        import mirror
        import mirror.sync

        pkgid = "test-pkg"
        pkg = self._make_fake_pkg(pkgid=pkgid)
        monkeypatch.setattr(mirror, "packages", {pkgid: pkg}, raising=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    with pytest.raises(Exception):
                        client.push_sync(pkgid, extra_args="not-a-dict")
            finally:
                server.stop()

    def test_push_sync_race_translates_runtime_error(self, monkeypatch):
        """push_sync returns already_running when start raises the in-progress race error"""
        import mirror
        import mirror.sync

        pkgid = "race-pkg"
        pkg = self._make_fake_pkg(pkgid=pkgid)
        monkeypatch.setattr(mirror, "packages", {pkgid: pkg}, raising=False)

        def fake_start(package, trigger, extra_args=None):
            raise RuntimeError(f"Package {pkgid} sync already in progress")

        monkeypatch.setattr(mirror.sync, "start", fake_start)

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    result = client.push_sync(pkgid)
                    assert result["status"] == "already_running"
                    assert result["package_id"] == pkgid
            finally:
                server.stop()

    def test_push_sync_non_race_runtime_error_surfaces(self, monkeypatch):
        """push_sync re-raises RuntimeError that does not match the in-progress race"""
        import mirror
        import mirror.sync

        pkgid = "config-error-pkg"
        pkg = self._make_fake_pkg(pkgid=pkgid)
        monkeypatch.setattr(mirror, "packages", {pkgid: pkg}, raising=False)

        def fake_start(package, trigger, extra_args=None):
            raise RuntimeError(f"Sync plug-in '{package.synctype}' has no execute callable")

        monkeypatch.setattr(mirror.sync, "start", fake_start)

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    with pytest.raises(Exception) as ei:
                        client.push_sync(pkgid)
                    assert "no execute callable" in str(ei.value)
            finally:
                server.stop()


def test_recv_message_handles_fragmented_header(tmp_path):
    """recv_message must reassemble headers split across multiple recv() calls."""
    import socket as _socket
    import struct
    import threading
    import time
    from mirror.socket.protocol import recv_message, send_message

    sock_path = tmp_path / "frag.sock"
    server = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen()

    received = {}

    def _server():
        conn, _ = server.accept()
        try:
            received["msg"] = recv_message(conn, timeout=5.0)
        finally:
            conn.close()

    t = threading.Thread(target=_server, daemon=True)
    t.start()

    client = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    client.connect(str(sock_path))
    body = b'{"hello":"world"}'
    header = struct.pack(">I", len(body))
    # Send header in two parts to force a partial recv
    client.sendall(header[:1])
    time.sleep(0.05)
    client.sendall(header[1:])
    client.sendall(body)
    client.close()

    t.join(timeout=5.0)
    server.close()

    assert received["msg"] == {"hello": "world"}


def test_send_command_serialized_under_concurrent_callers(tmp_path):
    """Concurrent send_command calls from one BaseClient must not mismatch responses."""
    import threading
    import time
    from mirror.socket.base import BaseServer
    from mirror.socket.master import MasterClient

    class EchoServer(BaseServer):
        def __init__(self, socket_path):
            super().__init__(socket_path, role="master")

        def echo(self, value: str) -> dict:
            time.sleep(0.01)  # encourage interleave attempts
            return {"value": value}

    server = EchoServer(tmp_path / "echo.sock")
    server.register_handler("echo", server.echo)
    server.start()
    try:
        client = MasterClient(server.socket_path)
        client.connect()
        try:
            errors = []
            results = {}

            def _worker(i: int):
                try:
                    resp = client.send_command("echo", value=f"v{i}")
                    results[i] = resp
                except Exception as exc:  # noqa: BLE001
                    errors.append((i, exc))

            threads = [threading.Thread(target=_worker, args=(i,)) for i in range(5)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(timeout=10.0)

            assert not errors, f"errors: {errors}"
            assert len(results) == 5
            for i, resp in results.items():
                assert resp == {"value": f"v{i}"}, f"thread {i} got {resp}"
        finally:
            client.disconnect()
    finally:
        server.stop()
