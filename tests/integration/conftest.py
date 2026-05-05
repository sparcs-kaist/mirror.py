"""Pytest fixtures for the mirror.py integration test suite.

All fixtures in this file are scoped to the tests/integration/ subdirectory only.
Integration tests talk to containerized services and do NOT import mirror.* directly.
"""

import hashlib
import os
import shutil
import subprocess
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

    _reset_host_dirs(integration_tmp)

    stack.docker_exec(
        "sh", "-c",
        "rm -rf /srv/publish/* /var/lib/mirror/stat.json /var/log/mirror/packages/*"
    )

    stack.restart_process("master")
    stack.wait_for_master_ready()

    yield stack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_host_dirs(integration_tmp: Path) -> None:
    """Clear the contents of publish, state, and log subdirectories.

    The directories themselves are kept so docker bind-mounts remain attached
    to the same inode that the container observed at startup.

    Args:
        integration_tmp(Path): Root of the host-side bind-mount tree.
    """
    for subdir in ("publish", "state", "log"):
        target = integration_tmp / subdir
        _clear_dir_contents(target)


def _clear_dir_contents(d: Path) -> None:
    """Remove all children of a directory without removing the directory itself.

    Args:
        d(Path): Directory whose contents should be cleared.
    """
    if not d.exists():
        return
    for child in d.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
