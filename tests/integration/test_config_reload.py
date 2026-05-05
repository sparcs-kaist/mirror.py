"""Config reload test: add a new package via config.json edit + master restart.

mirror.py does not support SIGHUP-based reload; config reload requires restarting master.
"""

import json
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
