import pytest
import json
import queue
import socket
import stat
import tempfile
import threading
import time
import types
import sys
import os
from pathlib import Path

import mirror
from mirror.structure import Config, Package, PackageSettings

# Use normal imports — earlier code used a `_load_module` helper that
# overwrote `sys.modules['mirror.socket.*']`, causing contamination for
# subsequent tests that patched those modules.
import mirror.socket.protocol as _protocol_module
import mirror.socket.base as _base_module
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
                assert server_info.protocol_version == 3
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

            pkg = Package(
                pkgid="test-pkg",
                name="Test Package",
                status="ACTIVE",
                href="/test-pkg",
                synctype="rsync",
                syncrate=3600,
                link=[Package.Link(rel="self", href="/test-pkg")],
                settings=PackageSettings(
                    hidden=False,
                    src="rsync://user@example.invalid/repo",
                    dst="/srv/mirror/test-pkg",
                    options={"password": "rpc-test-secret"},
                ),
                lastsync=123.0,
                disabled=False,
                timestamp=456.0,
            )

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
                    response = result["packages"][0]
                    assert set(response) == {
                        "id",
                        "name",
                        "href",
                        "synctype",
                        "syncrate",
                        "link",
                        "disabled",
                        "lastsync",
                        "timestamp",
                        "status",
                    }
                    assert response["id"] == "test-pkg"
                    assert response["syncrate"] == "PT1H"
                    assert "settings" not in response
                    assert "rpc-test-secret" not in json.dumps(response)
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
            pkg.to_status_dict.return_value = {"id": "pkg1", "name": "Package 1"}

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

    def test_master_get_package_omits_settings(self):
        """get_package returns status fields without sync credentials."""
        import mirror

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            pkg = Package(
                pkgid="private",
                name="Private",
                status="ERROR",
                href="/private",
                synctype="lftp",
                syncrate=-1,
                link=[],
                settings=PackageSettings(
                    hidden=True,
                    src="ftp://user@example.invalid/repo",
                    dst="/srv/mirror/private",
                    options={"password": "get-package-secret"},
                ),
            )
            pkg.statusinfo.runninglog = "/var/log/mirror/packages/private.log"

            original = getattr(mirror, "packages", None)
            mirror.packages = type("Packages", (), {"get": lambda self, key: pkg})()
            server = MasterServer(socket_path)
            server.start()
            time.sleep(0.1)

            try:
                with MasterClient(socket_path) as client:
                    response = client.get_package("private")
            finally:
                server.stop()
                mirror.packages = original

            assert response["syncrate"] == "PUSH"
            assert response["status"]["status"] == "ERROR"
            assert response["status"]["statusinfo"]["runninglog"].endswith(
                "private.log"
            )
            assert "settings" not in response
            assert "get-package-secret" not in json.dumps(response)


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
        """push_sync returns already_running when package is_syncing() returns True"""
        import mirror
        import mirror.sync

        pkgid = "running-pkg"
        pkg = self._make_fake_pkg(pkgid=pkgid, syncing=True)
        monkeypatch.setattr(mirror, "packages", {pkgid: pkg}, raising=False)

        start_called = []

        def fake_start(package, trigger, extra_args=None):
            start_called.append(True)

        monkeypatch.setattr(mirror.sync, "start", fake_start)

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
                    result = client.push_sync(pkgid, extra_args={"SSH_ORIGINAL_COMMAND": "ftpsync sync:all"})
                    assert result["status"] == "started"
                    assert recorded["extra_args"] == {"SSH_ORIGINAL_COMMAND": "ftpsync sync:all"}
            finally:
                server.stop()

    @pytest.mark.parametrize("key", ["BASH_ENV", "PERL5OPT", "PYTHONPATH", "ARBITRARY_VARIABLE"])
    @pytest.mark.parametrize("syncing", [False, True])
    def test_push_sync_rejects_env_injection(self, monkeypatch, tmp_path: Path, key: str, syncing: bool) -> None:
        """Untrusted environment keys cannot reach sync.start, even during a sync."""
        import mirror.sync
        from unittest.mock import MagicMock

        pkgid = "injection-pkg"
        pkg = self._make_fake_pkg(pkgid=pkgid, syncing=syncing)
        monkeypatch.setattr(mirror, "packages", {pkgid: pkg}, raising=False)
        start = MagicMock()
        monkeypatch.setattr(mirror.sync, "start", start)

        socket_path = tmp_path / "master.sock"
        server = MasterServer(socket_path)
        server.start()
        try:
            with MasterClient(socket_path) as client:
                with pytest.raises(Exception, match="is not allowed"):
                    client.push_sync(pkgid, extra_args={
                        "SSH_ORIGINAL_COMMAND": "ftpsync sync:all",
                        key: "payload",
                    })
            start.assert_not_called()
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


