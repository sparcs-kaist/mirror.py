import mirror
import mirror.structure
import mirror.socket.worker
import mirror.sync
import mirror.toolbox
import os
import time
import logging
import subprocess
from pathlib import Path

_DEFAULT_RSYNC_FLAGS = "vrltDSH"
_SAFE_RSYNC_FLAGS = "vrlptDSHaznhPxWENcimub"


def _validate_flag_option(value, name: str, whitelist: str | None = None) -> str:
    """Validate an rsync flag option string (option_exclude or option_include).

    Args:
        value: Value to validate (must be str).
        name(str): Option name used in error messages.
        whitelist(str | None): If provided, each char must appear in this set.

    Return:
        value(str): The validated value unchanged.
    """
    if not isinstance(value, str):
        raise ValueError(f"Invalid rsync {name}: must be a string")
    for c in value:
        if c == "-" or c.isspace() or ord(c) < 32 or ord(c) == 127:
            raise ValueError(f"Invalid rsync {name}: disallowed character {c!r}")
        if whitelist is not None and c not in whitelist:
            raise ValueError(f"Invalid rsync {name}: unsupported flag {c!r}")
    return value


def _validate_excludes(value) -> list[str]:
    """Validate a list of rsync --exclude patterns.

    Args:
        value: Value to validate (must be list of str with no control characters).

    Return:
        excludes(list[str]): Copy of the validated list.
    """
    if not isinstance(value, list):
        raise ValueError("Invalid rsync exclude option: must be a list")
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Invalid rsync exclude option: each item must be a string")
        if any(ord(c) < 32 or ord(c) == 127 for c in item):
            raise ValueError("Invalid rsync exclude option: item contains control characters")
    return list(value)


def setup(path: Path, package: mirror.structure.Package) -> None:
    """Prepare the sync environment (no-op for rsync)."""
    pass

def execute(package: mirror.structure.Package, pkg_logger: logging.Logger, trigger: str = "auto") -> None:
    """Run rsync sync for the given package.

    Args:
        package(mirror.structure.Package): Package to sync.
        pkg_logger(logging.Logger): Logger for this sync session.
    """
    # Set status to SYNC as soon as we enter execute
    pkg_logger.info(f"Starting sync.rsync for {package.name}")

    try:
        # 1. Get settings
        src = package.settings.src
        dst = Path(package.settings.dst)
        ffts_val = package.settings.options.get("ffts", False)

        user = str(package.settings.options.get("user", ""))
        password = str(package.settings.options.get("password", ""))
        option_exclude = package.settings.options.get("option_exclude", "")
        option_include = package.settings.options.get("option_include", "")
        excludes = package.settings.options.get("exclude", [])

        # 2. FFTS Check
        if ffts_val:
            if not check_ffts_update(package, pkg_logger):
                pkg_logger.info("FFTS check: Up to date. Skipping sync.")
                mirror.sync.on_sync_done(package.pkgid, success=True, returncode=0)
                return

        # 3. Prepare command and env
        command, env = rsync(
            pkg_logger, package.pkgid, src, dst, user, password,
            option_exclude=option_exclude,
            option_include=option_include,
            excludes=excludes,
        )

        # 4. Execute sync directly
        pkg_logger.info(f"+ src={src}")
        pkg_logger.info(f"+ frequency={mirror.toolbox.format_iso_duration(package.syncrate)}")
        pkg_logger.info(f"+ lastupdate={time.ctime(package.lastsync)}")
        pkg_logger.info(f"Running rsync: {' '.join(command)}")

        logpath = None
        for handler in pkg_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                logpath = Path(handler.baseFilename)
                break

        mirror.socket.worker.execute_command(
            job_id=package.pkgid,
            sync_method="rsync",
            commandline=command,
            env=env,
            uid=mirror.conf.uid,
            gid=mirror.conf.gid,
            log_path=logpath,
        )

    except AttributeError as e:
        pkg_logger.error(f"Sync for {package.pkgid} failed: value not found")
        pkg_logger.error(e)
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)
    except Exception as e:
        pkg_logger.error(f"Sync for {package.pkgid} failed: {e}")
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)

