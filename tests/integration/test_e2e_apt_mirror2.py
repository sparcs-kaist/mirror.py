"""End-to-end apt-mirror2 tests against signed flat and Debian repositories."""

import gzip
import json
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest


PACKAGE_ID = "apt-mirror2-test"
FIXTURE_CONTAINER = "apt-mirror2-fixture"
FIXTURE_PATH = Path(__file__).parent / "docker" / "apt-mirror2-fixture"


def _run_fixture_python(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a bounded Python mutation inside the apt-mirror2 fixture."""
    return subprocess.run(
        ["docker", "exec", FIXTURE_CONTAINER, "python", "-c", script, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _replace_fixture(version: str) -> None:
    """Replace the served repositories with a pristine fixture version."""
    script = (
        "import pathlib, shutil, sys; "
        "source = pathlib.Path('/srv/fixtures') / sys.argv[1]; "
        "target = pathlib.Path('/srv/data'); "
        "shutil.rmtree(target, ignore_errors=True); "
        "shutil.copytree(source, target)"
    )
    _run_fixture_python(script, version)


def _corrupt_payload(relative: str) -> None:
    """Replace a payload with same-sized bytes so native hash checking catches it."""
    script = (
        "import pathlib, sys; "
        "path = pathlib.Path('/srv/data') / sys.argv[1]; "
        "data = path.read_bytes(); "
        "path.write_bytes(b'X' * len(data))"
    )
    _run_fixture_python(script, relative)


def _wait_for_completion(
    mirror_stack: Any,
    previous_lastsync: float,
    expected_status: str,
    timeout: float = 150,
) -> float:
    """Wait for a new apt-mirror2 completion with the expected status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lastsync = mirror_stack.package_lastsync(PACKAGE_ID)
        status = mirror_stack.package_status(PACKAGE_ID)
        if lastsync > previous_lastsync and status == expected_status:
            return lastsync
        time.sleep(0.5)
    raise TimeoutError(
        f"{PACKAGE_ID} did not complete with status {expected_status!r} "
        f"within {timeout}s; lastsync={mirror_stack.package_lastsync(PACKAGE_ID)}, "
        f"status={mirror_stack.package_status(PACKAGE_ID)!r}"
    )


def _latest_log_text(mirror_stack: Any) -> str:
    """Read the newest completed package log."""
    logs = mirror_stack.read_package_log_dir(PACKAGE_ID)
    assert logs, f"No package logs found for {PACKAGE_ID}"
    latest = logs[-1]
    if latest.suffix == ".gz":
        with gzip.open(latest, "rt", errors="replace") as stream:
            return stream.read()
    return latest.read_text(errors="replace")


def _write_container_config(config_text: str) -> None:
    """Replace the integration config inside the mirror container."""
    result = subprocess.run(
        ["docker", "exec", "-i", "mirror", "tee", "/etc/mirror/config.json"],
        input=config_text,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Failed to write config.json: {result.stderr!r}"


@contextmanager
def _package_options(mirror_stack: Any, options: dict[str, Any]) -> Iterator[None]:
    """Apply temporary apt-mirror2 options through the production reload path."""
    result = mirror_stack.docker_exec("cat", "/etc/mirror/config.json")
    original = result.stdout
    config = json.loads(original)
    config["packages"][PACKAGE_ID]["settings"]["options"] = options
    _write_container_config(json.dumps(config, indent=2))
    reload_result = mirror_stack.docker_exec("mirror", "config", "reload", check=False)
    assert reload_result.returncode == 0, (
        f"Failed to reload temporary apt-mirror2 options: {reload_result.stderr!r}"
    )
    try:
        yield
    finally:
        _write_container_config(original)
        reload_result = mirror_stack.docker_exec("mirror", "config", "reload", check=False)
        assert reload_result.returncode == 0, (
            f"Failed to restore apt-mirror2 options: {reload_result.stderr!r}"
        )


def _fixture_bytes(version: str, relative: str) -> bytes:
    """Read expected bytes from a fixture version."""
    return (FIXTURE_PATH / version / relative).read_bytes()


@pytest.fixture(autouse=True)
def _reset_apt_mirror2_fixture(docker_services: Any) -> Iterator[None]:
    """Serve pristine v1 for every apt-mirror2 test."""
    del docker_services
    _replace_fixture("v1")
    try:
        yield
    finally:
        _replace_fixture("v1")


@pytest.mark.integration
def test_multiple_signed_flat_repositories_update_and_clean(mirror_stack: Any) -> None:
    """One package mirrors two key-isolated flat repositories and cleans updates."""
    destination = mirror_stack.publish_dir / PACKAGE_ID
    first_root = destination / "ubuntu2404/x86_64"
    second_root = destination / "ubuntu2604/x86_64"
    first_v1 = first_root / "pool/ubuntu2404-1.0_amd64.deb"
    second_v1 = second_root / "pool/ubuntu2604-1.0_amd64.deb"
    first_shared = first_root / "pool/ubuntu2404-shared_1.0_all.deb"
    second_shared = second_root / "pool/ubuntu2604-shared_1.0_all.deb"

    assert mirror_stack.package_status(PACKAGE_ID) == "ACTIVE"
    assert first_v1.read_bytes() == _fixture_bytes(
        "v1", "ubuntu2404/pool/ubuntu2404-1.0_amd64.deb"
    )
    assert second_v1.read_bytes() == _fixture_bytes(
        "v1", "ubuntu2604/pool/ubuntu2604-1.0_amd64.deb"
    )
    assert first_shared.read_bytes() == _fixture_bytes(
        "v1", "ubuntu2404/pool/ubuntu2404-shared_1.0_all.deb"
    )
    assert second_shared.read_bytes() == _fixture_bytes(
        "v1", "ubuntu2604/pool/ubuntu2604-shared_1.0_all.deb"
    )
    assert not (first_root / "pool/ubuntu2404-source_1.0.tar.xz").exists()
    assert not (second_root / "pool/ubuntu2604-source_1.0.tar.xz").exists()

    previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)
    _replace_fixture("v2")
    mirror_stack.trigger_sync(PACKAGE_ID)
    _wait_for_completion(mirror_stack, previous_lastsync, "ACTIVE")

    first_v2 = first_root / "pool/ubuntu2404-2.0_amd64.deb"
    second_v2 = second_root / "pool/ubuntu2604-2.0_amd64.deb"
    assert first_v2.read_bytes() == _fixture_bytes(
        "v2", "ubuntu2404/pool/ubuntu2404-2.0_amd64.deb"
    )
    assert second_v2.read_bytes() == _fixture_bytes(
        "v2", "ubuntu2604/pool/ubuntu2604-2.0_amd64.deb"
    )
    assert first_shared.exists()
    assert second_shared.exists()
    assert not first_v1.exists()
    assert not second_v1.exists()
    mirror_stack.docker_exec(
        "python",
        "-c",
        "from pathlib import Path; "
        "assert not list(Path('/srv/publish/apt-mirror2-test').glob('.mirror-apt-mirror2-*'))",
    )


@pytest.mark.integration
def test_source_enabled_and_source_only_flat_repositories(mirror_stack: Any) -> None:
    """source=true downloads source payloads and accepts a source-only flat repo."""
    options = {
        "source": True,
        "nthreads": 2,
        "limit_rate": "20m",
        "config": [
            {
                "src": "http://apt-mirror2-fixture:8001/ubuntu2404/",
                "dst": "source-enabled",
                "keyring": ["/etc/mirror/apt-mirror2-ubuntu2404.gpg"],
            },
            {
                "src": "http://apt-mirror2-fixture:8001/sourceonly/",
                "dst": "source-only",
                "keyring": ["/etc/mirror/apt-mirror2-ubuntu2404.gpg"],
            },
        ],
    }
    with _package_options(mirror_stack, options):
        previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)
        mirror_stack.trigger_sync(PACKAGE_ID)
        _wait_for_completion(mirror_stack, previous_lastsync, "ACTIVE")

        destination = mirror_stack.publish_dir / PACKAGE_ID
        binary = destination / "source-enabled/pool/ubuntu2404-1.0_amd64.deb"
        source = destination / "source-enabled/pool/ubuntu2404-source_1.0.tar.xz"
        source_only = destination / "source-only/pool/source-only_1.0.tar.xz"
        assert binary.read_bytes() == _fixture_bytes(
            "v1", "ubuntu2404/pool/ubuntu2404-1.0_amd64.deb"
        )
        assert source.read_bytes() == _fixture_bytes(
            "v1", "ubuntu2404/pool/ubuntu2404-source_1.0.tar.xz"
        )
        assert source_only.read_bytes() == _fixture_bytes(
            "v1", "sourceonly/pool/source-only_1.0.tar.xz"
        )
        assert not (destination / "source-only/Packages").exists()


