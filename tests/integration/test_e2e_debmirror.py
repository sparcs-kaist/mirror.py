"""End-to-end debmirror discovery tests against a signed Debian repository."""

import gzip
import json
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest


PACKAGE_ID = "debmirror-test"
FIXTURE_CONTAINER = "apt-fixture"
FIXTURE_PATH = Path(__file__).parent / "docker" / "apt-fixture"


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
        "source = pathlib.Path('/srv/fixtures') / sys.argv[1] / 'debian'; "
        "target = pathlib.Path('/srv/data/debian'); "
        "shutil.rmtree(target, ignore_errors=True); "
        "shutil.copytree(source, target)"
    )
    _run_fixture_python(script, version)


def _set_listing_denied(denied: bool) -> None:
    """Toggle denial of only the HTTP dists directory listing."""
    script = (
        "import pathlib, sys; "
        "marker = pathlib.Path('/srv/data/deny-dists-listing'); "
        "marker.touch() if sys.argv[1] == '1' else marker.unlink(missing_ok=True)"
    )
    _run_fixture_python(script, "1" if denied else "0")


def _set_partial_listing(enabled: bool) -> None:
    """Toggle a valid dists listing that omits the bullseye distribution."""
    script = (
        "import pathlib, sys; "
        "marker = pathlib.Path('/srv/data/partial-dists-listing'); "
        "marker.touch() if sys.argv[1] == '1' else marker.unlink(missing_ok=True)"
    )
    _run_fixture_python(script, "1" if enabled else "0")


def _tamper_metadata() -> None:
    """Change signed metadata while leaving both signatures intact."""
    script = (
        "import pathlib; "
        "root = pathlib.Path('/srv/data/debian/dists/bookworm'); "
        "release = root / 'Release'; "
        "release.write_bytes(release.read_bytes().replace("
        "b'Description: Signed', b'Description: Tampered', 1)); "
        "inrelease = root / 'InRelease'; "
        "inrelease.write_bytes(inrelease.read_bytes().replace("
        "b'Description: Signed', b'Description: Tampered', 1))"
    )
    _run_fixture_python(script)


