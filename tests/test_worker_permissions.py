import json
import os
import sys
import subprocess
import pytest
from pathlib import Path

# Helper to find a suitable target user/group (e.g., 'nobody')
def get_target_uid_gid():
    try:
        import pwd
        # Try to find 'nobody'
        user = pwd.getpwnam('nobody')
        return user.pw_uid, user.pw_gid
    except KeyError:
        # Fallback to 65534 if nobody not found
        return 65534, 65534

@pytest.mark.skipif(
    os.geteuid() != 0,
    reason="Requires root or CAP_SETUID to switch UID/GID; on non-root the "
           "preexec_fn raises at setgid before setuid is reached, which makes "
           "the script's downstream assertions (worker PID logged, file UID "
           "mismatch) unreachable.",
)
def test_worker_permissions_via_script():
    """
    Wraps the standalone verification script in a pytest test.
    This ensures we can capture the output reliably even across process boundaries
    (forked child process logs), which can be tricky with pytest fixtures directly.
    """
    script_path = Path(__file__).parent / "verify_worker_permissions.py"
    if not script_path.exists():
        pytest.fail(f"Verification script not found at {script_path}")

    current_uid = os.getuid()
    target_uid, target_gid = get_target_uid_gid()

    if current_uid == target_uid:
        pytest.skip("Current user is already the target user.")

    # Run the verification script
    cmd = [
        sys.executable, 
        str(script_path), 
        "--uid", str(target_uid), 
        "--gid", str(target_gid)
    ]

    # Run and capture output
    result = subprocess.run(cmd, capture_output=True, text=True)

    if current_uid == 0:
        # ROOT: Script should return 0 (SUCCESS)
        if result.returncode != 0:
            pytest.fail(f"Script failed (Root mode). Output:\n{result.stdout}\nError:\n{result.stderr}")
        assert "SUCCESS: Permissions verified." in result.stdout
        
    else:
        # USER: Script currently returns 1 (FAILURE) because ownership check fails.
        # But we want to verify that it produced the WARNING logs.
        
        # Check for the warning logs that prove we tried to switch and failed gracefully
        assert f"Failed to set UID to {target_uid}" in result.stderr or f"Failed to set UID to {target_uid}" in result.stdout, \
            f"Expected warning log missing. Output:\n{result.stdout}\nStderr:\n{result.stderr}"
            
        assert f"Failed to set GID to {target_gid}" in result.stderr or f"Failed to set GID to {target_gid}" in result.stdout
        
        # Verify that the worker actually ran (PID showed up)
        assert "Worker started with PID" in result.stdout

        # Verify that file ownership mismatch was reported (standard behavior for non-root)
        assert "FAIL: File UID" in result.stdout


def test_negative_nice_rejected_when_not_root():
    """Job(nice<0) must raise PermissionError early when EUID != 0."""
    import pytest as _pt
    from mirror.worker.process import Job

    if os.geteuid() == 0:
        _pt.skip("Test requires non-root EUID")

    with pytest.raises(PermissionError, match="negative nice"):
        Job("nice_test", ["true"], {}, None, None, -5)


def test_preexec_applies_nice_and_clears_groups_before_uid_changes(monkeypatch):
    """preexec_fn order must be: nice -> setgroups -> setgid -> setuid."""
    from mirror.worker.process import Job

    calls = []

    def fake_setgid(gid):
        calls.append(("setgid", gid))

    def fake_setuid(uid):
        calls.append(("setuid", uid))

    def fake_setgroups(groups):
        calls.append(("setgroups", groups))

    def fake_nice(n):
        calls.append(("nice", n))
        return n

    monkeypatch.setattr("mirror.worker.process.os.setgid", fake_setgid)
    monkeypatch.setattr("mirror.worker.process.os.setuid", fake_setuid)
    monkeypatch.setattr("mirror.worker.process.os.setgroups", fake_setgroups)
    monkeypatch.setattr("mirror.worker.process.os.nice", fake_nice)
    monkeypatch.setattr("mirror.worker.process.os.geteuid", lambda: 0)

    job = Job("order_test", ["true"], {}, 1000, 1000, 10)
    captured = {}

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            captured["preexec_fn"] = kwargs.get("preexec_fn")
            self.pid = 99
            self.returncode = None

        def poll(self):
            return None

    monkeypatch.setattr("mirror.worker.process.subprocess.Popen", _FakePopen)
    job.start()
    captured["preexec_fn"]()

    names = [c[0] for c in calls]
    assert names.index("nice") < names.index("setgroups"), f"order was {names}"
    assert names.index("setgroups") < names.index("setgid"), f"order was {names}"
    assert names.index("setgid") < names.index("setuid"), f"order was {names}"


def test_nice_zero_uses_popen_user_group_without_preexec(monkeypatch):
    """nice=0 should avoid preexec_fn and use Popen's credential kwargs."""
    from mirror.worker.process import Job

    captured = {}

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self.pid = 100
            self.returncode = None

        def poll(self):
            return None

    monkeypatch.setattr("mirror.worker.process.subprocess.Popen", _FakePopen)
    monkeypatch.setattr("mirror.worker.process.os.geteuid", lambda: 0)

    job = Job("no_preexec_test", ["true"], {}, 1000, 1001, 0)
    job.start()

    assert "preexec_fn" not in captured
    assert captured["user"] == 1000
    assert captured["group"] == 1001
    assert captured["extra_groups"] == []