@pytest.mark.integration
def test_repository_key_isolation_failure_preserves_existing_mirror(
    mirror_stack: Any,
) -> None:
    """A repository cannot validate metadata with another entry's key."""
    destination = mirror_stack.publish_dir / PACKAGE_ID
    preserved = destination / "ubuntu2604/x86_64/Packages"
    preserved_bytes = preserved.read_bytes()
    previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)
    previous_errorcount = mirror_stack.package_errorcount(PACKAGE_ID)
    options = {
        "source": False,
        "config": [
            {
                "src": "http://apt-mirror2-fixture:8001/ubuntu2404/",
                "dst": "ubuntu2404/x86_64",
                "keyring": ["/etc/mirror/apt-mirror2-ubuntu2404.gpg"],
            },
            {
                "src": "http://apt-mirror2-fixture:8001/ubuntu2604/",
                "dst": "ubuntu2604/x86_64",
                "keyring": ["/etc/mirror/apt-mirror2-ubuntu2404.gpg"],
            },
        ],
    }

    with _package_options(mirror_stack, options):
        mirror_stack.trigger_sync(PACKAGE_ID)
        failed_lastsync = _wait_for_completion(
            mirror_stack, previous_lastsync, "ERROR"
        )
        assert mirror_stack.package_errorcount(PACKAGE_ID) > previous_errorcount
        assert preserved.read_bytes() == preserved_bytes
        assert "Release signature verification failed" in _latest_log_text(mirror_stack)

    mirror_stack.trigger_sync(PACKAGE_ID)
    _wait_for_completion(mirror_stack, failed_lastsync, "ACTIVE")
    assert mirror_stack.package_errorcount(PACKAGE_ID) == 0


