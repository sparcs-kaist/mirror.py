"""Tests for the _worker_client dispatcher reuse logic in mirror.socket.worker."""

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mirror.socket.worker as worker_module
from mirror.socket.worker import WorkerClient, WorkerServer, _worker_client


def _make_connected_client_mock() -> MagicMock:
    """Return a MagicMock with spec=WorkerClient and is_connected=True."""
    mock = MagicMock(spec=WorkerClient)
    mock.is_connected = True
    return mock


def _make_cm_mock() -> MagicMock:
    """Return a MagicMock that behaves as a context manager yielding itself."""
    cm = MagicMock(spec=WorkerClient)
    cm.is_connected = False
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def test_execute_command_reuses_supervised_client_when_connected(monkeypatch):
    """When _instance is a connected WorkerClient, no new client is constructed."""
    instance_mock = _make_connected_client_mock()
    instance_mock.execute_command.return_value = {"status": "started"}
    monkeypatch.setattr(worker_module, "_instance", instance_mock)

    with patch.object(worker_module, "WorkerClient") as cls_mock:
        worker_module.execute_command(job_id="j", commandline=["echo"], env={}, uid=1000, gid=1000)

    assert cls_mock.call_count == 0
    instance_mock.execute_command.assert_called_once()


def test_execute_command_rejects_missing_uid_gid(monkeypatch):
    instance_mock = _make_connected_client_mock()
    monkeypatch.setattr(worker_module, "_instance", instance_mock)

    with pytest.raises(ValueError, match="explicit uid and gid"):
        worker_module.execute_command(job_id="j", commandline=["echo"], env={})

    instance_mock.execute_command.assert_not_called()


def test_worker_server_rejects_missing_uid_gid(tmp_path, monkeypatch):
    import mirror.worker.process as process_module

    monkeypatch.setattr(process_module, "prune_finished", lambda: None)
    server = WorkerServer(socket_path=tmp_path / "worker.sock")

    with pytest.raises(ValueError, match="explicit uid and gid"):
        server._handle_execute_command(job_id="j", commandline=["echo"], env={})


def test_helper_falls_back_when_instance_is_none(monkeypatch):
    """When _instance is None, a temporary WorkerClient is constructed."""
    monkeypatch.setattr(worker_module, "_instance", None)

    cm_mock = _make_cm_mock()
    with patch.object(worker_module, "WorkerClient", return_value=cm_mock) as cls_mock:
        try:
            worker_module.ping()
        except Exception:
            pass

    assert cls_mock.call_count == 1
    cm_mock.__enter__.assert_called_once()
    cm_mock.ping.assert_called_once()


def test_helper_falls_back_when_instance_disconnected(monkeypatch):
    """When _instance.is_connected is False, a temporary WorkerClient is constructed."""
    instance_mock = MagicMock(spec=WorkerClient)
    instance_mock.is_connected = False
    monkeypatch.setattr(worker_module, "_instance", instance_mock)

    cm_mock = _make_cm_mock()
    with patch.object(worker_module, "WorkerClient", return_value=cm_mock) as cls_mock:
        try:
            worker_module.ping()
        except Exception:
            pass

    assert cls_mock.call_count == 1
    cm_mock.__enter__.assert_called_once()
    cm_mock.ping.assert_called_once()


def test_helper_falls_back_when_instance_is_workerserver(monkeypatch):
    """When _instance is a WorkerServer (not WorkerClient), a temporary client is used."""
    server_mock = MagicMock(spec=WorkerServer)
    monkeypatch.setattr(worker_module, "_instance", server_mock)

    cm_mock = _make_cm_mock()
    with patch.object(worker_module, "WorkerClient", return_value=cm_mock) as cls_mock:
        try:
            worker_module.ping()
        except Exception:
            pass

    assert cls_mock.call_count == 1
    cm_mock.__enter__.assert_called_once()
    cm_mock.ping.assert_called_once()


def test_helper_falls_back_when_socket_path_is_explicit(monkeypatch):
    """When socket_path is explicitly given, always opens a temporary client."""
    instance_mock = _make_connected_client_mock()
    monkeypatch.setattr(worker_module, "_instance", instance_mock)

    cm_mock = _make_cm_mock()
    with patch.object(worker_module, "WorkerClient", return_value=cm_mock) as cls_mock:
        try:
            worker_module.ping(socket_path="/tmp/x.sock")
        except Exception:
            pass

    assert cls_mock.call_count == 1
    cm_mock.__enter__.assert_called_once()
    cm_mock.ping.assert_called_once()


def test_is_worker_running_uses_reuse_path(monkeypatch):
    """is_worker_running routes through the persistent client when connected."""
    instance_mock = _make_connected_client_mock()
    instance_mock.ping.return_value = {"message": "pong"}
    instance_mock.get_progress.return_value = {"syncing": True}
    monkeypatch.setattr(worker_module, "_instance", instance_mock)

    result_no_job = worker_module.is_worker_running()
    assert result_no_job is True
    instance_mock.ping.assert_called_once()

    result_with_job = worker_module.is_worker_running(job_id="p")
    assert result_with_job is True
    instance_mock.get_progress.assert_called_once_with("p")

    instance_mock.get_progress.return_value = {"syncing": False}
    result_not_syncing = worker_module.is_worker_running(job_id="p")
    assert result_not_syncing is False


def test_dispatcher_uses_snapshot_after_yield(monkeypatch):
    """Flipping _instance to None after yield does not affect the in-flight call."""
    original_mock = _make_connected_client_mock()
    original_mock.ping.return_value = {"message": "pong"}
    monkeypatch.setattr(worker_module, "_instance", original_mock)

    with _worker_client() as client:
        monkeypatch.setattr(worker_module, "_instance", None)
        result = client.ping()

    assert client is original_mock
    assert result == {"message": "pong"}


def test_execute_command_forwards_all_kwargs(monkeypatch):
    """All kwargs are forwarded to WorkerClient.execute_command with correct conversions."""
    instance_mock = _make_connected_client_mock()
    instance_mock.execute_command.return_value = {"status": "started"}
    monkeypatch.setattr(worker_module, "_instance", instance_mock)

    log = Path("/var/log/mirror/test.log")

    worker_module.execute_command(
        job_id="j1",
        commandline=["rsync", "-av"],
        env={"KEY": "val"},
        sync_method="rsync",
        uid=1000,
        gid=1000,
        nice=5,
        log_path=log,
    )

    instance_mock.execute_command.assert_called_once_with(
        "j1",
        ["rsync", "-av"],
        {"KEY": "val"},
        sync_method="rsync",
        uid=1000,
        gid=1000,
        nice=5,
        log_path=str(log),
    )
