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

def setup(path: Path, package: mirror.structure.Package) -> None:
    """Prepare the sync environment (no-op for rsync)."""
    pass

def execute(package: mirror.structure.Package, pkg_logger: logging.Logger) -> None:
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
        
        # 2. FFTS Check
        if ffts_val:
            if not check_ffts_update(package, pkg_logger):
                pkg_logger.info("FFTS check: Up to date. Skipping sync.")
                mirror.sync.on_sync_done(package.pkgid, success=True, returncode=0)
                return

        # 3. Prepare command and env
        command, env = rsync(pkg_logger, package.pkgid, src, dst, user, password)

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
            commandline=command,
            env=env,
            uid=os.getuid(),
            gid=os.getgid(),
            log_path=logpath,
        )

    except AttributeError as e:
        pkg_logger.error(f"Sync for {package.pkgid} failed: value not found")
        pkg_logger.error(e)
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)
    except Exception as e:
        pkg_logger.error(f"Sync for {package.pkgid} failed: {e}")
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)

def rsync(logger: logging.Logger, pkgid: str, src: str, dst: Path, user: str, password: str) -> tuple[list[str], dict[str, str]]:
    """Build the rsync command list and environment dictionary.

    Args:
        logger(logging.Logger): Logger for this sync session.
        pkgid(str): Package identifier.
        src(str): Source URL or path.
        dst(Path): Destination directory.
        user(str): Rsync username (empty string if not required).
        password(str): Rsync password (empty string if not required).

    Return:
        result(tuple[list[str], dict[str, str]]): Command argument list and environment dict.
    """
    command = [
        "rsync",
        "-vrlptDSH",
        "--exclude=*.~tmp~",
        "--delete-delay",
        "--delay-updates",
        f"{src}/",
        f"{dst}/",
    ]

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
        timeout = 10

        user = str(package.settings.options.get("user", ""))
        password = str(package.settings.options.get("password", ""))

        command = [
            "rsync",
            "--no-motd",
            "--dry-run",
            "--out-format=%n",
            f"--contimeout={timeout}",
            f"{src}/{fftsfile}",
            f"{dst}/{fftsfile}",
        ]

        env = os.environ.copy()
        if user:
            env["USER"] = user
            env["RSYNC_PASSWORD"] = password

        pkg_logger.info(f"Executing FFTS check: {' '.join(command)}")
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        
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
