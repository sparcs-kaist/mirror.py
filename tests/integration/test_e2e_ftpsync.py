"""End-to-end ftpsync sync tests through the master and worker daemons."""

import time

import pytest

from .helpers import latest_package_log_text, run_fixture_python


def _wait_for_new_active_sync(mirror_stack, pkgid: str, lastsync: float) -> None:
    """Wait for a triggered sync to finish instead of accepting stale ACTIVE state."""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if (
            mirror_stack.package_status(pkgid) == "ACTIVE"
            and mirror_stack.package_lastsync(pkgid) > lastsync
        ):
            return
        time.sleep(0.25)
    raise TimeoutError(
        f"{pkgid} did not complete a new ACTIVE sync; "
        f"status={mirror_stack.package_status(pkgid)!r}, "
        f"lastsync={mirror_stack.package_lastsync(pkgid)!r}"
    )


@pytest.mark.integration
def test_basic_ftpsync_sync(mirror_stack):
    """Basic ftpsync sync reaches ACTIVE and writes a trace file to publish dir."""
    lastsync = mirror_stack.package_lastsync("ftpsync-test")
    mirror_stack.trigger_sync("ftpsync-test")
    _wait_for_new_active_sync(mirror_stack, "ftpsync-test", lastsync)

    assert mirror_stack.package_status("ftpsync-test") == "ACTIVE", (
        "ftpsync-test did not reach ACTIVE status after trigger"
    )

    trace_file = mirror_stack.publish_dir / "ftpsync-test" / "Project" / "trace" / "master"
    assert trace_file.exists(), (
        f"Expected archvsync trace file at {trace_file}; "
        f"publish dir contents: {list((mirror_stack.publish_dir / 'ftpsync-test').rglob('*'))}"
    )


@pytest.mark.integration
def test_ftpsync_offline_fallback(mirror_stack):
    """A failed git clone uses bundled archvsync while fixture rsync stays reachable."""
    wrapper_path = "/usr/local/bin/git"
    backup_path = "/tmp/mirror-ftpsync-git.original"
    marker_path = "/tmp/mirror-ftpsync-git.called"
    state_path = "/tmp/mirror-ftpsync-git.installed"
    wrapper = """#!/bin/sh
printf '%s\\n' "$*" >> /tmp/mirror-ftpsync-git.called
if [ "$1" = "clone" ]; then
    exit 42
fi
exec /usr/bin/git "$@"
"""
    install_wrapper = """
from pathlib import Path
import sys

wrapper = Path(sys.argv[1])
backup = Path(sys.argv[2])
marker = Path(sys.argv[3])
state = Path(sys.argv[4])
if backup.exists():
    wrapper.unlink(missing_ok=True)
    backup.rename(wrapper)
marker.unlink(missing_ok=True)
state.unlink(missing_ok=True)
if wrapper.exists():
    wrapper.rename(backup)
state.touch()
wrapper.write_text(sys.argv[5])
wrapper.chmod(0o755)
"""
    restore_wrapper = """
from pathlib import Path
import sys

wrapper = Path(sys.argv[1])
backup = Path(sys.argv[2])
marker = Path(sys.argv[3])
state = Path(sys.argv[4])
if state.exists() or backup.exists():
    wrapper.unlink(missing_ok=True)
    if backup.exists():
        backup.rename(wrapper)
marker.unlink(missing_ok=True)
state.unlink(missing_ok=True)
"""

    try:
        run_fixture_python(
            "mirror",
            install_wrapper,
            wrapper_path,
            backup_path,
            marker_path,
            state_path,
            wrapper,
        )
        lastsync = mirror_stack.package_lastsync("ftpsync-test")
        mirror_stack.trigger_sync("ftpsync-test")

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            marker = mirror_stack.docker_exec(
                "test", "-s", marker_path, check=False
            )
            if marker.returncode == 0:
                break
            time.sleep(0.1)
        else:
            pytest.fail("git clone wrapper was not invoked by ftpsync setup")

        _wait_for_new_active_sync(mirror_stack, "ftpsync-test", lastsync)

        assert mirror_stack.package_status("ftpsync-test") == "ACTIVE", (
            "ftpsync did not reach ACTIVE after bundled fallback provisioning"
        )
        marker = mirror_stack.docker_exec("cat", marker_path)
        assert "clone --depth 1" in marker.stdout
        assert "archvsync.git" in marker.stdout

        package_root = mirror_stack.publish_dir / "ftpsync-test"
        assert (package_root / "Project" / "trace" / "master").is_file()
        assert (package_root / "pool" / "main" / "p" / "pkg" / "pkg_1.0.deb").is_file()

        log_text = latest_package_log_text(mirror_stack, "ftpsync-test")
        assert "archvsync provisioned via bundled base64 script" in log_text

        tempdirs = run_fixture_python(
            "mirror",
            "from pathlib import Path; "
            "print(len(list(Path('/var/lib/mirror').glob('mirror_ftpsync_*'))))",
        )
        assert tempdirs.stdout.strip() == "0"
    finally:
        run_fixture_python(
            "mirror",
            restore_wrapper,
            wrapper_path,
            backup_path,
            marker_path,
            state_path,
        )
