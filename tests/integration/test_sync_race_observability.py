"""Integration tests for sync-race observability.

Verifies that the race-condition fixes on fix/sync-race-conditions do not
emit spurious error/mismatch log lines during normal sync completion,
concurrent multi-package completions, or heavy RPC pressure.
"""

import concurrent.futures
import json
import subprocess
import threading
import time

import pytest


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

POST_COMPLETION_OBSERVATION_SECONDS = 8

RACE_LINE_SUBSTRINGS = (
    "marked as syncing but no worker found",
    "Package is syncing while status is",
)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _master_log_tail_offset(mirror_stack) -> int:
    """Return current byte count of master log files so a slice can be read later.

    Args:
        mirror_stack: MirrorStack instance.

    Return:
        offset(int): Total byte count of all master log files at call time.
    """
    result = mirror_stack.docker_exec(
        "sh", "-c",
        "find /var/log/mirror -maxdepth 3 -name '*.log' ! -path '*/packages/*' "
        "| sort | xargs -r wc -c | tail -1 | awk '{print $1}'",
        check=False,
    )
    try:
        return int(result.stdout.strip() or "0")
    except ValueError:
        return 0


def _read_master_log_slice(mirror_stack, offset: int) -> str:
    """Return master log content beyond the given byte offset.

    Reads the full master log and slices in Python. Master log volume is
    small enough for this approach.

    Args:
        mirror_stack: MirrorStack instance.
        offset(int): Byte offset to start reading from.

    Return:
        slice_text(str): Log content after the given offset.
    """
    result = mirror_stack.docker_exec(
        "sh", "-c",
        "find /var/log/mirror -maxdepth 3 -name '*.log' ! -path '*/packages/*' "
        "| sort | xargs cat 2>/dev/null || true",
        check=False,
    )
    text = result.stdout if result.returncode == 0 else ""
    return text[offset:]


def _assert_no_race_lines(slice_text: str) -> None:
    """Assert that no race-trigger lines appear in the slice (globally, no pkgid filter).

    The elif branch log line `Package is syncing while status is X` (daemon.py:252-254)
    does NOT include the pkgid, so we cannot filter by pkgid. Both substrings are rare
    enough that any occurrence within the scoped completion window indicates a real
    race-trigger fire.

    Args:
        slice_text(str): Log slice to check for race-trigger lines.
    """
    offenders = [
        line for line in slice_text.splitlines()
        if any(sub in line for sub in RACE_LINE_SUBSTRINGS)
    ]
    assert not offenders, "Race-trigger log lines found:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_clean_completion_emits_no_race_lines(mirror_stack):
    """A normal single-package sync completion does not emit race-trigger log lines.

    Steps:
    1. Capture master log byte offset and errorcount before sync.
    2. Trigger sync for rsync-test.
    3. Wait for ACTIVE status.
    4. Sleep POST_COMPLETION_OBSERVATION_SECONDS so the daemon iterates past
       the MISMATCH_GRACE_SECONDS window if a race fired.
    5. Assert no race-trigger substrings appear in the log slice.
    6. Assert errorcount is unchanged and status is ACTIVE.
    """
    pkgid = "rsync-test"

    offset = _master_log_tail_offset(mirror_stack)
    errorcount_before = mirror_stack.package_errorcount(pkgid)

    mirror_stack.trigger_sync(pkgid)
    mirror_stack.wait_for_status(pkgid, "ACTIVE", timeout=60)

    time.sleep(POST_COMPLETION_OBSERVATION_SECONDS)

    log_slice = _read_master_log_slice(mirror_stack, offset)
    _assert_no_race_lines(log_slice)

    assert mirror_stack.package_errorcount(pkgid) == errorcount_before, (
        f"errorcount changed after a clean sync: "
        f"before={errorcount_before}, "
        f"after={mirror_stack.package_errorcount(pkgid)}"
    )
    assert mirror_stack.package_status(pkgid) == "ACTIVE", (
        f"Expected ACTIVE after clean sync, got {mirror_stack.package_status(pkgid)!r}"
    )