def test_json_transport_round_trip_preserves_mapping():
    """The protocol sends and receives nested JSON mappings without coercion."""
    sender, receiver = _protocol_module.socket.socketpair()
    payload = {
        "command": "sync",
        "kwargs": {"packages": ["debian", "한글"], "force": True},
        "request_id": "request-1",
    }

    try:
        _protocol_module.send_message(sender, payload)
        assert _protocol_module.recv_message(receiver, timeout=1.0) == payload
    finally:
        sender.close()
        receiver.close()


def test_recv_message_handles_fragmented_header(tmp_path):
    """recv_message must reassemble headers split across multiple recv() calls."""
    import socket as _socket
    import struct
    import threading
    import time
    from mirror.socket.protocol import recv_message

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


def test_recv_message_handles_fragmented_body():
    """recv_message must reassemble a JSON body split across recv() calls."""
    import struct

    sender, receiver = _protocol_module.socket.socketpair()
    body = b'{"hello":"fragmented body"}'

    def _send_fragments():
        sender.sendall(struct.pack(">I", len(body)) + body[:5])
        time.sleep(0.05)
        sender.sendall(body[5:])

    thread = threading.Thread(target=_send_fragments, daemon=True)
    thread.start()
    try:
        assert _protocol_module.recv_message(receiver, timeout=1.0) == {
            "hello": "fragmented body"
        }
    finally:
        thread.join(timeout=1.0)
        sender.close()
        receiver.close()


@pytest.mark.parametrize(
    ("wire_data", "message"),
    [
        (b"\x00\x00", "header"),
        (b"\x00\x00\x00\x0a{}", "message"),
    ],
)
def test_recv_message_reports_connection_closed_mid_frame(wire_data, message):
    """A peer close during either frame section raises a useful error."""
    sender, receiver = _protocol_module.socket.socketpair()
    sender.sendall(wire_data)
    sender.close()

    try:
        with pytest.raises(ConnectionError, match=message):
            _protocol_module.recv_message(receiver, timeout=1.0)
    finally:
        receiver.close()


@pytest.mark.parametrize("length", [0, _protocol_module.MAX_MESSAGE_SIZE + 1])
def test_recv_message_rejects_invalid_size_before_body(length):
    """Invalid frame sizes must be rejected without waiting for a body."""
    import struct

    sender, receiver = _protocol_module.socket.socketpair()
    sender.sendall(struct.pack(">I", length))
    try:
        with pytest.raises(_protocol_module.ProtocolError, match="Message size"):
            _protocol_module.recv_message(receiver, timeout=0.2)
    finally:
        sender.close()
        receiver.close()


@pytest.mark.parametrize(
    "body",
    [
        b"\xff",
        b"{invalid-json}",
        b"[]",
    ],
)
def test_recv_message_rejects_invalid_payload(body):
    """UTF-8, JSON, and top-level mapping validation happen at the boundary."""
    import struct

    sender, receiver = _protocol_module.socket.socketpair()
    sender.sendall(struct.pack(">I", len(body)) + body)
    try:
        with pytest.raises(_protocol_module.ProtocolError):
            _protocol_module.recv_message(receiver, timeout=0.2)
    finally:
        sender.close()
        receiver.close()