def test_nonzero_nice_uses_preexec_without_popen_user_group(monkeypatch):
    """Non-zero nice still needs preexec_fn because Popen has no nice kwarg."""
    from mirror.worker.process import Job

    captured = {}

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self.pid = 101
            self.returncode = None

        def poll(self):
            return None

    monkeypatch.setattr("mirror.worker.process.subprocess.Popen", _FakePopen)
    monkeypatch.setattr("mirror.worker.process.os.geteuid", lambda: 0)

    job = Job("preexec_test", ["true"], {}, 1000, 1001, 5)
    job.start()

    assert callable(captured["preexec_fn"])
    assert "user" not in captured
    assert "group" not in captured


def test_nonroot_same_identity_preserves_supplementary_groups(monkeypatch):
    """Explicit current IDs and an unchanged foreground identity preserve groups."""
    from mirror.worker.process import _identity_popen_kwargs

    monkeypatch.setattr("mirror.worker.process.os.geteuid", lambda: 1000)
    monkeypatch.setattr("mirror.worker.process.os.getegid", lambda: 1001)

    same_identity = _identity_popen_kwargs(1000, 1001, 0)
    same_uid = _identity_popen_kwargs(1000, None, 0)
    same_gid = _identity_popen_kwargs(None, 1001, 0)
    inherited_identity = _identity_popen_kwargs(None, None, 0)

    assert "extra_groups" not in same_identity
    assert "extra_groups" not in same_uid
    assert "extra_groups" not in same_gid
    assert "extra_groups" not in inherited_identity


def test_nonroot_changed_identity_clears_groups(monkeypatch):
    """A requested identity change clears groups even when the parent is non-root."""
    from mirror.worker.process import _identity_popen_kwargs

    monkeypatch.setattr("mirror.worker.process.os.geteuid", lambda: 1000)
    monkeypatch.setattr("mirror.worker.process.os.getegid", lambda: 1001)

    assert _identity_popen_kwargs(1002, 1001, 0)["extra_groups"] == []
    assert _identity_popen_kwargs(1000, 1002, 0)["extra_groups"] == []


def test_root_explicit_partial_identity_clears_groups(monkeypatch):
    """Root clears groups when either UID or GID is explicit, even unchanged."""
    from mirror.worker.process import _identity_popen_kwargs

    monkeypatch.setattr("mirror.worker.process.os.geteuid", lambda: 0)
    monkeypatch.setattr("mirror.worker.process.os.getegid", lambda: 0)

    assert _identity_popen_kwargs(0, None, 0)["extra_groups"] == []
    assert _identity_popen_kwargs(None, 0, 0)["extra_groups"] == []


def test_preexec_group_clear_failure_stops_identity_change(monkeypatch):
    """A setgroups failure must prevent setgid and setuid from running."""
    from mirror.worker.process import _identity_popen_kwargs

    calls = []
    monkeypatch.setattr("mirror.worker.process.os.geteuid", lambda: 0)
    monkeypatch.setattr("mirror.worker.process.os.nice", lambda value: calls.append("nice"))
    monkeypatch.setattr(
        "mirror.worker.process.os.setgroups",
        lambda groups: (_ for _ in ()).throw(PermissionError("denied")),
    )
    monkeypatch.setattr("mirror.worker.process.os.setgid", lambda gid: calls.append("setgid"))
    monkeypatch.setattr("mirror.worker.process.os.setuid", lambda uid: calls.append("setuid"))

    preexec = _identity_popen_kwargs(1000, 1000, 1)["preexec_fn"]
    with pytest.raises(PermissionError, match="denied"):
        preexec()

    assert calls == ["nice"]


@pytest.mark.parametrize("nice", [0, 5])
@pytest.mark.skipif(os.geteuid() != 0, reason="Requires root to set child credentials")
def test_root_child_has_no_supplementary_groups(nice: int) -> None:
    """Both Popen paths drop a seeded supplementary group and child identity."""
    target_uid, target_gid = get_target_uid_gid()
    script = f"""
import json
import os
import subprocess
import sys
from mirror.worker.process import _identity_popen_kwargs

os.setgroups([12345])
result = subprocess.run(
    [sys.executable, "-c", "import json, os; print(json.dumps([os.getuid(), os.getgid(), os.getgroups()]))"],
    check=True,
    capture_output=True,
    text=True,
    **_identity_popen_kwargs({target_uid}, {target_gid}, {nice}),
)
print(json.dumps([json.loads(result.stdout), os.getgroups()]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == [
        [target_uid, target_gid, []],
        [12345],
    ]


@pytest.mark.parametrize(
    ("uid", "gid"),
    [
        (None, None),
        (None, 1001),
        (1000, None),
    ],
)
def test_job_start_requires_uid_gid(uid, gid):
    """Direct Job.start calls must not execute without explicit credentials."""
    from mirror.worker.process import Job

    job = Job("missing_creds_test", ["true"], {}, uid, gid, 0)

    with pytest.raises(ValueError, match="explicit uid and gid"):
        job.start()
