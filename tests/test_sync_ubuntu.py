"""Tests for mirror.sync.ubuntu — pure helpers and daemon/standalone entries."""

import logging
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import mirror
import mirror.structure
import mirror.socket.worker
import mirror.sync
from mirror.sync.ubuntu import (
    UBUNTU_RSYNC_BASE_ARGS,
    UBUNTU_STAGE1_EXCLUDES,
    UBUNTU_TRACE_PATH_DEFAULT,
    build_daemon_shell_command,
    build_ubuntu_commands,
    execute,
    write_trace_file,
    run_standalone,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_package(
    pkgid: str = "ubuntu",
    src: str = "rsync://mirror.example.com/ubuntu/",
    dst: str = "/srv/mirror/ubuntu",
    options: dict = None,
) -> mirror.structure.Package:
    """Build a minimal Package for ubuntu sync tests."""
    settings = mirror.structure.PackageSettings(
        hidden=False,
        src=src,
        dst=dst,
        options=options if options is not None else {},
    )
    pkg = mirror.structure.Package(
        pkgid=pkgid,
        name=pkgid,
        status="UNKNOWN",
        href=f"/{pkgid}",
        synctype="rsync",
        syncrate=3600,
        link=[],
        settings=settings,
        lastsync=0.0,
    )
    return pkg


def _make_fake_runner(returncode: int = 0):
    """Return a fake subprocess.run replacement that records its calls."""
    calls = []

    class FakeResult:
        def __init__(self, rc):
            self.returncode = rc

    def runner(argv, **kwargs):
        calls.append(argv)
        return FakeResult(returncode)

    runner.calls = calls
    return runner


# ---------------------------------------------------------------------------
# 1. build_ubuntu_commands — stage1 excludes
# ---------------------------------------------------------------------------

def test_build_ubuntu_commands_stage1_has_excludes():
    stage1, stage2 = build_ubuntu_commands("rsync://host/u", Path("/tmp/x"))

    expected_excludes = {
        "--exclude=Packages*",
        "--exclude=Sources*",
        "--exclude=Release*",
        "--exclude=InRelease",
    }
    for ex in expected_excludes:
        assert ex in stage1, f"Expected {ex!r} in stage1"

    assert "--delete" not in stage1


# ---------------------------------------------------------------------------
# 2. build_ubuntu_commands — stage2 delete flags
# ---------------------------------------------------------------------------

def test_build_ubuntu_commands_stage2_has_delete():
    stage1, stage2 = build_ubuntu_commands("rsync://host/u", Path("/tmp/x"))

    delete_idx = stage2.index("--delete")
    after_idx = stage2.index("--delete-after")
    assert after_idx == delete_idx + 1, "--delete-after must immediately follow --delete"

    for token in stage2:
        assert not token.startswith("--exclude="), f"Unexpected exclude in stage2: {token!r}"


# ---------------------------------------------------------------------------
# 3. build_ubuntu_commands — trailing slash on src and dst
# ---------------------------------------------------------------------------

def test_build_ubuntu_commands_trailing_slash():
    stage1, stage2 = build_ubuntu_commands("rsync://host/u", Path("/tmp/x"))

    for argv in (stage1, stage2):
        assert argv[-2].endswith("/"), f"src arg should end with /: {argv[-2]!r}"
        assert argv[-1].endswith("/"), f"dst arg should end with /: {argv[-1]!r}"


# ---------------------------------------------------------------------------
# 4. build_daemon_shell_command — shell-quoting of dangerous metacharacters
# ---------------------------------------------------------------------------

def test_build_daemon_shell_command_quotes_paths():
    # Hostile metacharacters in EVERY user-controllable token (src, dst,
    # trace_path, rsync_bin, extra_rsync_args, stage1_excludes, trace_hostname).
    result = build_daemon_shell_command(
        src="rsync://host space/u`whoami`",
        dst=Path("/tmp/space dir;rm -rf /"),
        trace_path="weird path",
        rsync_bin="/opt/rsync 1;evil",
        extra_rsync_args=("--bw=1 2", "$(rm -rf /)"),
        stage1_excludes=("a;b", "$(touch /tmp/pwn)"),
        trace_hostname="myhost;evil",
    )
    import re
    # After stripping every single-quoted segment, no raw shell metacharacter
    # from a user-controllable token should remain. Outside-of-quotes we only
    # expect the harness syntax: `set -e`, `&&`, `date -u > `, `"$(hostname -f)"`.
    stripped = re.sub(r"'[^']*'", "", result)
    # The hostile substrings must not appear unquoted.
    for needle in (
        "rsync://host space/u`whoami`",
        "/tmp/space dir;rm -rf /",
        "weird path",
        "/opt/rsync 1;evil",
        "--bw=1 2",
        "$(rm -rf /)",
        "a;b",
        "$(touch /tmp/pwn)",
        "myhost;evil",
    ):
        assert needle not in stripped, f"Unquoted hostile token {needle!r} found in: {result!r}"
    # Raw ';' (other than inside quotes) must not appear at all.
    assert ";" not in stripped, f"Unquoted ';' in: {result!r}"
    # Command substitution backticks/dollar-paren outside the explicit
    # $(hostname -f) site must not appear unquoted.
    stripped_minus_hostname = stripped.replace('"$(hostname -f)"', "")
    assert "`" not in stripped_minus_hostname, f"Unquoted backtick in: {result!r}"
    assert "$(" not in stripped_minus_hostname, f"Unquoted $( in: {result!r}"


# ---------------------------------------------------------------------------
# 5. build_daemon_shell_command — explicit hostname is quoted
# ---------------------------------------------------------------------------

def test_build_daemon_shell_command_with_explicit_hostname():
    import shlex
    result = build_daemon_shell_command(
        src="rsync://host/u",
        dst=Path("/tmp/u"),
        trace_hostname="mirror.example.com",
    )
    # shlex.quote returns the bare token when no metacharacters are present;
    # the test asserts the hostname token appears in the oneliner exactly as
    # shlex would emit it, and that the $(hostname -f) fallback is not used.
    assert shlex.quote("mirror.example.com") in result
    assert "mirror.example.com" in result
    assert "$(hostname -f)" not in result

    # A hostname with metacharacters MUST be wrapped in shlex's quoting.
    hostile = "weird;host"
    result_hostile = build_daemon_shell_command(
        src="rsync://host/u",
        dst=Path("/tmp/u"),
        trace_hostname=hostile,
    )
    assert shlex.quote(hostile) in result_hostile
    # Raw, unquoted hostile token must not appear.
    import re
    stripped = re.sub(r"'[^']*'", "", result_hostile)
    assert hostile not in stripped


# ---------------------------------------------------------------------------
# 6. build_daemon_shell_command — fallback to $(hostname -f)
# ---------------------------------------------------------------------------

def test_build_daemon_shell_command_falls_back_to_hostname_f():
    for hostname_arg in (None, ""):
        result = build_daemon_shell_command(
            src="rsync://host/u",
            dst=Path("/tmp/u"),
            trace_hostname=hostname_arg,
        )
        assert "$(hostname -f)" in result, (
            f"Expected $(hostname -f) in oneliner when trace_hostname={hostname_arg!r}"
        )


# ---------------------------------------------------------------------------
# 7. build_daemon_shell_command — trace disabled
# ---------------------------------------------------------------------------

def test_build_daemon_shell_command_omits_trace_when_disabled():
    result = build_daemon_shell_command(
        src="rsync://host/u",
        dst=Path("/tmp/u"),
        trace=False,
    )
    assert "date -u" not in result
    assert "$(hostname -f)" not in result


# ---------------------------------------------------------------------------
# 8. write_trace_file — content and path
# ---------------------------------------------------------------------------

def test_write_trace_file_content_and_path(tmp_path):
    fixed = datetime(2026, 5, 29, 12, 34, 56, tzinfo=timezone.utc)
    trace_file = write_trace_file(
        dst=tmp_path,
        trace_hostname="myhost.example",
        now=fixed,
    )
    expected_path = tmp_path / "project" / "trace" / "myhost.example"
    assert trace_file == expected_path
    assert expected_path.exists()

    expected_content = fixed.strftime("%a %b %e %H:%M:%S UTC %Y") + "\n"
    assert expected_path.read_text() == expected_content


# ---------------------------------------------------------------------------
# 9. run_standalone — both stages called in order
# ---------------------------------------------------------------------------

def test_run_standalone_both_stages_in_order(tmp_path):
    fake = _make_fake_runner(returncode=0)
    run_standalone(src="rsync://x/u", dst=tmp_path, trace=False, runner=fake)

    assert len(fake.calls) == 2
    stage1_argv, stage2_argv = fake.calls

    # Stage 1 should contain --exclude= flags
    assert any(t.startswith("--exclude=") for t in stage1_argv)
    # Stage 2 should contain --delete
    assert "--delete" in stage2_argv
    # Stage 1 should NOT contain --delete
    assert "--delete" not in stage1_argv


# ---------------------------------------------------------------------------
# 10. run_standalone — aborts on stage1 failure
# ---------------------------------------------------------------------------

def test_run_standalone_aborts_on_stage1_failure(tmp_path):
    call_count = 0

    class FakeResult:
        def __init__(self, rc):
            self.returncode = rc

    def runner(argv, **kwargs):
        nonlocal call_count
        call_count += 1
        # Always fail
        return FakeResult(2)

    with pytest.raises(SystemExit) as exc_info:
        run_standalone(src="rsync://x/u", dst=tmp_path, trace=False, runner=runner)

    assert exc_info.value.code == 2
    # Stage 2 must not have been invoked
    assert call_count == 1


# ---------------------------------------------------------------------------
# 11. run_standalone — no trace when trace=False
# ---------------------------------------------------------------------------

def test_run_standalone_skip_trace(tmp_path):
    fake = _make_fake_runner(returncode=0)
    run_standalone(src="rsync://x/u", dst=tmp_path, trace=False, runner=fake)

    assert not (tmp_path / "project").exists()


# ---------------------------------------------------------------------------
# 12. run_standalone — creates missing dst
# ---------------------------------------------------------------------------

def test_run_standalone_creates_missing_dst(tmp_path):
    new_dst = tmp_path / "newdir"
    assert not new_dst.exists()

    fake = _make_fake_runner(returncode=0)
    run_standalone(src="rsync://x/u", dst=new_dst, trace=False, runner=fake)

    assert new_dst.is_dir()


# ---------------------------------------------------------------------------
# 13. execute — worker called with /bin/dash oneliner
# ---------------------------------------------------------------------------

def test_execute_calls_worker_with_dash_oneliner(monkeypatch):
    worker_calls = []

    def fake_execute_command(**kwargs):
        worker_calls.append(kwargs)

    monkeypatch.setattr("mirror.socket.worker.execute_command", fake_execute_command)
    monkeypatch.setattr(
        mirror,
        "conf",
        SimpleNamespace(hostname="", uid=1000, gid=1000),
        raising=False,
    )
    monkeypatch.setattr("mirror.sync.on_sync_done", lambda *a, **kw: None)

    pkg = _make_package(options={})
    pkg_logger = logging.getLogger("test_execute_dash")
    pkg_logger.handlers = []

    execute(pkg, pkg_logger)

    assert len(worker_calls) == 1
    cmd = worker_calls[0]["commandline"]
    assert cmd[0] == "/bin/dash"
    assert cmd[1] == "-c"

    oneliner = cmd[2]
    # Both rsync stages must appear
    assert oneliner.count("rsync") >= 2
    # Hostname fallback must be present when global hostname is empty
    assert "$(hostname -f)" in oneliner


# ---------------------------------------------------------------------------
# 14. execute — global hostname is used in the oneliner
# ---------------------------------------------------------------------------

def test_execute_passes_global_hostname_to_oneliner(monkeypatch):
    worker_calls = []

    def fake_execute_command(**kwargs):
        worker_calls.append(kwargs)

    monkeypatch.setattr("mirror.socket.worker.execute_command", fake_execute_command)
    monkeypatch.setattr(
        mirror,
        "conf",
        SimpleNamespace(hostname="foo.example", uid=1000, gid=1000),
        raising=False,
    )
    monkeypatch.setattr("mirror.sync.on_sync_done", lambda *a, **kw: None)

    pkg = _make_package(options={})
    pkg_logger = logging.getLogger("test_execute_hostname")
    pkg_logger.handlers = []

    execute(pkg, pkg_logger)

    oneliner = worker_calls[0]["commandline"][2]
    import shlex
    assert shlex.quote("foo.example") in oneliner
    assert "foo.example" in oneliner
    assert "$(hostname -f)" not in oneliner


# ---------------------------------------------------------------------------
# 15. execute — empty global hostname falls back to $(hostname -f)
# ---------------------------------------------------------------------------

def test_execute_omits_hostname_when_global_empty(monkeypatch):
    worker_calls = []

    def fake_execute_command(**kwargs):
        worker_calls.append(kwargs)

    monkeypatch.setattr("mirror.socket.worker.execute_command", fake_execute_command)
    monkeypatch.setattr(
        mirror,
        "conf",
        SimpleNamespace(hostname="", uid=1000, gid=1000),
        raising=False,
    )
    monkeypatch.setattr("mirror.sync.on_sync_done", lambda *a, **kw: None)

    pkg = _make_package(options={})
    pkg_logger = logging.getLogger("test_execute_no_hostname")
    pkg_logger.handlers = []

    execute(pkg, pkg_logger)

    oneliner = worker_calls[0]["commandline"][2]
    assert "$(hostname -f)" in oneliner


# ---------------------------------------------------------------------------
# 16. execute — user/password passed as env to worker
# ---------------------------------------------------------------------------

def test_execute_passes_user_password_env(monkeypatch):
    worker_calls = []

    def fake_execute_command(**kwargs):
        worker_calls.append(kwargs)

    monkeypatch.setattr("mirror.socket.worker.execute_command", fake_execute_command)
    monkeypatch.setattr(
        mirror,
        "conf",
        SimpleNamespace(hostname="", uid=1000, gid=1000),
        raising=False,
    )
    monkeypatch.setattr("mirror.sync.on_sync_done", lambda *a, **kw: None)

    pkg = _make_package(options={"user": "alice", "password": "s3cret"})
    pkg_logger = logging.getLogger("test_execute_env")
    pkg_logger.handlers = []

    execute(pkg, pkg_logger)

    env = worker_calls[0]["env"]
    assert env["USER"] == "alice"
    assert env["RSYNC_PASSWORD"] == "s3cret"


# ---------------------------------------------------------------------------
# 17. trace_path traversal rejection (daemon + standalone)
# ---------------------------------------------------------------------------

def test_build_daemon_shell_command_rejects_absolute_trace_path():
    with pytest.raises(ValueError, match="must be relative"):
        build_daemon_shell_command(
            src="rsync://x/u",
            dst=Path("/tmp/u"),
            trace_path="/etc/evil",
        )


def test_build_daemon_shell_command_rejects_parent_traversal():
    with pytest.raises(ValueError, match=r"must not contain '\.\.'"):
        build_daemon_shell_command(
            src="rsync://x/u",
            dst=Path("/tmp/u"),
            trace_path="../../etc",
        )


def test_write_trace_file_rejects_absolute_trace_path(tmp_path):
    with pytest.raises(ValueError, match="must be relative"):
        write_trace_file(
            dst=tmp_path,
            trace_path="/etc/evil",
            trace_hostname="h",
        )


def test_write_trace_file_rejects_parent_traversal(tmp_path):
    with pytest.raises(ValueError, match=r"must not contain '\.\.'"):
        write_trace_file(
            dst=tmp_path,
            trace_path="../escape",
            trace_hostname="h",
        )