def rsync(
    logger: logging.Logger,
    pkgid: str,
    src: str,
    dst: Path,
    user: str,
    password: str,
    option_exclude: str = "",
    option_include: str = "",
    excludes: list[str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Build the rsync command list and environment dictionary.

    Args:
        logger(logging.Logger): Logger for this sync session.
        pkgid(str): Package identifier.
        src(str): Source URL or path.
        dst(Path): Destination directory.
        user(str): Rsync username (empty string if not required).
        password(str): Rsync password (empty string if not required).
        option_exclude(str): Characters to remove from the default flag string.
        option_include(str): Characters to append to the default flag string (whitelisted).
        excludes(list[str] | None): Additional --exclude patterns to append.

    Return:
        result(tuple[list[str], dict[str, str]]): Command argument list and environment dict.
    """
    option_exclude = _validate_flag_option(option_exclude, "option_exclude")
    option_include = _validate_flag_option(option_include, "option_include", whitelist=_SAFE_RSYNC_FLAGS)
    excludes = _validate_excludes(excludes if excludes is not None else [])

    # Build flags: start from default, append include chars not already present,
    # then remove exclude chars.
    flags = _DEFAULT_RSYNC_FLAGS
    for c in option_include:
        if c not in flags:
            flags += c
    flags = "".join(c for c in flags if c not in option_exclude)

    command = ["rsync"]
    if flags:
        command.append(f"-{flags}")
    command.append("--partial")
    command.append("--exclude=*.~tmp~")
    for pattern in excludes:
        command.append(f"--exclude={pattern}")
    command.extend([
        "--delete-delay",
        "--delay-updates",
        f"{src}/",
        f"{dst}/",
    ])

    env = {}
    if user:
        env["USER"] = user
        env["RSYNC_PASSWORD"] = password

    return command, env


def check_ffts_update(package: mirror.structure.Package, pkg_logger: logging.Logger) -> bool:
    """Check if the mirror needs an update via a dry-run rsync (FFTS method).

    Args:
        package(mirror.structure.Package): Package to check.
        pkg_logger(logging.Logger): Logger for this sync session.

    Return:
        needs_update(bool): True if an update is needed or check failed, False if up to date.
    """
    pkg_logger.info(f"Running FFTS check for {package.name}")

    try:
        src = package.settings.src
        dst = Path(package.settings.dst)
        fftsfile = package.settings.options.get("fftsfile", "")
        connection_timeout = 10
        process_timeout = 60

        user = str(package.settings.options.get("user", ""))
        password = str(package.settings.options.get("password", ""))

        command = [
            "rsync",
            "--no-motd",
            "--dry-run",
            "--out-format=%n",
            f"--contimeout={connection_timeout}",
            f"{src}/{fftsfile}",
            f"{dst}/{fftsfile}",
        ]

        env = os.environ.copy()
        if user:
            env["USER"] = user
            env["RSYNC_PASSWORD"] = password

        pkg_logger.info(f"Executing FFTS check: {' '.join(command)}")
        result = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            timeout=process_timeout,
        )

        if result.returncode == 0:
            if result.stdout.strip():
                pkg_logger.info("FFTS check: Update needed.")
                return True
            else:
                pkg_logger.info("FFTS check: Up to date.")
                return False
        else:
            pkg_logger.warning(f"FFTS check failed with return code {result.returncode}: {result.stderr}")
            # Assume update needed on error to avoid skipping a required sync
            return True

    except Exception as e:
        pkg_logger.error(f"FFTS check for {package.pkgid} failed: {e}")
        # Assume update needed on error to avoid skipping a required sync
        return True


def plugin():
    """Entry-point factory for the rsync plug-in.

    Return:
        record(mirror.plugin.PluginRecord): Sync plug-in record exposing execute and on_sync_done.
    """
    from mirror.plugin import sync_plugin
    return sync_plugin(name="rsync", execute=execute)
