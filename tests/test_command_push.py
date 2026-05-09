"""Tests for mirror.command.push"""
import sys
from unittest.mock import MagicMock

import pytest

import mirror
import mirror.command.push as push_mod
from mirror.command.push import push


@pytest.fixture(autouse=True)
def _mock_config_load(monkeypatch):
    monkeypatch.setattr("mirror.config.load", lambda path: None)


@pytest.fixture(autouse=True)
def _reset_packages():
    original = getattr(mirror, "packages", None)
    yield
    if original is not None:
        mirror.packages = original


def test_push_unknown_package(monkeypatch):
    monkeypatch.setattr(mirror, "packages", {}, raising=False)

    with pytest.raises(SystemExit) as ei:
        push("nope", "/dev/null")

    assert ei.value.code == 2


def test_push_master_not_running(monkeypatch):
    pkg = MagicMock()
    monkeypatch.setattr(mirror, "packages", {"debian": pkg}, raising=False)
    monkeypatch.setattr("mirror.socket.master.is_master_running", lambda: False)

    with pytest.raises(SystemExit) as ei:
        push("debian", "/dev/null")

    assert ei.value.code == 1


def test_push_success_no_ssh_env(monkeypatch):
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)

    pkg = MagicMock()
    monkeypatch.setattr(mirror, "packages", {"debian": pkg}, raising=False)
    monkeypatch.setattr("mirror.socket.master.is_master_running", lambda: True)

    calls: list[dict] = []

    def fake_push_sync(pkgid, extra_args=None):
        calls.append({"pkgid": pkgid, "extra_args": extra_args})
        return {"package_id": pkgid, "status": "started"}

    monkeypatch.setattr("mirror.socket.master.push_sync", fake_push_sync)

    # Should not raise SystemExit
    push("debian", "/dev/null")

    assert len(calls) == 1
    assert calls[0]["extra_args"] == {}


def test_push_success_with_ssh_env(monkeypatch):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "ftpsync sync:archive:debian")
    monkeypatch.setenv("SSH_CONNECTION", "203.0.113.10 54321 198.51.100.5 22")

    pkg = MagicMock()
    monkeypatch.setattr(mirror, "packages", {"debian": pkg}, raising=False)
    monkeypatch.setattr("mirror.socket.master.is_master_running", lambda: True)

    calls: list[dict] = []

    def fake_push_sync(pkgid, extra_args=None):
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

    pkg = MagicMock()
    monkeypatch.setattr(mirror, "packages", {"debian": pkg}, raising=False)
    monkeypatch.setattr("mirror.socket.master.is_master_running", lambda: True)
    monkeypatch.setattr(
        "mirror.socket.master.push_sync",
        lambda pkgid, extra_args=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(SystemExit) as ei:
        push("debian", "/dev/null")

    assert ei.value.code == 3
