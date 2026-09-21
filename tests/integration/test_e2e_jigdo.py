"""End-to-end tests for Debian CD reconstruction with the real jigdo tools."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest


_FIXTURE_ROOT = "/tmp/mirror-jigdo-e2e"
_VERSION = "1.0"
_ARCH = "amd64"
_SET = "dvd"
_HOSTNAME = "mirror.test"
_JIGDO_FILE_COMMAND = "jigdo-file --report=noprogress"


@pytest.fixture
def jigdo_fixture(mirror_stack):
    """Create three tiny Debian DVD jigdo images with the real jigdo-file CLI."""
    script = rf"""
set -euo pipefail
root={_FIXTURE_ROOT}
rm -rf "$root"
mkdir -p \
  "$root/debian/pool/main/p/jigdo-e2e" \
  "$root/reference" \
  "$root/cdimage/{_VERSION}/{_ARCH}/jigdo-{_SET}" \
  "$root/cdimage/{_VERSION}/{_ARCH}/iso-{_SET}" \
  "$root/cdimage/project/build/{_VERSION}" \
  "$root/cdimage/project/trace"

for disc in 1 2 3; do
  part="$root/debian/pool/main/p/jigdo-e2e/disc-$disc.deb"
  image="debian-{_VERSION}-{_ARCH}-DVD-$disc.iso"
  head -c 4096 /dev/zero | tr '\000' "$disc" > "$part"
  {{
    printf 'Debian jigdo integration DVD-%s\n' "$disc"
    cat "$part"
    printf '\nend DVD-%s\n' "$disc"
  }} > "$root/reference/$image"
  jigdo-file make-template \
    --force \
    --report=noprogress \
    --image="$root/reference/$image" \
    --jigdo="$root/cdimage/{_VERSION}/{_ARCH}/jigdo-{_SET}/$image.jigdo" \
    --template="$root/cdimage/{_VERSION}/{_ARCH}/jigdo-{_SET}/$image.template" \
    --label="Debian=$root/debian" \
    --uri="Debian=file:$root/debian" \
    "$root/debian//"
done

(
  cd "$root/reference"
  sha512sum debian-*.iso
) > "$root/cdimage/{_VERSION}/{_ARCH}/iso-{_SET}/SHA512SUMS"
cp \
  "$root/reference/debian-{_VERSION}-{_ARCH}-DVD-1.iso" \
  "$root/cdimage/{_VERSION}/{_ARCH}/iso-{_SET}/"