def test_send_message_rejects_oversize_payload():
    """send_message must reject a JSON body larger than the frame limit."""
    sender, receiver = _protocol_module.socket.socketpair()
    payload = {"value": "x" * _protocol_module.MAX_MESSAGE_SIZE}
    try:
        with pytest.raises(_protocol_module.ProtocolError, match="Message size"):
            _protocol_module.send_message(sender, payload)
    finally:
        sender.close()
        receiver.close()


def test_message_size_boundary_round_trip(monkeypatch):
    """A body exactly at the configured size limit remains valid."""
    limit = 64
    monkeypatch.setattr(_protocol_module, "MAX_MESSAGE_SIZE", limit)
    empty_size = len(_protocol_module.json.dumps({"value": ""}).encode("utf-8"))
    payload = {"value": "x" * (limit - empty_size)}
    sender, receiver = _protocol_module.socket.socketpair()
    try:
        _protocol_module.send_message(sender, payload)
        assert _protocol_module.recv_message(receiver, timeout=1.0) == payload
    finally:
        sender.close()
        receiver.close()


def test_recv_message_idle_wait_starts_deadline_on_first_byte(monkeypatch):
    """An idle persistent connection remains valid until a frame starts."""
    sender, receiver = _protocol_module.socket.socketpair()
    monkeypatch.setattr(_protocol_module, "MESSAGE_TIMEOUT", 0.05)
    received = {}

    def receive():
        received["message"] = _protocol_module.recv_message(receiver)

    thread = threading.Thread(target=receive, daemon=True)
    thread.start()
    time.sleep(0.1)
    assert thread.is_alive()
    _protocol_module.send_message(sender, {"ready": True})
    thread.join(timeout=1.0)
    try:
        assert received == {"message": {"ready": True}}
    finally:
        sender.close()
        receiver.close()


def test_recv_message_uses_absolute_frame_deadline(monkeypatch):
    """Small fragments cannot extend the total frame read deadline."""
    import struct

    sender, receiver = _protocol_module.socket.socketpair()
    monkeypatch.setattr(_protocol_module, "MESSAGE_TIMEOUT", 0.05)
    body = b'{"slow":true}'
    frame = struct.pack(">I", len(body)) + body

    def send_slowly():
        try:
            for byte in frame:
                sender.sendall(bytes([byte]))
                time.sleep(0.02)
        except OSError:
            pass

    thread = threading.Thread(target=send_slowly, daemon=True)
    thread.start()
    try:
        with pytest.raises(_protocol_module.socket.timeout):
            _protocol_module.recv_message(receiver)
    finally:
        sender.close()
        receiver.close()
        thread.join(timeout=1.0)


