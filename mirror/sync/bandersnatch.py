import mirror
import mirror.structure
import mirror.socket.worker
import mirror.sync
import logging
from pathlib import Path


def execute(package: mirror.structure.Package, pkg_logger: logging.Logger):
    """Sync package using bandersnatch

    Args:
        package(mirror.structure.Package): Package object
        pkg_logger(logging.Logger): Logger object for this sync session
    """
    pkg_logger.info(f"Starting sync.bandersnatch for {package.name}")

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
            sync_method="bandersnatch",
            commandline=command,
            env={},
            uid=mirror.conf.uid,
            gid=mirror.conf.gid,
            log_path=log_path,
        )

    except Exception as e:
        pkg_logger.error(f"bandersnatch sync for {package.pkgid} failed: {e}")
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)


def plugin():
    """Entry-point factory for the bandersnatch plug-in."""
    from mirror.plugin import sync_plugin
    return sync_plugin(name="bandersnatch", execute=execute)