@pytest.mark.integration
def test_payload_hash_failure_skips_metadata_publish_and_cleanup(
    mirror_stack: Any,
) -> None:
    """A bad payload hash fails without publishing metadata or cleaning old files."""
    destination = mirror_stack.publish_dir / PACKAGE_ID / "ubuntu2604/x86_64"
    previous_packages = (destination / "Packages").read_bytes()
    previous_payload = destination / "pool/ubuntu2604-1.0_amd64.deb"
    previous_payload_bytes = previous_payload.read_bytes()
    previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)

    _replace_fixture("v2")
    _corrupt_payload("ubuntu2604/pool/ubuntu2604-2.0_amd64.deb")
    mirror_stack.trigger_sync(PACKAGE_ID)
    failed_lastsync = _wait_for_completion(
        mirror_stack, previous_lastsync, "ERROR", timeout=180
    )

    assert (destination / "Packages").read_bytes() == previous_packages
    assert previous_payload.read_bytes() == previous_payload_bytes
    log = _latest_log_text(mirror_stack)
    assert "HashMismatchException" in log
    assert "Repository cleanup skipped because of download errors" in log
    assert "Metadata movement skipped because of download errors" in log

    _replace_fixture("v2")
    mirror_stack.trigger_sync(PACKAGE_ID)
    _wait_for_completion(mirror_stack, failed_lastsync, "ACTIVE")
    assert (destination / "pool/ubuntu2604-2.0_amd64.deb").read_bytes() == _fixture_bytes(
        "v2", "ubuntu2604/pool/ubuntu2604-2.0_amd64.deb"
    )
    assert not previous_payload.exists()


@pytest.mark.integration
def test_ftp_conventional_repository_auto_discovery_and_release_fallback(
    mirror_stack: Any,
) -> None:
    """FTP discovery resolves dists, components, and arches using Release.gpg."""
    options = {
        "source": False,
        "config": [
            {
                "src": "ftp://apt-mirror2-fixture:2121/debian/",
                "dst": "ftp-conventional",
                "keyring": ["/etc/mirror/apt-mirror2-ubuntu2404.gpg"],
            }
        ],
    }
    with _package_options(mirror_stack, options):
        previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)
        mirror_stack.trigger_sync(PACKAGE_ID)
        _wait_for_completion(mirror_stack, previous_lastsync, "ACTIVE")

        destination = mirror_stack.publish_dir / PACKAGE_ID / "ftp-conventional"
        payload = destination / "pool/main/f/ftp-test/ftp-test_1.0_amd64.deb"
        assert payload.read_bytes() == _fixture_bytes(
            "v1", "debian/pool/main/f/ftp-test/ftp-test_1.0_amd64.deb"
        )
        assert (destination / "dists/bookworm/Release").exists()
        assert not (destination / "dists/bookworm/InRelease").exists()
