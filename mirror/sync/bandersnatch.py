import mirror
import mirror.structure
import mirror.socket.worker
import mirror.logger
import os
import logging
from pathlib import Path

module = "sync"
name = "bandersnatch"
_LOAD = False


def execute(package: mirror.structure.Package, pkg_logger: logging.Logger):
    """Sync package using bandersnatch

    Args:
        package(mirror.structure.Package): Package object
        pkg_logger(logging.Logger): Logger object for this sync session
    """
    pkg_logger.info(f"Starting {module}.{name} for {package.name}")

    try:
        command = ["bandersnatch", "mirror"]

        log_path = None
        for handler in pkg_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                log_path = handler.baseFilename
                break

        pkg_logger.info(f"Delegating bandersnatch sync to worker")
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
        pkg_logger.error(f"bandersnatch sync for {package.pkgid} failed: {e}")
        mirror.logger.close_logger(pkg_logger)
        package.set_status("ERROR")