def test_send_message_times_out_when_peer_does_not_read():
    """A peer that stops reading cannot block a sender indefinitely."""
    sender, receiver = _protocol_module.socket.socketpair()
    payload = {"value": "x" * (_protocol_module.MAX_MESSAGE_SIZE // 2)}
    try:
        with pytest.raises(_protocol_module.socket.timeout):
            _protocol_module.send_message(sender, payload, timeout=0.05)
    finally:
        sender.close()
        receiver.close()


def test_protocol_io_preserves_socket_timeout():
    """Protocol reads and writes must not mutate socket timeout state."""
    sender, receiver = _protocol_module.socket.socketpair()
    sender.settimeout(2.0)
    receiver.settimeout(3.0)
    try:
        _protocol_module.send_message(sender, {"ok": True})
        assert _protocol_module.recv_message(receiver, timeout=1.0) == {"ok": True}
        assert sender.gettimeout() == 2.0
        assert receiver.gettimeout() == 3.0
    finally:
        sender.close()
        receiver.close()


def test_protocol_supports_file_descriptors_above_select_limit():
    """Readiness polling must support daemon sockets whose fd exceeds 1023."""
    import fcntl

    sender, receiver = _protocol_module.socket.socketpair()
    try:
        try:
            high_fd = fcntl.fcntl(receiver.fileno(), fcntl.F_DUPFD, 1024)
        except OSError as exc:
            pytest.skip(f"Cannot allocate a high file descriptor: {exc}")
        high_receiver = _protocol_module.socket.socket(fileno=high_fd)
        receiver.close()
        try:
            _protocol_module.send_message(sender, {"high": True})
            assert _protocol_module.recv_message(high_receiver, timeout=1.0) == {"high": True}
        finally:
            high_receiver.close()
    finally:
        sender.close()
        receiver.close()


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


def test_server_serializes_broadcasts_with_rpc_responses(tmp_path):
    """Notifications and RPC responses must never interleave on one stream."""
    class RecordingClient(BaseClient):
        def __init__(self, socket_path):
            super().__init__(socket_path, role="cli")
            self.notifications = []

        def handle_notification(self, data):
            self.notifications.append(data)

    server = MasterServer(tmp_path / "broadcast.sock")
    server.start()
    client = RecordingClient(server.socket_path)
    client.connect()
    errors = []

    def broadcast_many():
        try:
            for index in range(20):
                server.broadcast({
                    "type": "notification",
                    "event": "test",
                    "index": index,
                    "padding": "x" * 32768,
                })
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=broadcast_many, daemon=True)
    thread.start()
    try:
        for _ in range(20):
            assert client.send_command("ping") == {"message": "pong"}
        thread.join(timeout=5.0)
        deadline = time.monotonic() + 2.0
        while len(client.notifications) < 20 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not errors
        assert len(client.notifications) == 20
        assert client.is_connected
    finally:
        client.disconnect()
        server.stop()


def test_send_command_does_not_hold_send_lock_while_waiting(monkeypatch):
    """A waiting RPC must not block another caller from sending its command."""
    client = BaseClient("/tmp/not-used.sock", role="cli")
    client._connected = True
    client._sock = object()
    sent_ids = []
    first_sent = threading.Event()
    second_sent = threading.Event()

    def fake_send_message(sock, data, timeout=None):
        sent_ids.append(data["request_id"])
        if len(sent_ids) == 1:
            first_sent.set()
        if len(sent_ids) == 2:
            second_sent.set()

    monkeypatch.setattr(_base_module, "send_message", fake_send_message)

    results = {}

    def call(name):
        results[name] = client.send_command("slow", recv_timeout=1.0, name=name)

    first = threading.Thread(target=call, args=("first",))
    first.start()

    assert first_sent.wait(1.0)

    second = threading.Thread(target=call, args=("second",))
    second.start()

    assert second_sent.wait(0.2), "second send was blocked by first response wait"

    for request_id, value in zip(sent_ids, ("first", "second")):
        with client._pending_lock:
            response_queue = client._pending_responses[request_id]
        response_queue.put({"status": 200, "message": "OK", "data": value, "request_id": request_id})

    first.join(timeout=2)
    second.join(timeout=2)

    assert results == {"first": "first", "second": "second"}


def test_route_response_matches_request_id_out_of_order():
    """Out-of-order responses must be delivered to the matching pending queue."""
    client = BaseClient("/tmp/not-used.sock", role="cli")
    q1 = queue.Queue()
    q2 = queue.Queue()
    with client._pending_lock:
        client._pending_responses["one"] = q1
        client._pending_responses["two"] = q2

    assert client._route_response({"status": 200, "data": 2, "request_id": "two"}) is True
    assert client._route_response({"status": 200, "data": 1, "request_id": "one"}) is True

    assert q1.get_nowait()["data"] == 1
    assert q2.get_nowait()["data"] == 2


def test_route_response_drops_uncorrelated_and_duplicate_messages():
    """Uncorrelated or duplicate responses must not block the listener."""
    client = BaseClient("/tmp/not-used.sock", role="cli")
    pending = queue.Queue(maxsize=1)
    with client._pending_lock:
        client._pending_responses["req"] = pending

    assert client._route_response({"status": 200, "data": "missing-id"}) is False
    assert client._route_response({"status": 200, "data": "bad-id", "request_id": []}) is False
    assert pending.empty()

    assert client._route_response({"status": 200, "data": "first", "request_id": "req"}) is True
    assert client._route_response({"status": 200, "data": "second", "request_id": "req"}) is False
    assert pending.get_nowait()["data"] == "first"


def test_stale_late_response_is_dropped(monkeypatch):
    """A timed-out response must not be available to satisfy a later request."""
    client = BaseClient("/tmp/not-used.sock", role="cli")
    client._connected = True
    client._sock = object()
    sent_ids = []

    def fake_send_message(sock, data, timeout=None):
        sent_ids.append(data["request_id"])

    monkeypatch.setattr(_base_module, "send_message", fake_send_message)

    with pytest.raises(TimeoutError):
        client.send_command("slow", recv_timeout=0.01)

    old_id = sent_ids[0]
    assert client._route_response({"status": 200, "data": "late", "request_id": old_id}) is False

    def respond_current(sock, data, timeout=None):
        sent_ids.append(data["request_id"])
        request_id = data["request_id"]
        with client._pending_lock:
            response_queue = client._pending_responses[request_id]
        response_queue.put({"status": 200, "message": "OK", "data": "fresh", "request_id": request_id})

    monkeypatch.setattr(_base_module, "send_message", respond_current)
    assert client.send_command("fresh", recv_timeout=1.0) == "fresh"


def test_notification_does_not_unblock_pending_rpc():
    """Notifications must be handled separately from RPC response queues."""
    class RecordingClient(BaseClient):
        def __init__(self):
            super().__init__("/tmp/not-used.sock", role="cli")
            self.notifications = []

        def handle_notification(self, data):
            self.notifications.append(data)

    client = RecordingClient()
    pending = queue.Queue()
    with client._pending_lock:
        client._pending_responses["req"] = pending

    client._handle_incoming_message({"type": "notification", "event": "job_finished"})

    assert client.notifications == [{"type": "notification", "event": "job_finished"}]
    assert pending.empty()


def test_handle_incoming_message_drops_invalid_messages():
    """Malformed top-level JSON messages must not crash the listener."""
    client = BaseClient("/tmp/not-used.sock", role="cli")

    client._handle_incoming_message("not-a-dict")
    client._handle_incoming_message(["not", "a", "dict"])

    assert client._pending_responses == {}


def test_listener_disconnect_wakes_pending_requests_quickly(monkeypatch):
    """Pending RPCs should receive ConnectionError when the listener dies."""
    client = BaseClient("/tmp/not-used.sock", role="cli")
    client._connected = True
    client._sock = object()

    errors = []
    ready = threading.Event()

    def fake_send_message(sock, data, timeout=None):
        ready.set()

    monkeypatch.setattr(_base_module, "send_message", fake_send_message)

    def call_hanging_rpc():
        try:
            client.send_command("hang", recv_timeout=10.0)
        except ConnectionError as exc:
            errors.append(exc)

    thread = threading.Thread(target=call_hanging_rpc, daemon=True)
    thread.start()
    assert ready.wait(1.0)
    client._connected = False
    client._wake_pending_connection_error()
    thread.join(timeout=1.0)

    assert errors and isinstance(errors[0], ConnectionError)


def test_listener_protocol_error_closes_socket_and_wakes_pending():
    """A corrupt frame poisons the client connection and wakes RPC waiters."""
    import struct

    sender, receiver = _protocol_module.socket.socketpair()
    client = BaseClient("/tmp/not-used.sock", role="cli")
    client._connected = True
    client._sock = receiver
    response_queue = queue.Queue(maxsize=1)
    with client._pending_lock:
        client._pending_responses["pending"] = response_queue

    thread = threading.Thread(target=client._listen_loop, daemon=True)
    thread.start()
    sender.sendall(struct.pack(">I", 2) + b"[]")
    thread.join(timeout=1.0)
    try:
        assert not client.is_connected
        assert client._sock is None
        assert response_queue.get_nowait() is _base_module._CONNECTION_ERROR_SENTINEL
    finally:
        sender.close()
        receiver.close()


def test_send_failure_poisons_client_connection(monkeypatch):
    """A failed frame send closes the socket because the stream may be desynchronized."""
    sender, receiver = _protocol_module.socket.socketpair()
    client = BaseClient("/tmp/not-used.sock", role="cli")
    client._connected = True
    client._sock = sender

    def fail_send(sock, data, timeout=None):
        raise socket.timeout("timed out")

    monkeypatch.setattr(_base_module, "send_message", fail_send)
    try:
        with pytest.raises(ConnectionError, match="Failed to send"):
            client.send_command("ping")
        assert not client.is_connected
        assert client._sock is None
    finally:
        sender.close()
        receiver.close()


def test_server_connection_cap_rejects_and_recovers_pending_slot(tmp_path, monkeypatch):
    """Pending handshakes count toward the cap and their slots are reusable."""
    monkeypatch.setattr(_base_module, "MAX_CONNECTIONS", 2)
    server = MasterServer(tmp_path / "limited.sock")
    server.start()
    pending = []
    try:
        for _ in range(2):
            raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            raw.connect(str(server.socket_path))
            _protocol_module.recv_message(raw, timeout=1.0)
            pending.append(raw)

        rejected = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        rejected.settimeout(1.0)
        rejected.connect(str(server.socket_path))
        try:
            assert rejected.recv(1) == b""
        finally:
            rejected.close()

        pending.pop().close()
        deadline = time.monotonic() + 2.0
        while True:
            client = MasterClient(server.socket_path)
            try:
                client.connect()
                break
            except (ConnectionError, OSError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        try:
            assert client.ping() == {"message": "pong"}
        finally:
            client.disconnect()
    finally:
        for raw in pending:
            raw.close()
        server.stop()


def test_server_stop_closes_pending_handshake(tmp_path):
    """Stopping a server closes clients still waiting in the handshake."""
    server = MasterServer(tmp_path / "pending-stop.sock")
    server.start()
    raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    raw.settimeout(1.0)
    raw.connect(str(server.socket_path))
    _protocol_module.recv_message(raw, timeout=1.0)
    server.stop()
    try:
        assert raw.recv(1) == b""
    finally:
        raw.close()


def test_server_send_timeout_releases_connection_slot(tmp_path, monkeypatch):
    """A client that does not read a response is closed and releases its slot."""
    monkeypatch.setattr(_base_module, "MAX_CONNECTIONS", 1)
    monkeypatch.setattr(_base_module, "MESSAGE_TIMEOUT", 0.05)
    server = MasterServer(tmp_path / "blocked-send.sock")
    server.register_handler("large", lambda: {"padding": "x" * (4 * 1024 * 1024)})
    server.start()
    raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    raw.connect(str(server.socket_path))
    try:
        _protocol_module.recv_message(raw, timeout=1.0)
        _protocol_module.send_message(raw, {
            "status": 200,
            "message": "Handshake",
            "data": {"info": {
                "app_name": APP_NAME,
                "app_version": "raw",
                "protocol_version": PROTOCOL_VERSION,
                "is_server": False,
                "role": "cli",
            }},
        })
        _protocol_module.recv_message(raw, timeout=1.0)
        _protocol_module.send_message(raw, {
            "command": "large",
            "kwargs": None,
            "request_id": "large-response",
        })

        deadline = time.monotonic() + 2.0
        while server.client_count and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.client_count == 0

        with MasterClient(server.socket_path) as client:
            assert client.ping() == {"message": "pong"}
    finally:
        raw.close()
        server.stop()


def test_handler_thread_start_failure_releases_slot(tmp_path, monkeypatch):
    """Failure to start a handler thread must not consume a connection slot."""
    monkeypatch.setattr(_base_module, "MAX_CONNECTIONS", 1)
    server = MasterServer(tmp_path / "thread-start.sock")
    server.start()
    original_start = threading.Thread.start
    failed = False

    def fail_once(thread):
        nonlocal failed
        if not failed and getattr(thread, "_target", None) == server._serve_connection:
            failed = True
            raise RuntimeError("cannot start thread")
        return original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_once)
    raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    raw.settimeout(1.0)
    raw.connect(str(server.socket_path))
    try:
        assert raw.recv(1) == b""
        with MasterClient(server.socket_path) as client:
            assert client.ping() == {"message": "pong"}
    finally:
        raw.close()
        server.stop()


def test_protocol_version_mismatch_rejected_by_handshake(tmp_path):
    """Protocol version mismatches must fail during handshake."""
    from mirror.socket.protocol import recv_message, send_message
    from mirror.socket.master import MasterServer

    server = MasterServer(tmp_path / "version.sock")
    server.start()
    try:
        raw = _base_module.socket.socket(_base_module.socket.AF_UNIX, _base_module.socket.SOCK_STREAM)
        raw.connect(str(server.socket_path))
        try:
            recv_message(raw, timeout=5.0)
            send_message(raw, {
                "status": 200,
                "message": "Handshake",
                "data": {"info": {
                    "app_name": APP_NAME,
                    "app_version": "old",
                    "protocol_version": PROTOCOL_VERSION - 1,
                    "is_server": False,
                    "role": "cli",
                }},
            })
            response = recv_message(raw, timeout=5.0)
        finally:
            raw.close()
    finally:
        server.stop()

    assert response["status"] == 400
    assert "Protocol version mismatch" in response["message"]


def test_server_echoes_request_id_for_rpc_response(tmp_path):
    """Servers must echo request_id so clients can correlate responses."""
    from mirror.socket.protocol import recv_message, send_message
    from mirror.socket.master import MasterServer

    server = MasterServer(tmp_path / "echo.sock")
    server.start()
    try:
        raw = _base_module.socket.socket(_base_module.socket.AF_UNIX, _base_module.socket.SOCK_STREAM)
        raw.connect(str(server.socket_path))
        try:
            recv_message(raw, timeout=5.0)
            send_message(raw, {
                "status": 200,
                "message": "Handshake",
                "data": {"info": {
                    "app_name": APP_NAME,
                    "app_version": "raw",
                    "protocol_version": PROTOCOL_VERSION,
                    "is_server": False,
                    "role": "cli",
                }},
            })
            confirm = recv_message(raw, timeout=5.0)
            assert confirm["status"] == 200

            send_message(raw, {"command": "ping", "kwargs": None, "request_id": "abc123"})
            response = recv_message(raw, timeout=5.0)

            send_message(raw, {"command": "ping", "kwargs": None})
            legacy_response = recv_message(raw, timeout=5.0)
        finally:
            raw.close()
    finally:
        server.stop()

    assert response["status"] == 200
    assert response["request_id"] == "abc123"
    assert "request_id" not in legacy_response


def test_client_rejects_server_protocol_version_mismatch(tmp_path):
    """Clients must reject a server that advertises another protocol version."""
    from mirror.socket.protocol import recv_message, send_message

    socket_path = tmp_path / "client-version.sock"
    server = _base_module.socket.socket(_base_module.socket.AF_UNIX, _base_module.socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)

    def accept_once():
        conn, _ = server.accept()
        try:
            send_message(conn, {
                "status": 200,
                "message": "Handshake",
                "data": {"info": {
                    "app_name": APP_NAME,
                    "app_version": "old",
                    "protocol_version": PROTOCOL_VERSION - 1,
                    "is_server": True,
                    "role": "master",
                }},
            })
            recv_message(conn, timeout=1.0)
        except Exception:
            pass
        finally:
            conn.close()

    thread = threading.Thread(target=accept_once, daemon=True)
    thread.start()
    try:
        client = BaseClient(socket_path, role="cli")
        with pytest.raises(ConnectionError, match="Protocol version mismatch"):
            client.connect()
    finally:
        server.close()
        thread.join(timeout=1.0)


def test_master_get_runtime_info(monkeypatch):
    """get_runtime_info RPC returns curated daemon config fields."""
    import mirror
    import mirror.sync
    import tempfile

    class FakeConf:
        name = "mymirror"
        hostname = "m.example"
        localtimezone = "UTC"
        logfolder = Path("/var/log/mirror")
        webroot = Path("/var/www/mirror")
        max_runtime_seconds = 43200
        errorcontinuetime = 60
        logger = {"packagefileformat": {"base": "/var/log/mirror/packages"}}

    monkeypatch.setattr(mirror, "conf", FakeConf(), raising=False)
    monkeypatch.setattr(mirror.sync, "methods", ["rsync", "ftpsync"], raising=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = Path(tmpdir) / "master.sock"
        server = MasterServer(socket_path)
        server.start()
        time.sleep(0.1)

        try:
            with MasterClient(socket_path) as client:
                result = client.get_runtime_info()
                assert result["mirrorname"] == "mymirror"
                assert result["hostname"] == "m.example"
                assert result["localtimezone"] == "UTC"
                assert result["logfolder"] == "/var/log/mirror"
                assert result["webroot"] == "/var/www/mirror"
                assert result["log_base"] == "/var/log/mirror/packages"
                assert result["max_runtime_seconds"] == 43200
                assert result["errorcontinuetime"] == 60
                assert result["sync_methods"] == ["rsync", "ftpsync"]
                assert isinstance(result["daemon_started_at"], float)
                assert abs(result["daemon_started_at"] - time.time()) < 60
        finally:
            server.stop()


class TestMasterSocketPermissions:
    """Test master socket ownership and mode configuration"""

    def test_default_mode_is_0600(self, monkeypatch):
        """MasterServer with no socket config defaults to mode 0o600"""
        monkeypatch.setattr(mirror, "conf", types.SimpleNamespace(socket=None), raising=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            server = MasterServer(socket_path)
            server.start()
            try:
                actual_mode = stat.S_IMODE(os.stat(socket_path).st_mode)
                assert actual_mode == 0o600
            finally:
                server.stop()

    def test_configured_mode_applied(self, monkeypatch):
        """MasterServer applies the configured socket mode"""
        monkeypatch.setattr(
            mirror, "conf",
            types.SimpleNamespace(socket=Config.SocketSettings(uid=None, gid=None, mode=0o660)),
            raising=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            server = MasterServer(socket_path)
            server.start()
            try:
                actual_mode = stat.S_IMODE(os.stat(socket_path).st_mode)
                assert actual_mode == 0o660
            finally:
                server.stop()

    def test_chown_uses_zero_not_minus_one(self, monkeypatch):
        """uid/gid of 0 must be forwarded as 0 to os.chown, not treated as falsy"""
        chown_calls = []

        def fake_chown(path, uid, gid):
            chown_calls.append((path, uid, gid))

        monkeypatch.setattr(
            mirror, "conf",
            types.SimpleNamespace(socket=Config.SocketSettings(uid=0, gid=0, mode=0o660)),
            raising=False,
        )
        monkeypatch.setattr(_base_module.os, "chown", fake_chown)

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            server = MasterServer(socket_path)
            server.start()
            try:
                assert len(chown_calls) == 1, f"expected 1 chown call, got {chown_calls}"
                _, uid, gid = chown_calls[0]
                assert uid == 0, f"expected uid=0, got {uid}"
                assert gid == 0, f"expected gid=0, got {gid}"
                actual_mode = stat.S_IMODE(os.stat(socket_path).st_mode)
                assert actual_mode == 0o660
            finally:
                server.stop()

    def test_perm_failure_aborts_and_cleans_up(self, monkeypatch):
        """A chown failure during start() must abort (raise OSError) and clean up the socket"""
        def fake_chown(path, uid, gid):
            raise OSError("EPERM")

        monkeypatch.setattr(
            mirror, "conf",
            types.SimpleNamespace(socket=Config.SocketSettings(uid=12345, gid=None, mode=0o660)),
            raising=False,
        )
        monkeypatch.setattr(_base_module.os, "chown", fake_chown)

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "master.sock"
            server = MasterServer(socket_path)
            with pytest.raises(OSError):
                server.start()
            assert not socket_path.exists(), "socket file must be cleaned up after perm failure"
            assert server.running is False

    def test_worker_socket_stays_0600(self):
        """WorkerServer must keep mode 0o600 regardless of master socket config"""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "worker.sock"
            server = WorkerServer(socket_path)
            server.start()
            try:
                actual_mode = stat.S_IMODE(os.stat(socket_path).st_mode)
                assert actual_mode == 0o600
            finally:
                server.stop()