def _wait_for_completion(
    mirror_stack: Any,
    previous_lastsync: float,
    expected_status: str,
    timeout: float = 120,
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
def _package_options(mirror_stack: Any, selections: dict[str, Any]) -> Iterator[None]:
    """Apply temporary debmirror selections through the production reload path."""
    result = mirror_stack.docker_exec("cat", "/etc/mirror/config.json")
    original = result.stdout
    config = json.loads(original)
    options = config["packages"][PACKAGE_ID]["settings"]["options"]
    options.update(selections)
    _write_container_config(json.dumps(config, indent=2))
    reload_result = mirror_stack.docker_exec("mirror", "config", "reload", check=False)
    assert reload_result.returncode == 0, (
        f"Failed to reload temporary debmirror options: {reload_result.stderr!r}"
    )
    try:
        yield
    finally:
        _write_container_config(original)
        reload_result = mirror_stack.docker_exec("mirror", "config", "reload", check=False)
        assert reload_result.returncode == 0, (
            f"Failed to restore debmirror options: {reload_result.stderr!r}"
        )


def _clean_destination(mirror_stack: Any) -> None:
    """Remove the debmirror destination without replacing its bind mount."""
    mirror_stack.docker_exec(
        "sh", "-c", "rm -rf /srv/publish/debmirror-test; mkdir -p /srv/publish/debmirror-test"
    )


def _fixture_bytes(version: str, relative: str) -> bytes:
    """Read expected bytes from a repository fixture version."""
    return (FIXTURE_PATH / version / "debian" / relative).read_bytes()


@pytest.fixture(autouse=True)
def _reset_debmirror_fixture(docker_services: Any) -> Iterator[None]:
    """Serve signed v1 for every test and restore all fixture controls."""
    del docker_services
    _set_listing_denied(False)
    _set_partial_listing(False)
    _replace_fixture("v1")
    try:
        yield
    finally:
        _set_listing_denied(False)
        _set_partial_listing(False)
        _replace_fixture("v1")


@pytest.mark.integration
def test_debmirror_auto_discovers_and_rediscovers_repository(mirror_stack: Any) -> None:
    """Omitted dimensions mirror all binaries and discover v2 additions."""
    destination = mirror_stack.publish_dir / PACKAGE_ID
    bookworm_amd64 = "pool/main/m/mirror-test/mirror-test_1.0_amd64.deb"
    bookworm_arm64 = "pool/main/m/mirror-test/mirror-test_1.0_arm64.deb"
    extra = "pool/extras/e/extra-test/extra-test_1.0_amd64.deb"
    shared = "pool/main/m/mirror-common/mirror-common_1.0_all.deb"
    allonly = "pool/main/a/allonly-test/allonly-test_1.0_all.deb"

    assert mirror_stack.package_status(PACKAGE_ID) == "ACTIVE"
    for relative in (bookworm_amd64, bookworm_arm64, extra, shared, allonly):
        payload = destination / relative
        assert payload.read_bytes() == _fixture_bytes("v1", relative)
    assert not (destination / "pool/main/m/mirror-source").exists()
    assert not (destination / "dists/stable").exists()
    first_log = _latest_log_text(mirror_stack)
    assert "dist=allonly,bookworm,bullseye" in first_log
    assert "section=extras,main" in first_log
    assert "arch=amd64,arm64" in first_log

    previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)
    _replace_fixture("v2")
    mirror_stack.trigger_sync(PACKAGE_ID)
    _wait_for_completion(mirror_stack, previous_lastsync, "ACTIVE")

    updated = "pool/main/m/mirror-test/mirror-test_2.0_amd64.deb"
    riscv64 = "pool/main/m/mirror-test/mirror-test_2.0_riscv64.deb"
    partner = "pool/partner/p/partner-test/partner-test_1.0_amd64.deb"
    trixie = "pool/main/t/trixie-test/trixie-test_1.0_amd64.deb"
    for relative in (updated, riscv64, partner, trixie, shared, allonly):
        payload = destination / relative
        assert payload.read_bytes() == _fixture_bytes("v2", relative)
    assert not (destination / bookworm_amd64).exists()
    assert not (destination / "pool/main/m/mirror-source").exists()
    second_log = _latest_log_text(mirror_stack)
    assert "dist=allonly,bookworm,bullseye,trixie" in second_log
    assert "section=extras,main,partner" in second_log
    assert "arch=amd64,arm64,riscv64" in second_log
    mirror_stack.docker_exec(
        "python", "-c",
        "from pathlib import Path; assert not list(Path('/tmp').glob('mirror-debmirror-run-*'))",
    )


@pytest.mark.integration
def test_debmirror_explicit_subset_and_source(mirror_stack: Any) -> None:
    """Explicit list selections restrict binaries and source=true fetches sources."""
    selections = {
        "dist": ["bookworm"],
        "section": ["main"],
        "arch": ["amd64"],
        "source": True,
    }
    with _package_options(mirror_stack, selections):
        _clean_destination(mirror_stack)
        previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)
        mirror_stack.trigger_sync(PACKAGE_ID)
        _wait_for_completion(mirror_stack, previous_lastsync, "ACTIVE")

        destination = mirror_stack.publish_dir / PACKAGE_ID
        binary = "pool/main/m/mirror-test/mirror-test_1.0_amd64.deb"
        dsc = "pool/main/m/mirror-source/mirror-source_1.0.dsc"
        source_tar = "pool/main/m/mirror-source/mirror-source_1.0.tar.xz"
        for relative in (binary, dsc, source_tar):
            assert (destination / relative).read_bytes() == _fixture_bytes("v1", relative)
        assert not (destination / "pool/main/m/mirror-test/mirror-test_1.0_arm64.deb").exists()
        assert not (destination / "pool/extras").exists()
        assert not (destination / "dists/bullseye").exists()


