"""Pytest fixtures for the mirror.py integration test suite.

All fixtures in this file are scoped to the tests/integration/ subdirectory only.
Integration tests talk to containerized services and do NOT import mirror.* directly.
"""

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from .helpers import MirrorStack, _wait_for_process_running


# ---------------------------------------------------------------------------
# Session-scoped infrastructure fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Absolute path to the mirror.py project root.

    Return:
        root(Path): Project root directory.
    """
    return Path(__file__).parent.parent.parent


@pytest.fixture(scope="session")
def built_wheel(project_root: Path) -> Path:
    """Build the current source tree as a wheel for the mirror Docker image.

    Uses whatever version is declared in pyproject.toml — no version rewriting.
    Idempotent: skips rebuild if a wheel matching the current source SHA already
    exists in the dist directory.

    Args:
        project_root(Path): Absolute path to the project root.

    Return:
        wheel_path(Path): Path to the wheel inside the mirror image build context.
    """
    dist_dir = project_root / "tests" / "integration" / "docker" / "mirror" / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    sha_marker = dist_dir / ".source.sha"

    hasher = hashlib.sha256()
    for path in sorted((project_root / "mirror").rglob("*.py")):
        hasher.update(path.read_bytes())
    hasher.update((project_root / "pyproject.toml").read_bytes())
    current_sha = hasher.hexdigest()

    existing = next(dist_dir.glob("mirror_py-*.whl"), None)
    if (
        existing is not None
        and sha_marker.exists()
        and sha_marker.read_text().strip() == current_sha
    ):
        return existing

    for old in dist_dir.glob("mirror_py-*.whl"):
        old.unlink()

    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        cwd=project_root,
        check=True,
    )

    sha_marker.write_text(current_sha)
    wheel = next(dist_dir.glob("mirror_py-*.whl"), None)
    if wheel is None:
        raise FileNotFoundError(f"uv build succeeded but no wheel found in {dist_dir}")
    return wheel


SETUP_TEST_IMAGE_TAG = "mirror-setup-test:latest"


@pytest.fixture(scope="session")
def setup_test_image(built_wheel: Path, project_root: Path) -> str:
    """Build the systemd-enabled setup-test docker image once per session.

    Used by integration tests that need a clean systemd container with the
    daemon prerequisites (rsync, lftp, bandersnatch) installed but no
    pre-baked config or enabled units — i.e. the starting state an operator
    sees before running `mirror setup`.

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
        ["docker", "build", "-t", SETUP_TEST_IMAGE_TAG, str(context)],
        check=True,
        capture_output=True,
    )
    return SETUP_TEST_IMAGE_TAG


