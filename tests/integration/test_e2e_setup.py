"""End-to-end test: `mirror setup` runs cleanly inside a real systemd container.

Builds a dedicated `mirror-setup-test` image (debian:bookworm-slim with
systemd-as-PID1, rsync, lftp, bandersnatch, and the locally-built wheel
pre-installed). Each test starts a privileged container, waits for systemd
to boot, then runs `mirror setup` and verifies real behavior — including
that `systemctl daemon-reload` is actually picked up by the running
systemd instance.

Three scenarios:
    1. Clean container -> directories, units, sanitized config all present;
       systemctl shows the units after daemon-reload.
    2. Pre-existing /etc/mirror/config.json -> setup preserves it.
    3. Missing required binary -> setup aborts before writing anything.
"""

import json
import subprocess
import time
import uuid

import pytest


def _start_systemd_container(image: str) -> str:
    """Start a privileged systemd container and wait until it has booted.

    Args:
        image(str): Docker image tag to run.

    Return:
        name(str): Container name (used for docker exec / rm).
    """
    name = f"mirror-setup-test-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker", "run", "-d",
            "--privileged",
            "--tmpfs", "/run",
            "--tmpfs", "/run/lock",
            "--cgroupns=host",
            "--name", name,
            image,
        ],
        check=True,
        capture_output=True,
    )

    deadline = time.time() + 30
    while time.time() < deadline:
        result = subprocess.run(
            ["docker", "exec", name, "systemctl", "is-system-running"],
            capture_output=True,
            text=True,
        )
        state = result.stdout.strip()
        if state in {"running", "degraded"}:
            return name
        time.sleep(0.5)

    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    raise TimeoutError(f"systemd in {name} did not reach running state in 30s")


def _exec(name: str, script: str) -> subprocess.CompletedProcess:
    """Run a bash script inside the named running container.

    Args:
        name(str): Container name.
        script(str): Bash script body.

    Return:
        result(subprocess.CompletedProcess): Captured stdout/stderr/returncode.
    """
    return subprocess.run(
        ["docker", "exec", name, "bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _stop_container(name: str) -> None:
    """Force-remove a container, ignoring errors."""
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture
def systemd_container(setup_test_image: str):
    """Yield a freshly-booted privileged systemd container; tear down afterwards."""
    name = _start_systemd_container(setup_test_image)
    try:
        yield name
    finally:
        _stop_container(name)


@pytest.mark.integration
def test_setup_provisions_clean_container(systemd_container: str) -> None:
    """`mirror setup` on a fresh systemd container creates dirs, units, and config.

    Verifies that systemd actually picks up the new unit files after
    `systemctl daemon-reload`, which is the production-relevant behavior.
    """
    script = r"""
set -euo pipefail
mirror setup
test -f /etc/mirror/config.json
test -d /var/log/mirror/packages
test -d /var/www/mirror
test -d /var/run/mirror
test -d /var/lib/mirror
test -f /etc/systemd/system/mirror.service
test -f /etc/systemd/system/mirror-worker.service
systemctl cat mirror.service > /dev/null
systemctl cat mirror-worker.service > /dev/null
python3 - <<'PY'
import json
c = json.load(open("/etc/mirror/config.json"))
assert c["packages"] == {}, c["packages"]
assert "plugins" not in c["settings"], c["settings"]
assert c["settings"]["logfolder"] == "/var/log/mirror/ftpsync", c["settings"]["logfolder"]
print("CONFIG_OK")
PY
"""
    result = _exec(systemd_container, script)
    assert result.returncode == 0, (
        f"setup failed in container.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "CONFIG_OK" in result.stdout
    assert "Warning:" not in result.stdout, (
        f"setup printed unexpected warnings on a real systemd host:\n{result.stdout}"
    )

    edit_pos = result.stdout.find("Edit /etc/mirror/config.json")
    enable_pos = result.stdout.find("systemctl enable")
    assert edit_pos != -1 and enable_pos != -1, result.stdout
    assert edit_pos < enable_pos, "edit-config must precede enable/start"


@pytest.mark.integration
def test_setup_preserves_existing_config(systemd_container: str) -> None:
    """Re-running setup with an existing config.json leaves it untouched."""
    script = r"""
set -euo pipefail
mkdir -p /etc/mirror
printf '%s' '{"sentinel": true}' > /etc/mirror/config.json
mirror setup
python3 - <<'PY'
import json
c = json.load(open("/etc/mirror/config.json"))
assert c == {"sentinel": True}, c
print("PRESERVED")
PY
"""
    result = _exec(systemd_container, script)
    assert result.returncode == 0, (
        f"setup failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "PRESERVED" in result.stdout
    assert "skipping config write" in result.stdout.lower()


@pytest.mark.integration
def test_setup_aborts_when_required_binary_missing(systemd_container: str) -> None:
    """Removing lftp before setup causes a hard fail with no config written."""
    script = r"""
set -euo pipefail
rm -f /usr/bin/lftp /usr/local/bin/lftp /usr/sbin/lftp || true
hash -r
if mirror setup; then
    :
fi
test ! -f /etc/mirror/config.json
"""
    result = _exec(systemd_container, script)
    assert result.returncode == 0, (
        f"verification script failed.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "lftp" in combined, combined
    assert "Setup aborted" in combined, combined
