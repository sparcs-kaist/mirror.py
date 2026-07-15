"""apt-mirror sync method.

Delegates to the PyPI ``apt-mirror`` tool (https://pypi.org/project/apt-mirror/),
a Python reimplementation of apt-mirror. The tool is config-file driven: the
repositories to fetch and the destination (``base_path``) live in the
apt-mirror config file (default ``/etc/apt/mirror.list``), so ``settings.src``
and ``settings.dst`` are not used here, mirroring the bandersnatch method.

The optional ``configfile`` package option selects an alternate config file.
"""
import logging
from pathlib import Path

import mirror
import mirror.structure
import mirror.socket.worker
import mirror.sync


_DEFAULT_CONFIGFILE = "/etc/apt/mirror.list"


def _has_control_char(value: str) -> bool:
    """Return True if the string contains an ASCII control character."""
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _validate_configfile(value: object) -> str:
    """Validate the apt-mirror config file path option.

    Args:
        value(object): Caller-supplied ``configfile`` option value.

    Return:
        configfile(str): The validated path unchanged.

    Raises:
        ValueError: value is not a string, is empty, contains NUL or control
            characters, or starts with '-' (which apt-mirror would parse as an
            option rather than a path).
    """
    if not isinstance(value, str):
        raise ValueError("Invalid apt-mirror configfile: must be a string")
    if not value:
        raise ValueError("Invalid apt-mirror configfile: must not be empty")
    if "\x00" in value or _has_control_char(value):
        raise ValueError("Invalid apt-mirror configfile: contains control characters")
    if value.startswith("-"):
        raise ValueError("Invalid apt-mirror configfile: must not start with '-'")
    Path(value)
    return value


def _build_command(options: dict) -> list[str]:
    """Build the apt-mirror command list from package options.

    Args:
        options(dict): Package ``settings.options`` mapping.

    Return:
        command(list[str]): apt-mirror argument list.
    """
    configfile = _validate_configfile(options.get("configfile", _DEFAULT_CONFIGFILE))
    return ["apt-mirror", configfile]


def execute(package: mirror.structure.Package, pkg_logger: logging.Logger, trigger: str = "auto") -> None:
    """Run apt-mirror sync for the given package.

    Args:
        package(mirror.structure.Package): Package to sync.
        pkg_logger(logging.Logger): Logger for this sync session.
        trigger(str): Source of the trigger ("auto", "manual", etc.).
    """
    pkg_logger.info(f"Starting sync.apt_mirror for {package.name}")

    try:
        command = _build_command(package.settings.options)

        log_path = None
        for handler in pkg_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                log_path = handler.baseFilename
                break

        pkg_logger.info(f"Delegating apt-mirror sync to worker: {' '.join(command)}")
        mirror.socket.worker.execute_command(
            job_id=package.pkgid,
            sync_method="apt-mirror",
            commandline=command,
            env={},
            uid=mirror.conf.uid,
            gid=mirror.conf.gid,
            log_path=log_path,
        )

    except Exception as e:
        pkg_logger.error(f"apt-mirror sync for {package.pkgid} failed: {e}")
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)


def plugin():
    """Entry-point factory for the apt-mirror plug-in.

    Return:
        record(mirror.plugin.PluginRecord): Sync plug-in record exposing execute.
    """
    from mirror.plugin import sync_plugin
    return sync_plugin(name="apt-mirror", execute=execute)
