from unittest.mock import MagicMock

import pytest

import mirror.sync.lftp as lftp


def _package(src: str, dst: str = "/tmp/mirror"):
    package = MagicMock()
    package.pkgid = "pkg"
    package.name = "Pkg"
    package.settings.src = src
    package.settings.dst = dst
    return package


def _logger(tmp_path):
    logger = MagicMock()
    handler = MagicMock()
    handler.baseFilename = str(tmp_path / "pkg.log")
    logger.handlers = [handler]
    return logger


def test_lftp_valid_source_delegates_to_worker(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("mirror.socket.worker.execute_command", lambda **kwargs: calls.append(kwargs))

    lftp.execute(_package("example.org/debian"), _logger(tmp_path))

    assert len(calls) == 1
    assert calls[0]["commandline"][0] == "lftp"


@pytest.mark.parametrize(
    "src",
    [
        "ftp://example.org/debian",
        "example.org; !touch /tmp/pwn",
        "example.org `touch /tmp/pwn`",
        "example.org/de\\bian",
        "example.org/de*bian",
        "example.org/debian#comment",
        "example.org/../debian",
        "user@example.org/debian",
        "example.org/deb ian",
    ],
)
def test_lftp_rejects_unsafe_source_before_worker_rpc(src, tmp_path, monkeypatch):
    calls = []
    done = []
    monkeypatch.setattr("mirror.socket.worker.execute_command", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("mirror.sync.on_sync_done", lambda pkgid, success, returncode: done.append(success))

    lftp.execute(_package(src), _logger(tmp_path))

    assert calls == []
    assert done == [False]


@pytest.mark.parametrize("dst", ["/tmp/mirror\x00bad", "/tmp/mirror bad", "-bad", "/tmp/mirror;bad"])
def test_lftp_rejects_unsafe_destination_before_worker_rpc(dst, tmp_path, monkeypatch):
    calls = []
    done = []
    monkeypatch.setattr("mirror.socket.worker.execute_command", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("mirror.sync.on_sync_done", lambda pkgid, success, returncode: done.append(success))

    lftp.execute(_package("example.org/debian", dst=dst), _logger(tmp_path))

    assert calls == []
    assert done == [False]
