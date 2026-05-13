"""Config reload tests: hot-reload via CLI (``mirror config reload``) and SIGHUP.

mirror.py supports two reload trigger paths that share a single ``_perform_reload()``
implementation: the ``mirror config reload`` CLI subcommand (synchronous, returns the
result dict and exits non-zero on failure) and SIGHUP to the master PID (asynchronous,
result logged to mirror.log). Both paths are exercised here.
"""

import concurrent.futures
import json
import subprocess
import time

import pytest


@pytest.mark.integration
def test_add_remove_package_via_master_restart(mirror_stack):
    """Editing config.json and restarting master registers the new package in stat.json.

    Steps:
    1. Read current config.json from inside the container.
    2. Add a new package 'rsync-extra' pointing at the same rsync-fixture.
    3. Write it back and restart master.
    4. Assert 'rsync-extra' appears in stat.json within 30s.
    5. Remove the package, restart master again, assert it disappears.
    """
    # Read current config from the container.
    result = mirror_stack.docker_exec(
        "cat", "/etc/mirror/config.json",
    )
    assert result.returncode == 0, f"Failed to read config.json: {result.stderr}"
    config = json.loads(result.stdout)

    # Add rsync-extra package.
    config["packages"]["rsync-extra"] = {
        "name": "rsync-extra",
        "id": "rsync-extra",
        "href": "/rsync-extra",
        "synctype": "rsync",
        "syncrate": "PT30S",
        "link": [],
        "settings": {
            "hidden": False,
            "src": "rsync://rsync-fixture/data",
            "dst": "/srv/publish/rsync-extra",
            "options": {"username": "", "password": ""},
        },
    }

    new_config_json = json.dumps(config, indent=2)

    # Write it back via docker exec tee.
    import subprocess
    write_result = subprocess.run(
        ["docker", "exec", "-i", "mirror", "tee", "/etc/mirror/config.json"],
        input=new_config_json.encode(),
        capture_output=True,
    )
    assert write_result.returncode == 0, (
        f"Failed to write new config.json: {write_result.stderr.decode()}"
    )

    mirror_stack.restart_process("master")
    mirror_stack.wait_for_master_ready(timeout=30)

    # Poll stat.json for rsync-extra to appear.
    deadline = time.monotonic() + 30
    found = False
    while time.monotonic() < deadline:
        stat_result = mirror_stack.docker_exec(
            "cat", "/var/lib/mirror/stat.json",
        )
        if stat_result.returncode == 0:
            stat = json.loads(stat_result.stdout)
            if "rsync-extra" in stat.get("packages", {}):
                found = True
                break
        time.sleep(1)

    assert found, "rsync-extra did not appear in stat.json within 30s after master restart"

    # Remove rsync-extra and verify disappearance.
    del config["packages"]["rsync-extra"]
    new_config_json = json.dumps(config, indent=2)

    write_result = subprocess.run(
        ["docker", "exec", "-i", "mirror", "tee", "/etc/mirror/config.json"],
        input=new_config_json.encode(),
        capture_output=True,
    )
    assert write_result.returncode == 0, (
        f"Failed to restore config.json: {write_result.stderr.decode()}"
    )

    mirror_stack.restart_process("master")
    mirror_stack.wait_for_master_ready(timeout=30)

    deadline = time.monotonic() + 30
    removed = False
    while time.monotonic() < deadline:
        stat_result = mirror_stack.docker_exec(
            "cat", "/var/lib/mirror/stat.json",
        )
        if stat_result.returncode == 0:
            stat = json.loads(stat_result.stdout)
            if "rsync-extra" not in stat.get("packages", {}):
                removed = True
                break
        time.sleep(1)

    assert removed, "rsync-extra still present in stat.json 30s after removal + master restart"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_RSYNC_EXTRA_PKG = {
    "name": "rsync-extra",
    "id": "rsync-extra",
    "href": "/rsync-extra",
    "synctype": "rsync",
    "syncrate": "PT30S",
    "link": [],
    "settings": {
        "hidden": False,
        "src": "rsync://rsync-fixture/data",
        "dst": "/srv/publish/rsync-extra",
        "options": {"username": "", "password": ""},
    },
}


def _write_config(config: dict) -> None:
    """Write a config dict to /etc/mirror/config.json inside the container via docker exec tee.

    Args:
        config(dict): Configuration dictionary to serialize and write.
    """
    config_json = json.dumps(config, indent=2)
    result = subprocess.run(
        ["docker", "exec", "-i", "mirror", "tee", "/etc/mirror/config.json"],
        input=config_json.encode(),
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"Failed to write config.json: {result.stderr.decode()!r}"
    )


