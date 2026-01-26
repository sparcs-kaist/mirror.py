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