@pytest.mark.integration
def test_multi_package_concurrent_completion(mirror_stack):
    """Concurrent completion of 6 packages does not emit race-trigger log lines.

    Adds 6 short rsync packages (rsync-race-1 through rsync-race-6), triggers
    them all simultaneously, and verifies that the concurrent prune_finished
    and notification traffic does not trip either race-trigger log path.
    """
    race_pkgids = [f"rsync-race-{i}" for i in range(1, 7)]

    # Read current config.
    result = mirror_stack.docker_exec("cat", "/etc/mirror/config.json")
    assert result.returncode == 0, f"Failed to read config.json: {result.stderr}"
    original_config_bytes = result.stdout.encode()
    config = json.loads(result.stdout)

    # Add 6 race packages to config.
    for pkgid in race_pkgids:
        config["packages"][pkgid] = {
            "name": pkgid,
            "id": pkgid,
            "href": f"/{pkgid}",
            "synctype": "rsync",
            "syncrate": "PT60S",
            "link": [],
            "settings": {
                "hidden": False,
                "src": "rsync://rsync-fixture/data",
                "dst": f"/srv/publish/{pkgid}",
                "options": {"username": "", "password": ""},
            },
        }

    try:
        new_config_json = json.dumps(config, indent=2)
        subprocess.run(
            ["docker", "exec", "-i", "mirror", "tee", "/etc/mirror/config.json"],
            input=new_config_json.encode(),
            check=True,
            stdout=subprocess.DEVNULL,
        )

        mirror_stack.restart_process("master")
        mirror_stack.wait_for_master_ready(timeout=30)

        # Wait for the initial auto-sync (lastsync=0 -> immediate) of all 6 to
        # complete before capturing the log offset and triggering the test wave.
        for pkgid in race_pkgids:
            mirror_stack.wait_for_status(pkgid, "ACTIVE", timeout=120)

        offset = _master_log_tail_offset(mirror_stack)
        errorcount_before = {
            pkgid: mirror_stack.package_errorcount(pkgid)
            for pkgid in race_pkgids
        }

        # Trigger all 6 syncs concurrently.
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            trigger_futures = [
                executor.submit(mirror_stack.trigger_sync, pkgid)
                for pkgid in race_pkgids
            ]
            for f in concurrent.futures.as_completed(trigger_futures):
                f.result()

        for pkgid in race_pkgids:
            mirror_stack.wait_for_status(pkgid, "ACTIVE", timeout=90)

        time.sleep(POST_COMPLETION_OBSERVATION_SECONDS)

        log_slice = _read_master_log_slice(mirror_stack, offset)
        _assert_no_race_lines(log_slice)

        for pkgid in race_pkgids:
            after = mirror_stack.package_errorcount(pkgid)
            assert after == errorcount_before[pkgid], (
                f"errorcount changed for {pkgid}: "
                f"before={errorcount_before[pkgid]}, after={after}"
            )

    finally:
        subprocess.run(
            ["docker", "exec", "-i", "mirror", "tee", "/etc/mirror/config.json"],
            input=original_config_bytes,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        mirror_stack.restart_process("master")
        mirror_stack.wait_for_master_ready(timeout=30)

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            data = mirror_stack.stat_json()
            statuses = {
                pid: pkg.get("status", {}).get("status", "UNKNOWN")
                for pid, pkg in data.get("packages", {}).items()
            }
            if all(s != "SYNC" for s in statuses.values()):
                break
            time.sleep(0.5)


@pytest.mark.integration
def test_get_progress_hammer_during_sync(mirror_stack):
    """Hammering is_worker_running RPC from 16 concurrent callers during sync completion.

    Exercises the concurrent prune_finished path in the worker while a sync is
    finishing. Verifies that no race-trigger log lines appear after the
    observation window elapses.
    """
    pkgid = "rsync-test"

    offset = _master_log_tail_offset(mirror_stack)
    errorcount_before = mirror_stack.package_errorcount(pkgid)

    mirror_stack.trigger_sync(pkgid)

    stop_event = threading.Event()
    hammer_deadline = time.monotonic() + 70

    def _hammer():
        while not stop_event.is_set() and time.monotonic() < hammer_deadline:
            mirror_stack.docker_exec(
                "python", "-c",
                "from mirror.socket.worker import is_worker_running; "
                "print(is_worker_running('rsync-test'))",
                check=False,
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(_hammer) for _ in range(16)]
        try:
            mirror_stack.wait_for_status(pkgid, "ACTIVE", timeout=60)
        finally:
            stop_event.set()

        time.sleep(POST_COMPLETION_OBSERVATION_SECONDS)
        # ThreadPoolExecutor.__exit__ joins all workers.

    for f in futures:
        f.result()

    log_slice = _read_master_log_slice(mirror_stack, offset)
    _assert_no_race_lines(log_slice)

    assert mirror_stack.package_errorcount(pkgid) == errorcount_before, (
        f"errorcount changed after hammer test: "
        f"before={errorcount_before}, "
        f"after={mirror_stack.package_errorcount(pkgid)}"
    )