@pytest.fixture(scope="session")
def integration_tmp(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Host-side temp directory bind-mounted into the mirror container.

    Creates publish/, state/, and log/ subdirectories and sets the
    INTEGRATION_TMP environment variable so docker-compose can expand it.

    Args:
        tmp_path_factory(pytest.TempPathFactory): Pytest factory for session temp dirs.

    Return:
        tmp(Path): Path to the created temp directory.
    """
    tmp = tmp_path_factory.mktemp("mirror_integration", numbered=False)
    (tmp / "publish").mkdir()
    (tmp / "state").mkdir()
    (tmp / "log").mkdir()
    os.environ["INTEGRATION_TMP"] = str(tmp)
    return tmp


@pytest.fixture(scope="session")
def docker_compose_file(project_root: Path, built_wheel: Path, integration_tmp: Path) -> str:
    """Absolute path to docker-compose.yml.

    Depends on built_wheel so the local wheel is in the build context before
    compose builds the mirror image, and on integration_tmp so INTEGRATION_TMP
    is set before pytest-docker invokes compose up.

    Args:
        project_root(Path): Absolute path to the project root.
        built_wheel(Path): Path to the locally-built wheel.
        integration_tmp(Path): Temp dir (ensures env var is set first).

    Return:
        path(str): Absolute path string to docker-compose.yml.
    """
    return str(project_root / "tests" / "integration" / "docker-compose.yml")


@pytest.fixture(scope="session")
def docker_compose_project_name() -> str:
    """Docker Compose project name used across all integration tests.

    Return:
        name(str): Project name.
    """
    return "mirror_integration"


@pytest.fixture(scope="session")
def docker_services(docker_services, integration_tmp: Path):
    """Session-scoped compose stack, extended to wait for mirror readiness.

    Overrides pytest-docker's docker_services to ensure the mirror container's
    master and worker processes are RUNNING before any test proceeds.

    Args:
        docker_services: pytest-docker's built-in docker_services fixture.
        integration_tmp(Path): Temp dir (dependency ensures env var is set).

    Yields:
        docker_services: The original pytest-docker services object.
    """
    _wait_for_process_running("worker", timeout=60)
    _wait_for_process_running("master", timeout=60)
    yield docker_services


# ---------------------------------------------------------------------------
# Per-test fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mirror_stack(docker_services, integration_tmp: Path) -> MirrorStack:
    """Per-test MirrorStack with reset state.

    Clears publish/, state/, and log/ contents on the host side before each
    test so that each test starts from a blank slate. The directories
    themselves are preserved to keep the docker bind-mounts intact.
    Restarts master so in-memory package state is reloaded from a clean disk.
    Failures leave state on disk for inspection.

    Args:
        docker_services: Session-scoped compose stack (ensures stack is up).
        integration_tmp(Path): Host-side bind-mounted temp directory.

    Yields:
        stack(MirrorStack): Ready MirrorStack instance.
    """
    stack = MirrorStack(integration_tmp)

    # Stop both daemons before cleanup. Stopping the worker is required because
    # worker spawns sync subprocesses (rsync, ftpsync, lftp) under its cgroup;
    # systemd's default KillMode=control-group propagates the stop to those
    # children, ensuring no rsync is mid-write when we wipe /srv/publish.
    stack.stop_process("master")
    stack.stop_process("worker")

    # Cleanup is done entirely from inside the container because mirror runs
    # as root and produces root-owned files; host-side rmtree as the test user
    # would fail with EPERM. This preserves the bind-mount inodes since we
    # only remove children, not the mount points themselves.
    stack.docker_exec(
        "sh", "-c",
        "rm -rf /srv/publish/* /srv/publish/.[!.]* "
        "/var/lib/mirror/* /var/lib/mirror/.[!.]* "
        "/var/log/mirror/packages/* "
        "2>/dev/null; true"
    )

    # Start order: worker first so its socket is bound before master tries to
    # connect on startup.
    stack.start_process("worker")
    stack.wait_for_worker_ready()
    stack.start_process("master")
    stack.wait_for_master_ready()
    # systemctl is-active flips to "active" the moment the process is forked,
    # which is BEFORE mirror.config.load() has rewritten stat.json. Wait for
    # the freshly written stat.json to appear so wait_for_status() polls a
    # current state instead of any stale-but-still-on-disk file.
    deadline = time.monotonic() + 10
    stat_path = stack.state_dir / "stat.json"
    while time.monotonic() < deadline and not stat_path.exists():
        time.sleep(0.1)

    # Master triggers auto-syncs immediately on a fresh stat.json (lastsync=0
    # makes every package due). Tests that probe the system independently must
    # not race those baseline syncs. A freshly loaded package starts as UNKNOWN
    # before master drives it through SYNC to a terminal state, so waiting only
    # for "no SYNC" can break in the window before master's first loop pass even
    # begins — yielding while a sync is about to start (holding e.g. the ftpsync
    # Archive-lock, or before lftp's lastsync advances). Wait until every package
    # has left UNKNOWN and settled to a terminal state (ACTIVE/ERROR).
    deadline = time.monotonic() + 60
    statuses: dict[str, str] = {}
    while time.monotonic() < deadline:
        data = stack.stat_json()
        statuses = {
            pkgid: pkg.get("status", {}).get("status", "UNKNOWN")
            for pkgid, pkg in data.get("packages", {}).items()
        }
        if statuses and all(s not in ("SYNC", "UNKNOWN") for s in statuses.values()):
            break
        time.sleep(0.5)
    else:
        unsettled = [pkgid for pkgid, s in statuses.items() if s in ("SYNC", "UNKNOWN")]
        if unsettled:
            raise TimeoutError(
                f"mirror_stack: package(s) {unsettled} did not reach a terminal "
                f"state within 60s; all statuses: {statuses}"
            )

    yield stack