@pytest.mark.integration
def test_debmirror_signature_failure_preserves_mirror_and_recovers(mirror_stack: Any) -> None:
    """Bad signed metadata fails without deleting the prior mirror, then recovers."""
    destination = mirror_stack.publish_dir / PACKAGE_ID
    payload = destination / "pool/main/m/mirror-test/mirror-test_1.0_amd64.deb"
    index = destination / "dists/bookworm/main/binary-amd64/Packages.gz"
    original_payload = payload.read_bytes()
    original_index = index.read_bytes()
    previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)
    previous_errorcount = mirror_stack.package_errorcount(PACKAGE_ID)

    _tamper_metadata()
    mirror_stack.trigger_sync(PACKAGE_ID)
    failed_lastsync = _wait_for_completion(mirror_stack, previous_lastsync, "ERROR")

    assert mirror_stack.package_errorcount(PACKAGE_ID) > previous_errorcount
    assert payload.read_bytes() == original_payload
    assert index.read_bytes() == original_index
    assert "signature verification failed" in _latest_log_text(mirror_stack)

    _replace_fixture("v1")
    mirror_stack.trigger_sync(PACKAGE_ID)
    _wait_for_completion(mirror_stack, failed_lastsync, "ACTIVE")
    assert mirror_stack.package_errorcount(PACKAGE_ID) == 0


@pytest.mark.integration
def test_explicit_allonly_dist_bypasses_listing_and_uses_arch_none(
    mirror_stack: Any,
) -> None:
    """A denied listing fails auto discovery, while explicit all-only dist works."""
    destination = mirror_stack.publish_dir / PACKAGE_ID
    preserved = destination / "pool/main/m/mirror-test/mirror-test_1.0_amd64.deb"
    preserved_bytes = preserved.read_bytes()
    previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)

    _set_listing_denied(True)
    mirror_stack.trigger_sync(PACKAGE_ID)
    failed_lastsync = _wait_for_completion(mirror_stack, previous_lastsync, "ERROR")
    assert preserved.read_bytes() == preserved_bytes
    assert "status 403" in _latest_log_text(mirror_stack)

    with _package_options(mirror_stack, {"dist": "allonly"}):
        _clean_destination(mirror_stack)
        mirror_stack.trigger_sync(PACKAGE_ID)
        _wait_for_completion(mirror_stack, failed_lastsync, "ACTIVE")

        relative = "pool/main/a/allonly-test/allonly-test_1.0_all.deb"
        payload = destination / relative
        assert payload.read_bytes() == _fixture_bytes("v1", relative)
        log = _latest_log_text(mirror_stack)
        assert "dist=allonly section=main arch=none" in log
        assert "Returncode: 0" in log


@pytest.mark.integration
def test_partial_dist_listing_preserves_existing_distributions(
    mirror_stack: Any,
) -> None:
    """A partial successful listing cannot clean an existing distribution."""
    destination = mirror_stack.publish_dir / PACKAGE_ID
    release = destination / "dists/bullseye/Release"
    shared = destination / "pool/main/m/mirror-common/mirror-common_1.0_all.deb"
    release_bytes = release.read_bytes()
    shared_bytes = shared.read_bytes()
    previous_lastsync = mirror_stack.package_lastsync(PACKAGE_ID)

    _set_partial_listing(True)
    mirror_stack.trigger_sync(PACKAGE_ID)
    failed_lastsync = _wait_for_completion(mirror_stack, previous_lastsync, "ERROR")

    assert release.read_bytes() == release_bytes
    assert shared.read_bytes() == shared_bytes
    assert "existing distributions are missing" in _latest_log_text(mirror_stack)

    _set_partial_listing(False)
    mirror_stack.trigger_sync(PACKAGE_ID)
    _wait_for_completion(mirror_stack, failed_lastsync, "ACTIVE")
