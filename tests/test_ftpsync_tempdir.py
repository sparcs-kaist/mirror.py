"""ftpsync tempdir is created under STATE_PATH and cleaned up via handle map."""
from pathlib import Path
from unittest.mock import patch, MagicMock

import mirror
import mirror.sync.ftpsync as ftpsync_mod


def _make_pkg(pkgid: str = "ft"):
    pkg = MagicMock()
    pkg.pkgid = pkgid
    pkg.name = "FtPkg"
    pkg.settings.src = "src"
    pkg.settings.dst = "/tmp/dst"
    pkg.settings.options = {
        "email": "a@b",
        "hub": "h",
        "path": "/p",
    }
    return pkg


def test_tempdir_under_state_path_and_cleanup_via_handle_map(monkeypatch, tmp_path):
    monkeypatch.setattr(mirror, "STATE_PATH", tmp_path, raising=False)

    pkg = _make_pkg("ftp_temp")

    captured_path = {}

    def fake_setup(path, p, log_dir=None, log_name=None):
        captured_path["path"] = path

    monkeypatch.setattr(ftpsync_mod, "setup_ftpsync", fake_setup)
    monkeypatch.setattr("mirror.socket.worker.execute_command", MagicMock())

    logger = MagicMock(handlers=[])
    ftpsync_mod.execute(pkg, logger)

    # Verify temp path is under STATE_PATH
    p = captured_path["path"]
    assert str(p).startswith(str(tmp_path)), f"{p} should be under {tmp_path}"
    assert p.exists()

    # No dynamic attribute was explicitly set on Package
    assert "_ftpsync_tmp" not in pkg._mock_children, "Package should not gain _ftpsync_tmp"

    # Handle map records the entry
    assert pkg.pkgid in ftpsync_mod._ftpsync_handles

    # Cleanup via on_sync_done
    ftpsync_mod.on_sync_done(pkg, logger, True, 0)
    assert pkg.pkgid not in ftpsync_mod._ftpsync_handles
    assert not p.exists()
