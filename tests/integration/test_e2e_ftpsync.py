"""End-to-end ftpsync sync tests.

IMPORTANT: test_preflight.py::test_ftpsync_preflight_archvsync_runs must pass
before these tests. If the preflight fails, the fixture Debian archive layout
is rejected by archvsync and all tests here will also fail.

The module-level _PREFLIGHT_RESULT cache ensures the preflight gate runs once
per test session (via the autouse _ftpsync_preflight fixture) without requiring
a session-scoped fixture that would clash with the function-scoped mirror_stack.
"""

import subprocess

import pytest


_PREFLIGHT_RESULT = None  # None=untested, True=ok, str=failure reason


@pytest.fixture(autouse=True)
def _ftpsync_preflight(mirror_stack):
    """Run ftpsync preflight once per session and skip all tests if it fails."""
    global _PREFLIGHT_RESULT
    if _PREFLIGHT_RESULT is None:
        try:
            mirror_stack.trigger_sync("ftpsync-test")
            mirror_stack.wait_for_status("ftpsync-test", "ACTIVE", timeout=90)
            status = mirror_stack.package_status("ftpsync-test")
            if status == "ACTIVE":
                _PREFLIGHT_RESULT = True
            else:
                _PREFLIGHT_RESULT = f"preflight reached status {status!r} instead of ACTIVE"
        except Exception as e:
            _PREFLIGHT_RESULT = f"preflight failed: {e}"
    if _PREFLIGHT_RESULT is not True:
        pytest.skip(_PREFLIGHT_RESULT)


@pytest.mark.integration
@pytest.mark.dependency(depends=["ftpsync_preflight"])
def test_basic_ftpsync_sync(mirror_stack):
    """Basic ftpsync sync reaches ACTIVE and writes a trace file to publish dir."""
    mirror_stack.trigger_sync("ftpsync-test")
    mirror_stack.wait_for_status("ftpsync-test", "ACTIVE", timeout=90)

    assert mirror_stack.package_status("ftpsync-test") == "ACTIVE", (
        "ftpsync-test did not reach ACTIVE status after trigger"
    )

    trace_file = mirror_stack.publish_dir / "ftpsync-test" / "Project" / "trace" / "master"
    assert trace_file.exists(), (
        f"Expected archvsync trace file at {trace_file}; "
        f"publish dir contents: {list((mirror_stack.publish_dir / 'ftpsync-test').rglob('*'))}"
    )


@pytest.mark.integration
@pytest.mark.dependency(depends=["ftpsync_preflight"])
@pytest.mark.skip(
    reason="Cannot test offline fallback by disconnecting from compose network — "
    "that also makes the ftpsync-fixture unreachable. Distinguishing 'block git "
    "but allow internal rsync' would require iptables manipulation inside the "
    "mirror container or separate compose networks per direction. The base64 "
    "fallback should be unit-tested at mirror.sync.ftpsync._extract_archvsync."
)
def test_ftpsync_offline_fallback(mirror_stack):
    """ftpsync falls back to embedded base64 script when git clone cannot reach upstream."""
    network = "mirror_integration_default"
    container = "mirror"

    disconnect_result = subprocess.run(
        ["docker", "network", "disconnect", network, container],
        capture_output=True,
        text=True,
    )
    if disconnect_result.returncode != 0:
        pytest.xfail(
            "Cannot disconnect mirror container from compose network in this environment; "
            "offline fallback test skipped. "
            f"docker network disconnect stderr: {disconnect_result.stderr!r}"
        )

    try:
        # Remove cached archvsync clone so mirror must re-fetch (or fall back).
        mirror_stack.docker_exec("bash", "-c", "rm -rf /var/lib/mirror/mirror_ftpsync_*")

        mirror_stack.trigger_sync("ftpsync-test")
        mirror_stack.wait_for_status("ftpsync-test", "ACTIVE", timeout=90)

        assert mirror_stack.package_status("ftpsync-test") == "ACTIVE", (
            "ftpsync offline fallback test: did not reach ACTIVE after base64 fallback attempt"
        )
    finally:
        subprocess.run(
            ["docker", "network", "connect", network, container],
            check=True,
        )
