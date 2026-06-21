"""Verify the git-clone provisioning path in mirror.sync.ftpsync.

These tests reach the real archvsync repository over the network, so they are
marked `integration` (deselected by default) and skip cleanly when git is
missing or the remote is unreachable. They prove that the mirror.py functions
themselves drive the clone end to end: _clone_archvsync fetches a usable tree,
and setup_ftpsync lays out an executable bin/ftpsync that supports the
INFO_TRIGGER (-T) flag.
"""

import shutil
import subprocess

import pytest

import mirror.sync.ftpsync as ftpsync_mod

pytestmark = pytest.mark.integration


def _network_available() -> bool:
    """Return True if the archvsync remote can be reached via git ls-remote."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", ftpsync_mod.ARCHVSYNC_REPO],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return False


requires_git_remote = pytest.mark.skipif(
    shutil.which("git") is None or not _network_available(),
    reason="git unavailable or archvsync remote unreachable",
)


@requires_git_remote
def test_clone_archvsync_fetches_usable_tree(tmp_path):
    """_clone_archvsync clones a tree containing an executable bin/ftpsync."""
    ok = ftpsync_mod._clone_archvsync(tmp_path)
    assert ok, "_clone_archvsync reported failure against the real remote"

    ftpsync_script = tmp_path / "archvsync" / "bin" / "ftpsync"
    assert ftpsync_script.is_file(), f"{ftpsync_script} not present after clone"

    content = ftpsync_script.read_text()
    assert content.startswith("#!/usr/bin/env bash"), "cloned ftpsync missing bash shebang"
    assert "getopts T:" in content, "cloned ftpsync does not accept the -T INFO_TRIGGER flag"


@requires_git_remote
def test_setup_ftpsync_uses_clone_path_end_to_end(tmp_path, monkeypatch):
    """setup_ftpsync, with git present, provisions via clone and is consumable.

    Proves the clone branch (not the base64 fallback) runs through the public
    setup_ftpsync entry point and produces an executable bin/ftpsync plus a
    written ftpsync.conf.
    """
    from unittest.mock import MagicMock

    import mirror

    conf = MagicMock()
    conf.hostname = "mirror.test"
    conf.uid = None
    conf.gid = None
    conf.logfolder = str(tmp_path / "log")
    conf.ftpsync = MagicMock(
        maintainer="", sponsor="", country="", location="", throughput="", include="", exclude=""
    )
    monkeypatch.setattr(mirror, "conf", conf, raising=False)

    package = MagicMock()
    package.name = "debian"
    package.pkgid = "debian"
    package.settings.src = "rsync://ftp.debian.org/debian"
    package.settings.dst = str(tmp_path / "dst")
    package.settings.options = {"path": "/debian"}

    # Guard: prove the fallback is NOT what produced the result.
    monkeypatch.setattr(
        ftpsync_mod,
        "_extract_archvsync",
        lambda path: pytest.fail("fallback extraction ran; clone path was not taken"),
    )

    pkg_logger = MagicMock()
    ftpsync_mod.setup_ftpsync(tmp_path, package, logger=pkg_logger)

    ftpsync_script = tmp_path / "bin" / "ftpsync"
    assert ftpsync_script.is_file(), "bin/ftpsync missing after clone-based setup"
    assert ftpsync_script.stat().st_mode & 0o111, "bin/ftpsync is not executable"
    assert "getopts T:" in ftpsync_script.read_text(), "cloned ftpsync lacks -T support"

    conf_file = tmp_path / "etc" / "ftpsync.conf"
    assert conf_file.is_file(), "ftpsync.conf not written"

    # Provisioning notice must go to the supplied per-package logger.
    logged = " ".join(str(c.args[0]) for c in pkg_logger.info.call_args_list if c.args)
    assert "git clone" in logged, f"provisioning log not routed to package logger: {logged!r}"
