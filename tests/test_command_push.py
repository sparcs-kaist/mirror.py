"""Tests for mirror.command.push"""
import importlib

import pytest

push_mod = importlib.import_module("mirror.command.push")
from mirror.command.push import push


@pytest.fixture(autouse=True)
def _mock_socket_resolution(monkeypatch):
    monkeypatch.setattr(push_mod, "_resolve_master_socket", lambda explicit: "/tmp/master.sock")


def test_push_master_not_running(monkeypatch):
    monkeypatch.setattr("mirror.socket.master.is_master_running", lambda socket_path=None: False)

    with pytest.raises(SystemExit) as ei:
        push("debian", "/dev/null")

    assert ei.value.code == 1


def test_push_success_no_ssh_env(monkeypatch):
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)

    monkeypatch.setattr("mirror.socket.master.is_master_running", lambda socket_path=None: True)

    calls: list[dict] = []

    def fake_push_sync(pkgid, extra_args=None, socket_path=None):
        calls.append({"pkgid": pkgid, "extra_args": extra_args, "socket_path": socket_path})
        return {"package_id": pkgid, "status": "started"}

    monkeypatch.setattr("mirror.socket.master.push_sync", fake_push_sync)

    # Should not raise SystemExit
    push("debian", "/dev/null")

    assert len(calls) == 1
    assert calls[0]["extra_args"] == {}
    assert calls[0]["socket_path"] == "/tmp/master.sock"


def test_push_success_with_ssh_env(monkeypatch):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "ftpsync sync:archive:debian")
    monkeypatch.setenv("SSH_CONNECTION", "203.0.113.10 54321 198.51.100.5 22")

    monkeypatch.setattr("mirror.socket.master.is_master_running", lambda socket_path=None: True)

    calls: list[dict] = []

    def fake_push_sync(pkgid, extra_args=None, socket_path=None):
        calls.append({"pkgid": pkgid, "extra_args": extra_args})
        return {"package_id": pkgid, "status": "started"}

    monkeypatch.setattr("mirror.socket.master.push_sync", fake_push_sync)

    push("debian", "/dev/null")

    assert len(calls) == 1
    assert calls[0]["extra_args"] == {
        "SSH_ORIGINAL_COMMAND": "ftpsync sync:archive:debian",
        "SSH_CONNECTION": "203.0.113.10 54321 198.51.100.5 22",
    }


def test_push_rpc_error_exits_3(monkeypatch):
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)

    monkeypatch.setattr("mirror.socket.master.is_master_running", lambda socket_path=None: True)
    monkeypatch.setattr(
        "mirror.socket.master.push_sync",
        lambda pkgid, extra_args=None, socket_path=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(SystemExit) as ei:
        push("debian", "/dev/null")

    assert ei.value.code == 3


def test_push_does_not_load_config_or_modify_stat(tmp_path, monkeypatch):
    stat_path = tmp_path / "stat.json"
    stat_path.write_text('{"packages": {"debian": {"lastsync": 123.0}}}')
    before = stat_path.read_text()

    monkeypatch.setattr("mirror.config.load", lambda path: (_ for _ in ()).throw(AssertionError("must not load config")))
    monkeypatch.setattr("mirror.socket.master.is_master_running", lambda socket_path=None: True)
    monkeypatch.setattr(
        "mirror.socket.master.push_sync",
        lambda pkgid, extra_args=None, socket_path=None: {"package_id": pkgid, "status": "started"},
    )

    push("debian", str(tmp_path / "config.json"))

    assert stat_path.read_text() == before
