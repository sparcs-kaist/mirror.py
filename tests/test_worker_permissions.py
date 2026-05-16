import os
import sys
import subprocess
import pytest
from unittest.mock import patch
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


def test_preexec_applies_nice_before_uid_changes(monkeypatch):
    """preexec_fn order must be: nice -> setgid -> setuid."""
    from mirror.worker.process import Job

    calls = []

    def fake_setgid(gid):
        calls.append(("setgid", gid))

    def fake_setuid(uid):
        calls.append(("setuid", uid))

    def fake_nice(n):
        calls.append(("nice", n))
        return n

    monkeypatch.setattr("mirror.worker.process.os.setgid", fake_setgid)
    monkeypatch.setattr("mirror.worker.process.os.setuid", fake_setuid)
    monkeypatch.setattr("mirror.worker.process.os.nice", fake_nice)

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
    assert names.index("nice") < names.index("setgid"), f"order was {names}"
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

    job = Job("no_preexec_test", ["true"], {}, 1000, 1001, 0)
    job.start()

    assert "preexec_fn" not in captured
    assert captured["user"] == 1000
    assert captured["group"] == 1001


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

    job = Job("preexec_test", ["true"], {}, 1000, 1001, 5)
    job.start()

    assert callable(captured["preexec_fn"])
    assert "user" not in captured
    assert "group" not in captured


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