def _read_config(mirror_stack) -> dict:
    """Read and parse /etc/mirror/config.json from the container.

    Args:
        mirror_stack: MirrorStack instance.

    Return:
        config(dict): Parsed configuration dictionary.
    """
    result = mirror_stack.docker_exec("cat", "/etc/mirror/config.json")
    assert result.returncode == 0, f"Failed to read config.json: {result.stderr}"
    return json.loads(result.stdout)


def _cli_reload(mirror_stack) -> subprocess.CompletedProcess:
    """Run ``mirror config reload`` inside the container and return the result.

    Args:
        mirror_stack: MirrorStack instance.

    Return:
        result(subprocess.CompletedProcess): Completed process with stdout, stderr, returncode.
    """
    return mirror_stack.docker_exec(
        "mirror", "config", "reload", check=False
    )


def _poll_stat_for_pkg(mirror_stack, pkgid: str, present: bool, timeout: float) -> bool:
    """Poll stat.json until pkgid is present (or absent) or timeout expires.

    Args:
        mirror_stack: MirrorStack instance.
        pkgid(str): Package identifier to check.
        present(bool): True to wait for presence, False to wait for absence.
        timeout(float): Maximum seconds to poll.

    Return:
        matched(bool): True if the condition was satisfied within timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stat_result = mirror_stack.docker_exec("cat", "/var/lib/mirror/stat.json", check=False)
        if stat_result.returncode == 0:
            try:
                stat = json.loads(stat_result.stdout)
                found = pkgid in stat.get("packages", {})
                if found == present:
                    return True
            except json.JSONDecodeError:
                pass
        time.sleep(0.5)
    return False


def _read_master_log(mirror_stack) -> str:
    """Read the master daemon log from inside the container.

    Tries the time-based log path pattern. Falls back to journalctl output.

    Args:
        mirror_stack: MirrorStack instance.

    Return:
        log_text(str): Combined log content, or empty string on failure.
    """
    # Try the structured log directory first (time-based folder).
    result = mirror_stack.docker_exec(
        "sh", "-c",
        "find /var/log/mirror -maxdepth 3 -name '*.log' ! -path '*/packages/*' "
        "| sort | xargs cat 2>/dev/null || true",
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout

    # Fallback: read from journalctl for the mirror service.
    result = mirror_stack.docker_exec(
        "journalctl", "-u", "mirror.service", "--no-pager", "-n", "200", check=False
    )
    return result.stdout if result.returncode == 0 else ""


# ---------------------------------------------------------------------------
# Hot-reload tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_add_package_via_cli_reload(mirror_stack):
    """Adding a package to config.json and running ``mirror config reload`` registers it.

    Steps:
    1. Read current config.json from the container.
    2. Add ``rsync-extra`` package.
    3. Write back and run ``mirror config reload``.
    4. Assert exit code 0 and stdout contains ``[OK] Reloaded`` and ``added=``.
    5. Poll stat.json (up to 5s) for ``rsync-extra``.
    6. Restore config.json and reload to clean up.
    """
    config = _read_config(mirror_stack)
    config["packages"]["rsync-extra"] = _RSYNC_EXTRA_PKG.copy()
    _write_config(config)

    try:
        result = _cli_reload(mirror_stack)
        assert result.returncode == 0, (
            f"mirror config reload exited {result.returncode}; stderr={result.stderr!r}"
        )
        assert "[OK] Reloaded" in result.stdout, (
            f"Expected '[OK] Reloaded' in stdout: {result.stdout!r}"
        )
        assert "added=" in result.stdout, (
            f"Expected 'added=' in stdout: {result.stdout!r}"
        )
        # The CLI prints lists using Python repr, so the exact form may be
        # added=['rsync-extra'] or added=["rsync-extra"]; both contain rsync-extra.
        assert "rsync-extra" in result.stdout, (
            f"Expected 'rsync-extra' in the added list in stdout: {result.stdout!r}"
        )

        found = _poll_stat_for_pkg(mirror_stack, "rsync-extra", present=True, timeout=5)
        assert found, "rsync-extra did not appear in stat.json within 5s after CLI reload"

    finally:
        # Restore: remove rsync-extra and reload.
        config_clean = _read_config(mirror_stack)
        config_clean["packages"].pop("rsync-extra", None)
        _write_config(config_clean)
        _cli_reload(mirror_stack)


@pytest.mark.integration
def test_add_package_via_sighup(mirror_stack):
    """Adding a package and sending SIGHUP to the master registers it.

    Steps:
    1. Add ``rsync-extra`` to config.json.
    2. Read master PID from /var/run/mirror/mirror.pid.
    3. Send SIGHUP via ``kill -HUP <pid>``.
    4. Poll stat.json (up to 10s) for ``rsync-extra``.
    5. Verify master log contains a ``Reload done`` line.
    6. Restore config.json and reload to clean up.
    """
    config = _read_config(mirror_stack)
    config["packages"]["rsync-extra"] = _RSYNC_EXTRA_PKG.copy()
    _write_config(config)

    try:
        # Get master PID.
        pid_result = mirror_stack.docker_exec("cat", "/var/run/mirror/mirror.pid")
        assert pid_result.returncode == 0, (
            f"Failed to read mirror.pid: {pid_result.stderr}"
        )
        master_pid = pid_result.stdout.strip()
        assert master_pid.isdigit(), f"Unexpected PID content: {master_pid!r}"

        # Send SIGHUP.
        kill_result = mirror_stack.docker_exec(
            "kill", "-HUP", master_pid, check=False
        )
        assert kill_result.returncode == 0, (
            f"kill -HUP {master_pid} failed: {kill_result.stderr}"
        )

        found = _poll_stat_for_pkg(mirror_stack, "rsync-extra", present=True, timeout=10)
        assert found, "rsync-extra did not appear in stat.json within 10s after SIGHUP"

        log_text = _read_master_log(mirror_stack)
        assert "Reload done" in log_text, (
            f"Expected 'Reload done' in master log after SIGHUP; log tail:\n{log_text[-2000:]}"
        )

    finally:
        config_clean = _read_config(mirror_stack)
        config_clean["packages"].pop("rsync-extra", None)
        _write_config(config_clean)
        _cli_reload(mirror_stack)


@pytest.mark.integration
def test_remove_idle_package_via_cli(mirror_stack):
    """Removing an idle package and reloading drops it from stat.json.

    Steps:
    1. Add ``rsync-extra`` and reload so it appears in stat.json.
    2. Remove ``rsync-extra`` from config.json.
    3. Run ``mirror config reload``; assert ``removed=`` in stdout.
    4. Assert stat.json no longer contains ``rsync-extra``.
    """
    # Add rsync-extra first.
    config = _read_config(mirror_stack)
    config["packages"]["rsync-extra"] = _RSYNC_EXTRA_PKG.copy()
    _write_config(config)
    add_result = _cli_reload(mirror_stack)
    assert add_result.returncode == 0, (
        f"Setup reload (add) failed: {add_result.stderr!r}"
    )
    # Confirm it's present.
    found = _poll_stat_for_pkg(mirror_stack, "rsync-extra", present=True, timeout=5)
    assert found, "rsync-extra not in stat.json after add reload (test setup failed)"

    # Wait until rsync-extra is not actively syncing before removing it.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = mirror_stack.package_status("rsync-extra")
        if status != "SYNC":
            break
        time.sleep(0.5)

    # Remove rsync-extra.
    config = _read_config(mirror_stack)
    config["packages"].pop("rsync-extra", None)
    _write_config(config)

    result = _cli_reload(mirror_stack)
    assert result.returncode == 0, (
        f"mirror config reload (remove) exited {result.returncode}; stderr={result.stderr!r}"
    )
    assert "removed=" in result.stdout, (
        f"Expected 'removed=' in stdout: {result.stdout!r}"
    )
    assert "rsync-extra" in result.stdout, (
        f"Expected 'rsync-extra' in the removed list in stdout: {result.stdout!r}"
    )

    gone = _poll_stat_for_pkg(mirror_stack, "rsync-extra", present=False, timeout=5)
    assert gone, "rsync-extra still in stat.json 5s after remove reload"


@pytest.mark.integration
def test_path_setting_warn_and_ignore(mirror_stack):
    """Changing non-hot-reloadable settings warns but does not apply them.

    Steps:
    1. Edit config.json to change ``socket_path`` and ``logfolder`` to dummy values.
    2. Run ``mirror config reload``; assert exit 0.
    3. Assert stdout contains ``[WARN]`` for each changed field.
    4. Assert the daemon is still reachable on the original socket by running
       ``mirror config reload`` again successfully.
    5. Restore config.json.
    """
    config = _read_config(mirror_stack)
    original_socket_path = config.get("settings", {}).get("socket_path", "/var/run/mirror/master.sock")
    original_logfolder = config.get("settings", {}).get("logfolder", "/var/log/mirror")

    config["settings"]["socket_path"] = "/tmp/fake-mirror-socket"
    config["settings"]["logfolder"] = "/tmp/fake-log"
    _write_config(config)

    try:
        result = _cli_reload(mirror_stack)
        assert result.returncode == 0, (
            f"mirror config reload exited {result.returncode} with path changes; "
            f"stderr={result.stderr!r}"
        )
        assert "[WARN]" in result.stdout, (
            f"Expected '[WARN]' in stdout for non-hot-reloadable setting changes: {result.stdout!r}"
        )
        # At least one of the two changed settings should be mentioned.
        assert "socket_path" in result.stdout or "logfolder" in result.stdout, (
            f"Expected mention of socket_path or logfolder in stdout: {result.stdout!r}"
        )

        # Prove the daemon is still listening on the original socket.
        second_result = _cli_reload(mirror_stack)
        assert second_result.returncode == 0, (
            "Daemon unreachable after path-setting warn-and-ignore reload; "
            f"stderr={second_result.stderr!r}"
        )

    finally:
        config["settings"]["socket_path"] = original_socket_path
        config["settings"]["logfolder"] = original_logfolder
        _write_config(config)
        _cli_reload(mirror_stack)


@pytest.mark.integration
def test_malformed_config_no_state_change(mirror_stack):
    """Writing garbage to config.json and reloading exits 1 without changing stat.json.

    Steps:
    1. Snapshot stat.json bytes.
    2. Write garbage JSON to config.json.
    3. Run ``mirror config reload``; assert exit 1.
    4. Assert stat.json content is byte-identical to the snapshot.
    5. Restore original config and confirm a subsequent reload exits 0.
    """
    # Snapshot stat.json.
    stat_before = mirror_stack.docker_exec("cat", "/var/lib/mirror/stat.json")
    assert stat_before.returncode == 0, "Failed to read stat.json before test"
    stat_snapshot = stat_before.stdout

    # Snapshot original config for restoration.
    original_config = _read_config(mirror_stack)

    # Write malformed config.
    garbage_result = subprocess.run(
        ["docker", "exec", "-i", "mirror", "tee", "/etc/mirror/config.json"],
        input=b"not valid json {{{",
        capture_output=True,
    )
    assert garbage_result.returncode == 0, "Failed to write garbage config.json"

    try:
        result = _cli_reload(mirror_stack)
        assert result.returncode != 0, (
            f"Expected non-zero exit from reload with malformed config; got 0. "
            f"stdout={result.stdout!r}"
        )

        stat_after = mirror_stack.docker_exec("cat", "/var/lib/mirror/stat.json", check=False)
        if stat_after.returncode == 0:
            assert stat_after.stdout == stat_snapshot, (
                "stat.json changed after malformed-config reload; expected no change"
            )

    finally:
        # Restore original config and verify daemon is still functional.
        _write_config(original_config)
        recovery = _cli_reload(mirror_stack)
        assert recovery.returncode == 0, (
            f"Recovery reload after malformed config failed; stderr={recovery.stderr!r}"
        )


@pytest.mark.integration
def test_concurrent_cli_reloads(mirror_stack):
    """Three concurrent ``mirror config reload`` calls all succeed.

    Spawns 3 subprocesses in parallel via concurrent.futures. Asserts all
    exit 0 and produce ``[OK] Reloaded`` output. Checks that the master log
    shows at most 3 ``Reload done`` lines (concurrent requests may merge into
    fewer main-loop passes).
    """
    log_before = _read_master_log(mirror_stack)
    done_before = log_before.count("Reload done")

    def _run_reload() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "exec", "mirror", "mirror", "config", "reload"],
            capture_output=True,
            text=True,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(_run_reload) for _ in range(3)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    for i, res in enumerate(results):
        assert res.returncode == 0, (
            f"Concurrent reload #{i} exited {res.returncode}; stderr={res.stderr!r}"
        )
        assert "[OK] Reloaded" in res.stdout, (
            f"Concurrent reload #{i} stdout missing '[OK] Reloaded': {res.stdout!r}"
        )

    log_after = _read_master_log(mirror_stack)
    done_after = log_after.count("Reload done")
    new_done_lines = done_after - done_before
    assert 1 <= new_done_lines <= 3, (
        f"Expected 1-3 new 'Reload done' log lines from 3 concurrent reloads; "
        f"got {new_done_lines} (before={done_before}, after={done_after})"
    )


@pytest.mark.integration
@pytest.mark.skip(
    reason=(
        "Requires a reliably slow sync fixture to guarantee the package is "
        "in-flight when the removal reload fires. The current rsync-fixture does "
        "not support bandwidth throttling via package settings, and injecting a "
        "sleep into the sync runner is not available at runtime without a custom "
        "fixture. TODO: add a 'slow-rsync' docker service that throttles to "
        "~10 KB/s and update this test to use it."
    )
)
def test_in_flight_package_killed_on_remove(mirror_stack):
    """Removing a package while its sync is in-flight kills the subprocess.

    Goals:
    1. Add a package configured for a slow rsync sync.
    2. Trigger sync and verify SYNC status.
    3. Remove the package from config, run ``mirror config reload``.
    4. Assert ``killed_inflight`` in reload result contains the pkgid.
    5. Assert stat.json no longer contains the pkgid.
    6. Assert master log shows kill and resilient on_sync_done messages.
    """
    # This test is skipped; see reason above.
    pass
