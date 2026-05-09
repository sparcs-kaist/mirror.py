"""End-to-end test: operator workflow from `mirror setup` to first sync.

The other integration tests run against a pre-baked image that already has
`mirror setup` baked at build time and units enabled. This test instead
starts a CLEAN systemd container (the same image used by `test_e2e_setup`,
which has the binaries but no config and no enabled units), then runs at
runtime everything an operator does on a fresh server:

    1. `mirror setup` writes default config + dirs + units, runs daemon-reload.
    2. Operator edits /etc/mirror/config.json to add a real package.
    3. `systemctl enable --now mirror.service mirror-worker.service`.
    4. Daemon syncs from the existing rsync-fixture container.

Connectivity to rsync-fixture is provided by joining the compose-managed
bridge `mirror_integration_default`. The container is privileged, with its
own per-test bind mounts so the publish/state/log trees are inspectable
from the host without colliding with other tests' state.
"""

import json
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from .helpers import make_minimal_config


COMPOSE_NETWORK = "mirror_integration_default"


def _wait_until_systemd_running(name: str, timeout: float = 30) -> None:
    """Poll `systemctl is-system-running` inside a container until ready.

    Args:
        name(str): Container name.
        timeout(float): Maximum seconds to wait.

    Raises:
        TimeoutError: If systemd does not reach running/degraded within timeout.
    """
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", name, "systemctl", "is-system-running"],
            capture_output=True,
            text=True,
        )
        last = result.stdout.strip()
        if last in {"running", "degraded"}:
            return
        time.sleep(0.5)
    raise TimeoutError(f"systemd in {name} did not boot within {timeout}s (last: {last!r})")


def _exec(name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command inside the named running container.

    Args:
        name(str): Container name.
        *args(str): Command and arguments.
        check(bool): Raise CalledProcessError on non-zero exit.

    Return:
        result(subprocess.CompletedProcess): Captured stdout/stderr/returncode.
    """
    return subprocess.run(
        ["docker", "exec", name, *args],
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def operator_container(setup_test_image: str, docker_services, tmp_path_factory):
    """Start a fresh systemd container connected to the integration network.

    Yields a (name, host_tmp) tuple. host_tmp has publish/, state/, log/
    subdirs each bind-mounted to the corresponding daemon path inside the
    container.
    """
    name = f"mirror-operator-{uuid.uuid4().hex[:12]}"
    host_tmp = tmp_path_factory.mktemp(f"operator-{uuid.uuid4().hex[:8]}")
    for sub in ("publish", "state", "log"):
        (host_tmp / sub).mkdir()

    subprocess.run(
        [
            "docker", "run", "-d",
            "--privileged",
            "--tmpfs", "/run",
            "--tmpfs", "/run/lock",
            "--cgroupns=host",
            "-v", f"{host_tmp / 'publish'}:/srv/publish",
            "-v", f"{host_tmp / 'state'}:/var/lib/mirror",
            "-v", f"{host_tmp / 'log'}:/var/log/mirror",
            "--name", name,
            setup_test_image,
        ],
        check=True,
        capture_output=True,
    )
    try:
        _wait_until_systemd_running(name)
        subprocess.run(
            ["docker", "network", "connect", COMPOSE_NETWORK, name],
            check=True,
            capture_output=True,
        )
        yield name, host_tmp
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.mark.integration
def test_full_operator_workflow_setup_to_first_sync(operator_container) -> None:
    """Operator walks setup -> config edit -> systemctl enable -> sync verified."""
    name, host_tmp = operator_container

    setup_result = _exec(name, "mirror", "setup")
    assert setup_result.returncode == 0, (
        f"mirror setup failed: stdout={setup_result.stdout} stderr={setup_result.stderr}"
    )
    assert "Warning:" not in setup_result.stdout, (
        f"unexpected setup warnings on a clean stack:\n{setup_result.stdout}"
    )

    config = make_minimal_config({
        "rsync-test": {
            "name": "rsync-test",
            "id": "rsync-test",
            "href": "/rsync-test",
            "synctype": "rsync",
            "syncrate": "PT5S",
            "link": [],
            "settings": {
                "hidden": False,
                "src": "rsync://rsync-fixture/data",
                "dst": "/srv/publish/rsync-test",
                "options": {
                    "ffts": True,
                    "fftsfile": "fullfiletimelist-test",
                    "username": "",
                    "password": "",
                },
            },
        },
    })
    config_path = host_tmp / "config.json"
    config_path.write_text(json.dumps(config, indent=2))
    subprocess.run(
        ["docker", "cp", str(config_path), f"{name}:/etc/mirror/config.json"],
        check=True,
        capture_output=True,
    )

    enable_result = _exec(name, "systemctl", "enable", "--now",
                          "mirror.service", "mirror-worker.service")
    assert enable_result.returncode == 0, (
        f"systemctl enable --now failed: stderr={enable_result.stderr}"
    )

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        active = _exec(name, "systemctl", "is-active",
                       "mirror.service", "mirror-worker.service",
                       check=False).stdout.split()
        if active.count("active") == 2:
            break
        time.sleep(0.5)
    else:
        pytest.fail(f"units never both reached active: {active!r}")

    deadline = time.monotonic() + 60
    final_status = "UNKNOWN"
    while time.monotonic() < deadline:
        stat_path = host_tmp / "state" / "stat.json"
        if stat_path.exists():
            try:
                data = json.loads(stat_path.read_text())
                final_status = (
                    data.get("packages", {})
                    .get("rsync-test", {})
                    .get("status", {})
                    .get("status", "UNKNOWN")
                )
                if final_status == "ACTIVE":
                    break
            except (json.JSONDecodeError, KeyError):
                pass
        time.sleep(0.5)

    if final_status != "ACTIVE":
        journal = _exec(name, "journalctl", "-u", "mirror.service",
                        "--no-pager", "-n", "60", check=False).stdout
        pytest.fail(
            f"rsync-test did not reach ACTIVE within 60s "
            f"(final status: {final_status}). master journal tail:\n{journal}"
        )

    readme = host_tmp / "publish" / "rsync-test" / "README"
    assert readme.exists(), f"README not found at {readme}"
    content = readme.read_text().strip()
    assert content == "mirror.py integration test fixture (rsync) v1", content
