"""End-to-end test: `mirror setup` runs cleanly inside a fresh container.

Builds a dedicated `mirror-setup-test` image (python:3.13-slim with rsync,
lftp, bandersnatch and the locally-built wheel pre-installed), then runs
three docker scenarios exercising the setup command:

    1. Clean container -> directories, units, sanitized config all present.
    2. Pre-existing /etc/mirror/config.json -> setup preserves it.
    3. Missing required binary -> setup aborts before writing anything.

The image build and wheel placement are session-scoped and gated on the
wheel artifact's mtime, so the heavy work happens once.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

IMAGE_TAG = "mirror-setup-test:latest"


@pytest.fixture(scope="session")
def setup_test_image(built_wheel: Path, project_root: Path) -> str:
    """Build the setup-test docker image once per session.

    Args:
        built_wheel(Path): Wheel produced by the `built_wheel` fixture.
        project_root(Path): Project root for resolving the docker context.

    Return:
        tag(str): Docker image tag of the built setup-test image.
    """
    context = project_root / "tests" / "integration" / "docker" / "setup-test"
    dist_dir = context / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    for old in dist_dir.glob("mirror_py-*.whl"):
        old.unlink()
    shutil.copy2(built_wheel, dist_dir / built_wheel.name)

    subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, str(context)],
        check=True,
        capture_output=True,
    )
    return IMAGE_TAG


def _docker_run(image: str, script: str) -> subprocess.CompletedProcess:
    """Run a bash script inside a fresh container of the given image.

    Args:
        image(str): Docker image tag.
        script(str): Bash script body to execute.

    Return:
        result(subprocess.CompletedProcess): Captured stdout/stderr/returncode.
    """
    return subprocess.run(
        ["docker", "run", "--rm", image, "bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.integration
def test_setup_provisions_clean_container(setup_test_image: str) -> None:
    """`mirror setup` on a fresh container creates dirs, units, and sanitized config."""
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
python3 - <<'PY'
import json
c = json.load(open("/etc/mirror/config.json"))
assert c["packages"] == {}, c["packages"]
assert "plugins" not in c["settings"], c["settings"]
assert c["settings"]["logfolder"] == "/var/log/mirror/ftpsync", c["settings"]["logfolder"]
print("CONFIG_OK")
PY
"""
    result = _docker_run(setup_test_image, script)
    assert result.returncode == 0, (
        f"setup failed in container.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "CONFIG_OK" in result.stdout

    edit_pos = result.stdout.find("Edit /etc/mirror/config.json")
    enable_pos = result.stdout.find("systemctl enable")
    assert edit_pos != -1 and enable_pos != -1, result.stdout
    assert edit_pos < enable_pos, "edit-config must precede enable/start"


@pytest.mark.integration
def test_setup_preserves_existing_config(setup_test_image: str) -> None:
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
    result = _docker_run(setup_test_image, script)
    assert result.returncode == 0, (
        f"setup failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "PRESERVED" in result.stdout
    assert "skipping config write" in result.stdout.lower()


@pytest.mark.integration
def test_setup_aborts_when_required_binary_missing(setup_test_image: str) -> None:
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
    result = _docker_run(setup_test_image, script)
    assert result.returncode == 0, (
        f"verification script failed.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "lftp" in combined, combined
    assert "Setup aborted" in combined, combined