printf '{_SET}\n' > "$root/cdimage/project/build/{_VERSION}/{_ARCH}"
ln -s {_VERSION} "$root/cdimage/current"
"""
    result = mirror_stack.docker_exec(
        "bash", "-c", script, check=False
    )
    assert result.returncode == 0, (
        "Failed to create the jigdo fixture with jigdo-file; "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )

    yield {
        "src": f"{_FIXTURE_ROOT}/cdimage",
        "debian_mirror": f"file:{_FIXTURE_ROOT}/debian",
        "missing_part": (
            f"{_FIXTURE_ROOT}/debian/pool/main/p/jigdo-e2e/disc-3.deb"
        ),
    }

    mirror_stack.docker_exec("rm", "-rf", _FIXTURE_ROOT, check=False)


@pytest.mark.integration
def test_jigdo_standalone_reconstructs_all_three_dvds(
    mirror_stack, jigdo_fixture
):
    """Standalone sync reconstructs DVD 1-3 and preserves them on a second run."""
    destination = "/srv/publish/jigdo-standalone"
    first = _run_standalone(
        mirror_stack,
        src=jigdo_fixture["src"],
        destination=destination,
        debian_mirror=jigdo_fixture["debian_mirror"],
    )
    assert first.returncode == 0, _command_failure("first standalone sync", first)

    publish_root = mirror_stack.publish_dir / "jigdo-standalone"
    hashes_before = _assert_complete_mirror(publish_root)

    second = _run_standalone(
        mirror_stack,
        src=jigdo_fixture["src"],
        destination=destination,
        debian_mirror=jigdo_fixture["debian_mirror"],
    )
    assert second.returncode == 0, _command_failure("second standalone sync", second)
    assert _assert_complete_mirror(publish_root) == hashes_before


@pytest.mark.integration
def test_jigdo_daemon_reports_success_and_failed_reconstruction(
    mirror_stack, jigdo_fixture
):
    """Daemon records ACTIVE on success and ERROR without replacing trace on failure."""
    package_id = "jigdo-test"
    destination = "/srv/publish/jigdo-daemon"
    original_config = _read_config(mirror_stack)
    config = json.loads(json.dumps(original_config))
    config["packages"][package_id] = {
        "name": package_id,
        "id": package_id,
        "href": "/jigdo-test",
        "synctype": "jigdo",
        "syncrate": "P1D",
        "link": [],
        "settings": {
            "hidden": False,
            "src": jigdo_fixture["src"],
            "dst": destination,
            "options": {
                "jigdo_file": _JIGDO_FILE_COMMAND,
                "debian_mirror": jigdo_fixture["debian_mirror"],
                "jigdo_include": (
                    rf".*{_ARCH}-(CD|DVD)-[1-3]\.iso.*"
                ),
                "jigdo_exclude": "$^",
                "final_includes": [f"{_ARCH}/**.iso"],
            },
        },
    }
    _write_config(config)

    try:
        reload_result = mirror_stack.docker_exec(
            "mirror", "config", "reload", check=False
        )
        assert reload_result.returncode == 0, (
            "Failed to load jigdo package into daemon; "
            f"stdout={reload_result.stdout!r}, stderr={reload_result.stderr!r}"
        )
        mirror_stack.wait_for_status(package_id, "ACTIVE", timeout=90)

        publish_root = mirror_stack.publish_dir / "jigdo-daemon"
        _assert_complete_mirror(publish_root)
        trace = publish_root / "project" / "trace" / _HOSTNAME
        assert trace.is_file()

        mirror_stack.docker_exec(
            "sh", "-c",
            f"printf 'sentinel\\n' > {destination}/project/trace/{_HOSTNAME}",
        )
        mirror_stack.docker_exec("rm", jigdo_fixture["missing_part"])
        mirror_stack.docker_exec(
            "find",
            f"{destination}/{_VERSION}/{_ARCH}/iso-{_SET}",
            "-maxdepth", "1",
            "-name", "*.iso",
            "-delete",
        )

        mirror_stack.trigger_sync(package_id)
        mirror_stack.wait_for_status(package_id, "ERROR", timeout=90)

        assert trace.read_text() == "sentinel\n"
        assert mirror_stack.package_errorcount(package_id) >= 1
    finally:
        _write_config(original_config)
        mirror_stack.docker_exec("mirror", "config", "reload", check=False)


def _run_standalone(mirror_stack, *, src: str, destination: str, debian_mirror: str):
    """Run the public worker-execute jigdo CLI inside the integration container."""
    return mirror_stack.docker_exec(
        "mirror", "worker-execute", "jigdo",
        "--src", src,
        "--dst", destination,
        "--jigdo-file", _JIGDO_FILE_COMMAND,
        "--debian-mirror", debian_mirror,
        "--hostname", _HOSTNAME,
        "--final-include", f"{_ARCH}/**.iso",
        check=False,
    )


def _assert_complete_mirror(root: Path) -> dict[str, str]:
    """Assert the official Debian CD layout and hashes for DVD 1-3."""
    current = root / "current"
    assert current.is_symlink()
    assert os.readlink(current) == _VERSION

    jigdo_dir = root / _VERSION / _ARCH / f"jigdo-{_SET}"
    image_dir = root / _VERSION / _ARCH / f"iso-{_SET}"
    expected_names = {
        f"debian-{_VERSION}-{_ARCH}-DVD-{disc}.iso"
        for disc in range(1, 4)
    }
    assert {path.name for path in image_dir.glob("*.iso")} == expected_names
    assert len(list(jigdo_dir.glob("*.jigdo"))) == 3
    assert len(list(jigdo_dir.glob("*.template"))) == 3

    checksums = _read_checksums(image_dir / "SHA512SUMS")
    assert set(checksums) == expected_names
    actual = {
        name: hashlib.sha512((image_dir / name).read_bytes()).hexdigest()
        for name in expected_names
    }
    assert actual == checksums
    return actual


def _read_checksums(path: Path) -> dict[str, str]:
    """Read a Debian checksum file into a filename-to-digest mapping."""
    checksums = {}
    for line in path.read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        checksums[name.lstrip(" *")] = digest
    return checksums


def _read_config(mirror_stack) -> dict:
    """Read the daemon configuration from the integration container."""
    result = mirror_stack.docker_exec("cat", "/etc/mirror/config.json")
    return json.loads(result.stdout)


def _write_config(config: dict) -> None:
    """Write the daemon configuration into the integration container."""
    result = subprocess.run(
        ["docker", "exec", "-i", "mirror", "tee", "/etc/mirror/config.json"],
        input=json.dumps(config, indent=2).encode(),
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def _command_failure(label: str, result: subprocess.CompletedProcess) -> str:
    """Format complete command output for an assertion failure."""
    return (
        f"{label} failed with rc={result.returncode}; "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
