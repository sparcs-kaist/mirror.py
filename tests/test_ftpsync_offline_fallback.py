"""Verify ftpsync provisioning fallback and failure behavior."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import mirror
import mirror.sync.ftpsync as ftpsync_mod


@pytest.fixture
def ftpsync_context(monkeypatch, tmp_path):
    """Provide the minimum real configuration consumed by setup_ftpsync."""
    conf = SimpleNamespace(
        hostname="mirror.test",
        uid=None,
        gid=None,
        logfolder=str(tmp_path / "logs"),
        ftpsync=SimpleNamespace(
            maintainer="",
            sponsor="",
            country="",
            location="",
            throughput="",
            include="",
            exclude="",
        ),
    )
    package = SimpleNamespace(
        name="debian",
        pkgid="debian",
        settings=SimpleNamespace(
            src="rsync://upstream.example/debian",
            dst=str(tmp_path / "publish"),
            options={"path": "debian"},
        ),
    )
    monkeypatch.setattr(mirror, "conf", conf, raising=False)
    return package


def test_extract_succeeds_with_real_artifact(tmp_path):
    """The shipped artifact becomes the executable layout setup consumes."""
    assert ftpsync_mod._extract_archvsync(tmp_path) is True

    script = tmp_path / "archvsync" / "bin" / "ftpsync"
    assert script.is_file()
    assert script.read_bytes().startswith(b"#!/usr/bin/env bash")
    assert script.stat().st_mode & 0o111


@pytest.mark.parametrize(
    ("constant", "value"),
    [
        ("ARCHVSYNC_HASH", "0" * 64),
        ("ARCHVSYNC_SCRIPT", "!!! not valid base64 !!!"),
    ],
)
def test_extract_rejects_corrupt_artifact(tmp_path, monkeypatch, constant, value):
    """Corrupt bundled data fails without materializing a runnable tree."""
    monkeypatch.setattr(
        f"mirror.sync._ftpsync_script.{constant}", value, raising=False
    )

    assert ftpsync_mod._extract_archvsync(tmp_path) is False
    assert not (tmp_path / "archvsync").exists()


@pytest.mark.parametrize("git_available", [False, True])
def test_setup_uses_bundled_artifact_when_clone_unavailable(
    ftpsync_context, tmp_path, monkeypatch, git_available
):
    """Missing git and a failed clone both take the real bundled setup path."""
    clone = MagicMock(return_value=False)
    logger = MagicMock()
    monkeypatch.setattr(ftpsync_mod, "_check_git", lambda: git_available)
    monkeypatch.setattr(ftpsync_mod, "_clone_archvsync", clone)

    ftpsync_mod.setup_ftpsync(tmp_path, ftpsync_context, logger=logger)

    if git_available:
        clone.assert_called_once_with(tmp_path)
    else:
        clone.assert_not_called()
    script = tmp_path / "bin" / "ftpsync"
    assert script.is_file()
    assert script.stat().st_mode & 0o111
    assert (tmp_path / "etc" / "ftpsync.conf").is_file()
    logger.info.assert_called_once_with(
        "archvsync provisioned via bundled base64 script (git clone unavailable or failed)"
    )


def test_corrupt_fallback_fails_before_worker_delegation(
    ftpsync_context, tmp_path, monkeypatch
):
    """execute reports setup failure without creating a worker job."""
    worker_execute = MagicMock()
    sync_done = MagicMock()
    logger = MagicMock(handlers=[])
    monkeypatch.setattr(mirror, "STATE_PATH", tmp_path)
    monkeypatch.setattr(ftpsync_mod, "_check_git", lambda: False)
    monkeypatch.setattr(
        "mirror.sync._ftpsync_script.ARCHVSYNC_HASH", "0" * 64, raising=False
    )
    monkeypatch.setattr(mirror.socket.worker, "execute_command", worker_execute)
    monkeypatch.setattr(mirror.sync, "on_sync_done", sync_done)

    ftpsync_mod.execute(ftpsync_context, logger)

    worker_execute.assert_not_called()
    sync_done.assert_called_once_with("debian", success=False, returncode=None)
    assert "debian" not in ftpsync_mod._ftpsync_handles
    assert not list(tmp_path.glob("mirror_ftpsync_*"))
    assert "fallback extraction failed" in logger.error.call_args.args[0]
