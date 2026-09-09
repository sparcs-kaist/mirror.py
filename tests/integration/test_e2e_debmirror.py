"""End-to-end debmirror sync tests against a signed Debian repository."""

import gzip
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


PACKAGE_ID = "debmirror-test"
FIXTURE_CONTAINER = "debmirror-fixture"
FIXTURE_PATH = Path(__file__).parent / "docker" / "debmirror-fixture"


def _run_fixture_python(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a bounded Python mutation inside the debmirror fixture container."""
    return subprocess.run(
        ["docker", "exec", FIXTURE_CONTAINER, "python", "-c", script, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _replace_fixture(version: str) -> None:
    """Replace the served repository with a pristine fixture version."""
    script = (
        "import pathlib, shutil, sys; "
        "source = pathlib.Path('/srv/fixtures') / sys.argv[1]; "
        "target = pathlib.Path('/srv/data/debian'); "
        "shutil.rmtree(target, ignore_errors=True); "
        "shutil.copytree(source, target)"
    )
    _run_fixture_python(script, version)


def _tamper_release() -> None:
    """Change the served Release while leaving its detached signature intact."""
    script = (
        "import pathlib; "
        "path = pathlib.Path('/srv/data/debian/dists/bookworm/Release'); "
        "path.write_text(path.read_text() + '\\nTampered: yes\\n')"
    )
    _run_fixture_python(script)


def _wait_for_completion(
    mirror_stack: Any,
    previous_lastsync: float,
    expected_status: str,
    timeout: float = 90,
) -> float:
    """Wait for a new debmirror completion with the expected terminal status."""
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
    """Read the newest completed package log as text."""
    logs = mirror_stack.read_package_log_dir(PACKAGE_ID)
    assert logs, f"No package logs found for {PACKAGE_ID}"
    latest = logs[-1]
    if latest.suffix == ".gz":
        with gzip.open(latest, "rt", errors="replace") as stream:
            return stream.read()
    return latest.read_text(errors="replace")


@pytest.fixture(autouse=True)
def _reset_debmirror_fixture(docker_services: Any) -> Iterator[None]:
    """Serve signed v1 for every test and restore it after mutations."""
    del docker_services
    _replace_fixture("v1")
    try:
        yield
    finally:
        _replace_fixture("v1")


@pytest.mark.integration
def test_signed_debmirror_sync(mirror_stack: Any) -> None:
    """A signed repository syncs payload, index, status, timestamp, and log."""
    previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)

    mirror_stack.trigger_sync(PACKAGE_ID)
    completed_lastsync = _wait_for_completion(
        mirror_stack, previous_lastsync, "ACTIVE"
    )

    destination = mirror_stack.publish_dir / PACKAGE_ID
    payload = destination / "pool/main/m/mirror-test/mirror-test_1.0_amd64.deb"
    index = destination / "dists/bookworm/main/binary-amd64/Packages.gz"
    assert completed_lastsync > previous_lastsync
    assert payload.is_file(), f"Missing mirrored payload: {payload}"
    assert payload.read_bytes() == (
        FIXTURE_PATH / "v1" / payload.relative_to(destination)
    ).read_bytes()
    assert index.is_file(), f"Missing mirrored package index: {index}"
    assert "Version: 1.0" in gzip.decompress(index.read_bytes()).decode()
    assert "Returncode: 0" in _latest_log_text(mirror_stack)


@pytest.mark.integration
def test_debmirror_updates_and_removes_old_payload(mirror_stack: Any) -> None:
    """A v1-to-v2 repository update replaces the package and cleans up v1."""
    destination = mirror_stack.publish_dir / PACKAGE_ID
    old_payload = destination / "pool/main/m/mirror-test/mirror-test_1.0_amd64.deb"
    new_payload = destination / "pool/main/m/mirror-test/mirror-test_2.0_amd64.deb"
    assert old_payload.is_file(), f"Initial v1 payload is missing: {old_payload}"
    previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)

    _replace_fixture("v2")
    mirror_stack.trigger_sync(PACKAGE_ID)
    _wait_for_completion(mirror_stack, previous_lastsync, "ACTIVE")

    index = destination / "dists/bookworm/main/binary-amd64/Packages.gz"
    assert new_payload.is_file(), f"Updated v2 payload is missing: {new_payload}"
    assert new_payload.read_bytes() == (
        FIXTURE_PATH / "v2" / new_payload.relative_to(destination)
    ).read_bytes()
    assert not old_payload.exists(), f"Obsolete v1 payload was not deleted: {old_payload}"
    assert "Version: 2.0" in gzip.decompress(index.read_bytes()).decode()


@pytest.mark.integration
def test_debmirror_rejects_tampered_release_and_recovers(mirror_stack: Any) -> None:
    """A bad Release signature fails the sync, then pristine v1 recovers it."""
    previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)
    previous_errorcount = mirror_stack.package_errorcount(PACKAGE_ID)

    _tamper_release()
    mirror_stack.trigger_sync(PACKAGE_ID)
    failed_lastsync = _wait_for_completion(
        mirror_stack, previous_lastsync, "ERROR"
    )

    assert mirror_stack.package_errorcount(PACKAGE_ID) > previous_errorcount
    assert "BAD signature" in _latest_log_text(mirror_stack)

    _replace_fixture("v1")
    mirror_stack.trigger_sync(PACKAGE_ID)
    _wait_for_completion(mirror_stack, failed_lastsync, "ACTIVE")
    assert mirror_stack.package_errorcount(PACKAGE_ID) == 0
