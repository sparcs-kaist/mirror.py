"""Verify Package.set_status log-path and timestamp bookkeeping plus on_sync_done capture.

Requirements (used by the web status panel):
- ACTIVE → lastsuccesslog/lastsuccesstime set; lasterrorlog/lasterrortime/errorcount cleared.
- ERROR → lasterrorlog/lasterrortime set; errorcount += 1; lastsuccess* preserved.
- on_sync_done records the post-compression path (.log.gz when gzip is on),
  not the pre-compression .log path that no longer exists after compression.
"""
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.structure
import mirror.sync


def _make_package() -> mirror.structure.Package:
    settings = mirror.structure.PackageSettings(hidden=False, src="x", dst="y", options={})
    return mirror.structure.Package(
        pkgid="pkg",
        name="pkg",
        status="UNKNOWN",
        href="/pkg",
        synctype="rsync",
        syncrate=60,
        link=[],
        settings=settings,
    )


@pytest.fixture(autouse=True)
def _stub_event(monkeypatch):
    monkeypatch.setattr("mirror.event.post_event", lambda *a, **kw: None)


def test_active_sets_lastsuccesslog():
    pkg = _make_package()
    pkg.set_status("ACTIVE", logfile=Path("/var/log/mirror/packages/2026/05/06/12.pkg.log.gz"))
    assert pkg.statusinfo.lastsuccesslog == "/var/log/mirror/packages/2026/05/06/12.pkg.log.gz"


def test_error_sets_lasterrorlog():
    pkg = _make_package()
    pkg.set_status("ERROR", logfile=Path("/var/log/mirror/packages/err.log.gz"))
    assert pkg.statusinfo.lasterrorlog == "/var/log/mirror/packages/err.log.gz"
    assert pkg.statusinfo.errorcount == 1


def test_error_preserves_lastsuccesslog():
    """A failure must not erase the path of the previous successful run."""
    pkg = _make_package()
    pkg.set_status("ACTIVE", logfile=Path("/ok.log.gz"))
    pkg.set_status("ERROR", logfile=Path("/err.log.gz"))
    assert pkg.statusinfo.lasterrorlog == "/err.log.gz"
    assert pkg.statusinfo.lastsuccesslog == "/ok.log.gz", (
        "ACTIVE→ERROR transition wiped lastsuccesslog; user expects it preserved"
    )


def test_active_clears_error_state():
    """ERROR→ACTIVE transition clears all error-side state.

    lasterrorlog/lasterrortime/errorcount are reset; success-side fields are set.
    """
    pkg = _make_package()
    pkg.set_status("ERROR", logfile=Path("/err.log.gz"))
    pkg.set_status("ACTIVE", logfile=Path("/ok.log.gz"))
    assert pkg.statusinfo.lastsuccesslog == "/ok.log.gz"
    assert pkg.statusinfo.lasterrorlog is None
    assert pkg.statusinfo.lasterrortime == 0.0
    assert pkg.statusinfo.errorcount == 0


def test_active_sets_lastsuccesstime():
    pkg = _make_package()
    before = time.time()
    pkg.set_status("ACTIVE", logfile=Path("/ok.log.gz"))
    after = time.time()
    assert before <= pkg.statusinfo.lastsuccesstime <= after


def test_error_sets_lasterrortime():
    pkg = _make_package()
    before = time.time()
    pkg.set_status("ERROR", logfile=Path("/err.log.gz"))
    after = time.time()
    assert before <= pkg.statusinfo.lasterrortime <= after


def test_error_preserves_lastsuccesstime():
    """A failure must not erase the timestamp of the previous successful run.

    The web panel needs both 'last success was at X' AND 'last failure was at Y'
    when the package is currently ERROR.
    """
    pkg = _make_package()
    pkg.set_status("ACTIVE", logfile=Path("/ok.log.gz"))
    success_ts = pkg.statusinfo.lastsuccesstime
    pkg.set_status("ERROR", logfile=Path("/err.log.gz"))
    assert pkg.statusinfo.lastsuccesstime == success_ts
    assert pkg.statusinfo.lasterrortime > 0


def test_error_then_active_increments_then_resets_errorcount():
    pkg = _make_package()
    pkg.set_status("ERROR", logfile=Path("/e1.log.gz"))
    pkg.set_status("ACTIVE", logfile=Path("/ok1.log.gz"))
    pkg.set_status("ERROR", logfile=Path("/e2.log.gz"))
    pkg.set_status("ERROR", logfile=Path("/e3.log.gz"))
    # status equality short-circuits set_status, so e3 is a no-op vs e2 — but
    # set_status only short-circuits when the new status equals the OLD status.
    # ERROR -> ERROR returns early at the top of set_status; verify count matches.
    assert pkg.statusinfo.errorcount == 1
    pkg.set_status("ACTIVE", logfile=Path("/ok2.log.gz"))
    assert pkg.statusinfo.errorcount == 0


def test_on_sync_done_records_post_compression_path(tmp_path, monkeypatch):
    """on_sync_done must use the path close_logger returns (post-gzip),
    not the pre-compression path from get_log_path."""
    pkg = _make_package()
    monkeypatch.setattr(mirror, "packages", {"pkg": pkg}, raising=False)

    pre_path = tmp_path / "session.pkg.log"
    post_path = tmp_path / "session.pkg.log.gz"

    fake_logger = MagicMock(spec=__import__("logging").Logger)

    with patch("mirror.logger.get", return_value=fake_logger), \
         patch("mirror.logger.get_log_path", return_value=pre_path), \
         patch("mirror.logger.close_logger", return_value=post_path) as close_mock, \
         patch("mirror.event.post_event", lambda *a, **kw: None):
        mirror.sync.on_sync_done("pkg", success=True, returncode=0)

    close_mock.assert_called_once()
    assert pkg.statusinfo.lastsuccesslog == str(post_path), (
        f"Expected post-compression path; got {pkg.statusinfo.lastsuccesslog!r}"
    )
    assert pkg.statusinfo.lastsuccesslog != str(pre_path)


def test_to_dict_excludes_max_runtime_seconds():
    """Package.to_dict() must not include max_runtime_seconds in its output.

    The field is config-side only; leaking it into stat.json would be schema drift.
    """
    pkg = _make_package()
    pkg.max_runtime_seconds = 43200
    result = pkg.to_dict()
    assert "max_runtime_seconds" not in result, (
        "max_runtime_seconds leaked into stat.json serialization; drop it in to_dict()"
    )
