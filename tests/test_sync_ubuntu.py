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

def test_execute_delegates_to_worker_execute_cli(monkeypatch):
    """execute() must hand the worker an argv that re-invokes the CLI."""
    import sys as _sys

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
    pkg_logger = logging.getLogger("test_execute_cli_delegation")
    pkg_logger.handlers = []

    execute(pkg, pkg_logger)

    assert len(worker_calls) == 1
    argv = worker_calls[0]["commandline"]
    # CLI invocation prefix
    assert argv[:5] == [_sys.executable, "-m", "mirror", "worker-execute", "ubuntu"]
    # --src and --dst flags exist with the expected values
    assert "--src" in argv
    assert argv[argv.index("--src") + 1] == pkg.settings.src
    assert "--dst" in argv
    assert argv[argv.index("--dst") + 1] == str(pkg.settings.dst)
    # No /bin/dash, no shell expressions
    assert "/bin/dash" not in argv
    assert "-c" not in argv  # no `sh -c` style invocation
    assert not any("$(hostname -f)" in str(t) for t in argv)


# ---------------------------------------------------------------------------
# 14. execute — global hostname becomes --trace-hostname
# ---------------------------------------------------------------------------

def test_execute_passes_global_hostname_as_flag(monkeypatch):
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
    pkg_logger = logging.getLogger("test_execute_hostname_flag")
    pkg_logger.handlers = []

    execute(pkg, pkg_logger)

    argv = worker_calls[0]["commandline"]
    assert "--trace-hostname" in argv
    assert argv[argv.index("--trace-hostname") + 1] == "foo.example"


# ---------------------------------------------------------------------------
# 15. execute — empty global hostname omits --trace-hostname
# ---------------------------------------------------------------------------

def test_execute_omits_trace_hostname_when_global_empty(monkeypatch):
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

    argv = worker_calls[0]["commandline"]
    # No flag → run_standalone default (socket.getfqdn) decides at runtime.
    assert "--trace-hostname" not in argv


# ---------------------------------------------------------------------------
# 15b. execute — trace disabled passes --no-trace
# ---------------------------------------------------------------------------

def test_execute_passes_no_trace_flag(monkeypatch):
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

    pkg = _make_package(options={"trace": False})
    pkg_logger = logging.getLogger("test_execute_no_trace")
    pkg_logger.handlers = []

    execute(pkg, pkg_logger)

    argv = worker_calls[0]["commandline"]
    assert "--no-trace" in argv


# ---------------------------------------------------------------------------
# 15c. execute — extra_rsync_args and stage1_excludes forwarded
# ---------------------------------------------------------------------------

def test_execute_passes_extra_rsync_args_and_stage1_excludes(monkeypatch):
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

    pkg = _make_package(options={
        "extra_rsync_args": ["--bwlimit=1000", "--stats"],
        "stage1_excludes": ["foo*", "bar*"],
    })
    pkg_logger = logging.getLogger("test_execute_extra_args")
    pkg_logger.handlers = []

    execute(pkg, pkg_logger)

    argv = worker_calls[0]["commandline"]
    # Each extra arg appears as a separate --extra-rsync-arg <value> pair.
    extra_indices = [i for i, t in enumerate(argv) if t == "--extra-rsync-arg"]
    assert len(extra_indices) == 2
    extra_values = [argv[i + 1] for i in extra_indices]
    assert extra_values == ["--bwlimit=1000", "--stats"]

    excl_indices = [i for i, t in enumerate(argv) if t == "--stage1-exclude"]
    assert len(excl_indices) == 2
    excl_values = [argv[i + 1] for i in excl_indices]
    assert excl_values == ["foo*", "bar*"]


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
# 17. trace_path traversal rejection (write_trace_file)
# ---------------------------------------------------------------------------

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
