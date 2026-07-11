"""Tests for standalone mode: execute_command branch + on_sync_done short-circuit.

Covers:
- execute_command routes to run_foreground and records get_standalone_result
- standalone path does NOT call save_stat_data or generate_and_save_web_status
- on_sync_done short-circuit does not call Package.set_status
"""
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.config
import mirror.sync
import mirror.socket.worker as worker_module
from mirror.sync import set_standalone_mode, get_standalone_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_mirror_log(monkeypatch):
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)


@pytest.fixture(autouse=True)
def _reset_standalone_mode():
    """Ensure standalone mode is always reset to False after each test."""
    set_standalone_mode(False)
    mirror.sync._standalone_result.clear()
    yield
    set_standalone_mode(False)
    mirror.sync._standalone_result.clear()


@pytest.fixture()
def fake_package():
    """Minimal Package-like object for standalone tests."""
    pkg = MagicMock()
    pkg.pkgid = "standalone"
    pkg.synctype = "rsync"
    pkg.statusinfo = MagicMock()
    pkg.statusinfo.runninglog = None
    return pkg


@pytest.fixture()
def packages_with_pkg(monkeypatch, fake_package):
    """Populate mirror.packages so on_sync_done can find the package."""
    fake_pkgs = MagicMock()
    fake_pkgs.get = MagicMock(return_value=fake_package)
    monkeypatch.setattr(mirror, "packages", fake_pkgs, raising=False)
    return fake_pkgs


# ---------------------------------------------------------------------------
# Test: execute_command standalone branch — happy path
# ---------------------------------------------------------------------------

def test_execute_command_standalone_returns_finished_status(monkeypatch):
    """In standalone mode, execute_command must return status='finished' with the correct rc."""
    chosen_rc = 42
    set_standalone_mode(True)

    # Patch run_foreground to return chosen_rc without spawning a process.
    monkeypatch.setattr("mirror.worker.process.run_foreground", lambda *a, **kw: chosen_rc)
    # Patch on_sync_done so it does not touch daemon state.
    monkeypatch.setattr(mirror.sync, "on_sync_done", MagicMock())

    result = worker_module.execute_command(
        job_id="standalone",
        commandline=["true"],
        env={},
        sync_method="rsync",
        uid=None,
        gid=None,
    )

    assert result["status"] == "finished"
    assert result["returncode"] == chosen_rc
    assert result["job_id"] == "standalone"
    assert result["sync_method"] == "rsync"


def test_execute_command_standalone_calls_on_sync_done_with_correct_args(monkeypatch):
    """execute_command must call on_sync_done(job_id, success=(rc==0), returncode=rc)."""
    set_standalone_mode(True)
    chosen_rc = 0
    monkeypatch.setattr("mirror.worker.process.run_foreground", lambda *a, **kw: chosen_rc)

    on_sync_done_mock = MagicMock()
    monkeypatch.setattr(mirror.sync, "on_sync_done", on_sync_done_mock)

    worker_module.execute_command(
        job_id="myjob",
        commandline=["true"],
        env={},
        uid=None,
        gid=None,
    )

    on_sync_done_mock.assert_called_once_with("myjob", success=True, returncode=0)


def test_execute_command_standalone_failure_rc(monkeypatch):
    """A non-zero rc must set success=False in on_sync_done."""
    set_standalone_mode(True)
    chosen_rc = 7
    monkeypatch.setattr("mirror.worker.process.run_foreground", lambda *a, **kw: chosen_rc)

    on_sync_done_mock = MagicMock()
    monkeypatch.setattr(mirror.sync, "on_sync_done", on_sync_done_mock)

    worker_module.execute_command(
        job_id="failjob",
        commandline=["false"],
        env={},
        uid=None,
        gid=None,
    )

    on_sync_done_mock.assert_called_once_with("failjob", success=False, returncode=7)


# ---------------------------------------------------------------------------
# Test: get_standalone_result populated by on_sync_done short-circuit
# ---------------------------------------------------------------------------

def test_standalone_result_recorded_on_success(monkeypatch, packages_with_pkg, fake_package):
    """on_sync_done in standalone mode must record (True, 0) for a successful sync."""
    set_standalone_mode(True)
    monkeypatch.setattr("mirror.plugin.get_record", lambda synctype: None)

    mirror.sync.on_sync_done(fake_package.pkgid, success=True, returncode=0)

    result = get_standalone_result(fake_package.pkgid)
    assert result == (True, 0)


def test_standalone_result_recorded_on_failure(monkeypatch, packages_with_pkg, fake_package):
    """on_sync_done in standalone mode must record (False, rc) for a failed sync."""
    set_standalone_mode(True)
    monkeypatch.setattr("mirror.plugin.get_record", lambda synctype: None)

    mirror.sync.on_sync_done(fake_package.pkgid, success=False, returncode=3)

    result = get_standalone_result(fake_package.pkgid)
    assert result == (False, 3)


def test_get_standalone_result_returns_none_for_unknown_pkgid():
    """get_standalone_result must return None if no result is recorded for pkgid."""
    assert get_standalone_result("no-such-pkg") is None


