import mirror
import mirror.structure
import mirror.socket.worker
import mirror.logger
import os
import logging
from pathlib import Path

module = "sync"
name = "lftp"
_LOAD = False


def execute(package: mirror.structure.Package, pkg_logger: logging.Logger):
    """Run the lftp Sync method (CORE)

    Args:
        package(mirror.structure.Package): Package object
        pkg_logger(logging.Logger): Logger object for this sync session
    """
    pkg_logger.info(f"Starting {module}.{name} for {package.name}")

    try:
        src = package.settings.src
        dst = package.settings.dst

        lftp_script = (
            f"set ftp:anon-pass mirror@{src}; "
            f"set cmd:verbose yes; "
            r"mirror --continue --delete --no-perms --verbose=3 "
            r"-X '\.(mirror|notar)' -x '\.in\..*\.' -X 'lost+found' "
            f"ftp://{src} {dst}"
        )

        command = ["lftp", "-c", lftp_script]

        log_path = None
        for handler in pkg_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                log_path = handler.baseFilename
                break

        pkg_logger.info(f"Delegating lftp sync to worker: {' '.join(command)}")
        mirror.socket.worker.execute_command(
            job_id=package.pkgid,
            sync_method=name,
            commandline=command,
            env={},
            uid=os.getuid(),
            gid=os.getgid(),
            log_path=log_path,
        )

    except Exception as e:
        pkg_logger.error(f"lftp sync for {package.pkgid} failed: {e}")
        mirror.logger.close_logger(pkg_logger)
        package.set_status("ERROR")