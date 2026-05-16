import json
import os
import platform
import subprocess
from pathlib import Path

from prompt_toolkit.shortcuts import print_formatted_text

from mirror.config.config import DEFAULT_CONFIG
from mirror.toolbox import command_exists

_REQUIRED_BINARIES = ["rsync", "lftp", "bandersnatch"]
_OPTIONAL_BINARIES = ["git"]

_DIRECTORIES = [
    Path("/etc/mirror"),
    Path("/var/run/mirror"),
    Path("/var/lib/mirror"),
    Path("/var/log/mirror"),
    Path("/var/log/mirror/packages"),
    Path("/var/www/mirror"),
]
_DEFAULT_DIRECTORY_MODE = 0o755
_RUN_DIRECTORY_MODE = 0o700

_CONFIG_PATH = Path("/etc/mirror/config.json")
_SYSTEMD_PATH = Path("/etc/systemd/system")

_MIRROR_SERVICE = """[Unit]
Description=Mirror Daemon
Wants=mirror-worker.service
After=network.target mirror-worker.service

[Service]
ExecStart=mirror daemon --config /etc/mirror/config.json
Restart=always
User=root
Group=root

[Install]
WantedBy=multi-user.target
"""

_MIRROR_WORKER_SERVICE = """[Unit]
Description=Mirror Worker
After=network.target

[Service]
ExecStart=mirror worker --config /etc/mirror/config.json
Restart=always
User=root
Group=root

[Install]
WantedBy=multi-user.target
"""


def _ensure_root_and_linux() -> bool:
    """Check that the process is running on Linux as root."""
    if platform.system() != "Linux":
        print_formatted_text("This command can only be run on Linux.")
        return False
    if os.geteuid() != 0:
        print_formatted_text("This command must be run as root.")
        return False
    return True


def _check_required_binaries() -> bool:
    """Verify required binaries are available and warn about optional ones."""
    missing = [b for b in _REQUIRED_BINARIES if not command_exists(b)]
    if missing:
        for binary in missing:
            print_formatted_text(f"Missing required binary: {binary}")
        print_formatted_text("Setup aborted. Install missing binaries and re-run.")
        return False

    for binary in _OPTIONAL_BINARIES:
        if not command_exists(binary):
            print_formatted_text(
                f"Warning: optional binary '{binary}' not found. "
                "ftpsync's git-based update path is unavailable, "
                "but the bundled fallback will be used instead."
            )

    return True


def _ensure_directories() -> None:
    """Create all required mirror directories idempotently."""
    for directory in _DIRECTORIES:
        mode = _RUN_DIRECTORY_MODE if directory.parts[-3:] == ("var", "run", "mirror") else _DEFAULT_DIRECTORY_MODE
        directory.mkdir(parents=True, mode=mode, exist_ok=True)
        directory.chmod(mode)


def _write_default_config_if_absent() -> bool:
    """Write the default config only when no config file exists yet.

    Return:
        written(bool): True if the config was written, False if it already existed.
    """
    if _CONFIG_PATH.exists():
        print_formatted_text(
            "Existing /etc/mirror/config.json detected — skipping config write."
        )
        return False
    _CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=4))
    return True


def _write_systemd_units() -> None:
    """Write mirror.service and mirror-worker.service unit files."""
    (_SYSTEMD_PATH / "mirror.service").write_text(_MIRROR_SERVICE)
    (_SYSTEMD_PATH / "mirror-worker.service").write_text(_MIRROR_WORKER_SERVICE)


def _reload_systemd() -> None:
    """Reload the systemd daemon to pick up new unit files."""
    try:
        result = subprocess.run(
            ["systemctl", "daemon-reload"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print_formatted_text(
            "Warning: 'systemctl' not found. Skipping daemon-reload — "
            "run it manually on a systemd host before starting the services."
        )
        return
    if result.returncode != 0:
        stderr_snippet = result.stderr[:200]
        print_formatted_text(
            f"Warning: systemctl daemon-reload failed: {stderr_snippet}. "
            "Run 'systemctl daemon-reload' manually before starting the services."
        )


def _print_next_steps(config_written: bool) -> None:
    """Print post-setup instructions for the operator."""
    if config_written:
        print_formatted_text(
            "Edit /etc/mirror/config.json — replace empty 'packages' "
            "with your real entries — before starting the services."
        )
    else:
        print_formatted_text(
            "Existing config preserved. Directories and systemd units are in place."
        )
    print_formatted_text(
        "Then run 'systemctl enable --now mirror.service mirror-worker.service' "
        "to enable and start the daemon."
    )


def setup() -> None:
    """Provision directories, systemd units, and verify daemon prerequisites."""
    if not _ensure_root_and_linux():
        return
    if not _check_required_binaries():
        return
    try:
        _ensure_directories()
        config_written = _write_default_config_if_absent()
        _write_systemd_units()
        _reload_systemd()
        _print_next_steps(config_written)
    except Exception as e:
        print_formatted_text(f"An error occurred during setup: {e}")