def test_set_standalone_mode_true_clears_stale_results(monkeypatch, packages_with_pkg, fake_package):
    """Re-enabling standalone mode must clear prior results.

    Guards the reused-pkgid case: a first run records success, then a second
    run for the same id fails BEFORE on_sync_done. Without clearing, the stale
    success would be read and the CLI would wrongly exit 0.
    """
    set_standalone_mode(True)
    monkeypatch.setattr("mirror.plugin.get_record", lambda synctype: None)

    # First run records success for the id.
    mirror.sync.on_sync_done(fake_package.pkgid, success=True, returncode=0)
    assert get_standalone_result(fake_package.pkgid) == (True, 0)

    # Second run: re-enabling standalone mode clears the stale result. The run
    # then fails before on_sync_done (nothing re-records), so the result is None.
    set_standalone_mode(True)
    assert get_standalone_result(fake_package.pkgid) is None


# ---------------------------------------------------------------------------
# Test: standalone path does NOT call save_stat_data or generate_and_save_web_status
# ---------------------------------------------------------------------------

def test_standalone_does_not_call_save_stat_data(monkeypatch, packages_with_pkg, fake_package):
    """on_sync_done short-circuit must not call mirror.config.save_stat_data."""
    set_standalone_mode(True)
    monkeypatch.setattr("mirror.plugin.get_record", lambda synctype: None)

    def _forbid_save_stat():
        raise AssertionError("save_stat_data must not be called in standalone mode")

    monkeypatch.setattr(mirror.config, "save_stat_data", _forbid_save_stat)

    # Must not raise.
    mirror.sync.on_sync_done(fake_package.pkgid, success=True, returncode=0)


def test_standalone_does_not_call_generate_and_save_web_status(monkeypatch, packages_with_pkg, fake_package):
    """on_sync_done short-circuit must not call generate_and_save_web_status."""
    set_standalone_mode(True)
    monkeypatch.setattr("mirror.plugin.get_record", lambda synctype: None)

    def _forbid_web_status(*a, **kw):
        raise AssertionError("generate_and_save_web_status must not be called in standalone mode")

    monkeypatch.setattr(mirror.config, "generate_and_save_web_status", _forbid_web_status, raising=False)

    # Must not raise.
    mirror.sync.on_sync_done(fake_package.pkgid, success=True, returncode=0)


# ---------------------------------------------------------------------------
# Test: on_sync_done short-circuit does not call Package.set_status
# ---------------------------------------------------------------------------

def test_standalone_on_sync_done_does_not_call_set_status(monkeypatch, packages_with_pkg, fake_package):
    """on_sync_done in standalone mode must NOT call package.set_status."""
    set_standalone_mode(True)
    monkeypatch.setattr("mirror.plugin.get_record", lambda synctype: None)

    mirror.sync.on_sync_done(fake_package.pkgid, success=True, returncode=0)

    fake_package.set_status.assert_not_called()


def test_standalone_on_sync_done_does_not_call_set_status_on_failure(monkeypatch, packages_with_pkg, fake_package):
    """on_sync_done short-circuit must not call set_status even when sync fails."""
    set_standalone_mode(True)
    monkeypatch.setattr("mirror.plugin.get_record", lambda synctype: None)

    mirror.sync.on_sync_done(fake_package.pkgid, success=False, returncode=1)

    fake_package.set_status.assert_not_called()


# ---------------------------------------------------------------------------
# Test: execute_command standalone branch calls run_foreground with correct args
# ---------------------------------------------------------------------------

def test_execute_command_standalone_passes_log_path(monkeypatch, tmp_path):
    """execute_command in standalone mode must pass log_path as a Path to run_foreground."""
    set_standalone_mode(True)
    log_file = str(tmp_path / "test.log")
    captured = {}

    def _fake_run_foreground(job_id, commandline, env, uid, gid, nice, log_path=None, log_helper_command=None):
        captured["log_path"] = log_path
        return 0

    monkeypatch.setattr("mirror.worker.process.run_foreground", _fake_run_foreground)
    monkeypatch.setattr(mirror.sync, "on_sync_done", MagicMock())

    worker_module.execute_command(
        job_id="logtest",
        commandline=["true"],
        env={},
        uid=None,
        gid=None,
        log_path=log_file,
    )

    from pathlib import Path
    assert captured["log_path"] == Path(log_file)


def test_execute_command_standalone_no_log_path_passes_none(monkeypatch):
    """execute_command in standalone mode must pass None for log_path when omitted."""
    set_standalone_mode(True)
    captured = {}

    def _fake_run_foreground(job_id, commandline, env, uid, gid, nice, log_path=None, log_helper_command=None):
        captured["log_path"] = log_path
        return 0

    monkeypatch.setattr("mirror.worker.process.run_foreground", _fake_run_foreground)
    monkeypatch.setattr(mirror.sync, "on_sync_done", MagicMock())

    worker_module.execute_command(
        job_id="nolog",
        commandline=["true"],
        env={},
        uid=None,
        gid=None,
    )

    assert captured["log_path"] is None


# ---------------------------------------------------------------------------
# Test: non-standalone path is not affected
# ---------------------------------------------------------------------------

def test_non_standalone_execute_command_requires_uid_gid():
    """With standalone mode off, execute_command must still require uid and gid."""
    set_standalone_mode(False)
    with pytest.raises(ValueError, match="uid and gid"):
        worker_module.execute_command(
            job_id="test",
            commandline=["true"],
            env={},
            uid=None,
            gid=None,
        )
