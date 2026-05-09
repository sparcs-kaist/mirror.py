import mirror
import mirror.config
import mirror.logger
import mirror.sync
import mirror.socket
import mirror.socket.worker
import mirror.event
import mirror.structure

import time
import signal
import sys
import os
from pathlib import Path


def should_auto_sync(package: mirror.structure.Package, now: float, errorcontinuetime: int) -> bool:
    """Decide whether the daemon auto-loop should start a sync for this package.

    Args:
        package(Package): Package to evaluate.
        now(float): Current epoch seconds (injected for testability).
        errorcontinuetime(int): Seconds to wait before retrying after an ERROR.

    Return:
        should_sync(bool): True if the auto-loop should call sync.start(package).
    """
    if package.syncrate < 0:
        return False
    if now - package.lastsync > package.syncrate:
        return True
    if package.status == "ERROR" and now - package.lastsync > errorcontinuetime:
        return True
    return False


def daemon(config: str) -> None:
    """Run the mirror master daemon.

    Args:
        config(str): Path to the main JSON configuration file.
    """
    # Load all configurations from the single config file path.
    mirror.config.load(Path(config))
    mirror.logger.setup_logger()

    # Write PID file
    pid_file = mirror.RUN_PATH / "mirror.pid"
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()))
    except OSError as e:
        mirror.log.error(f"Failed to write PID file {pid_file}: {e}")
        sys.exit(1)

    # Fire initialization complete event
    mirror.event.post_event("MASTER.INIT.PRE", wait=True)

    # Start Master Server socket
    socket_server = mirror.socket.init("master")

    mirror.log.info(f"Master Daemon listening on {socket_server.socket_path}")
    mirror.log.info("Daemon started and configuration loaded.")

    if mirror.socket.worker.is_worker_running():
        mirror.log.info("Worker server is running and reachable.")
    else:
        mirror.log.error("Worker server is NOT running. Sync operations may fail if they rely on it.")

    def signal_handler(sig, frame):
        mirror.log.info("Master Daemon stopping...")
        mirror.socket.stop()
        if pid_file.exists():
            pid_file.unlink()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    mirror.event.post_event("MASTER.INIT.POST")
    
    try:
        while True:
            for package in mirror.packages.values():
                try:
                    if package.is_disabled():
                        mirror.log.debug(f"Package {package.pkgid} is disabled. Skipping.")
                        continue

                    if package.is_syncing():
                        # Source of truth for an active sync is the in-progress
                        # set in mirror.sync; the worker may not yet have been
                        # called (e.g. ftpsync setup is still running) so
                        # is_worker_running can return False even when the sync
                        # is healthy.
                        if package.pkgid in mirror.sync._in_progress:
                            continue
                        if mirror.socket.worker.is_worker_running(package.pkgid):
                            continue
                        mirror.log.warning(f"Package {package.pkgid} marked as syncing but no worker found.")
                        pkg_logger = mirror.logger.get(package.pkgid)
                        if pkg_logger.handlers:
                            mirror.logger.close_logger(pkg_logger)
                        package.set_status("ERROR")
                        continue
                    elif mirror.socket.worker.is_worker_running(package.pkgid):
                        mirror.log.error(f"Package is syncing while status is {package.status}. Changed the status to syncing.")
                        package.set_status("SYNC")
                        continue

                    if should_auto_sync(package, time.time(), mirror.conf.errorcontinuetime):
                        mirror.log.info(f"Package {package.pkgid} requires sync (last_sync={package.lastsync}, syncrate={package.syncrate}, status={package.status})")
                        mirror.sync.start(package)
                except Exception as e:
                    # A single package failing must not crash the whole daemon.
                    mirror.log.error(f"Package {package.pkgid} loop iteration failed: {e}")

            time.sleep(1)
    except Exception as e:
        mirror.log.error(f"Daemon failed: {e}")
        socket_server.stop()
        if pid_file.exists():
            pid_file.unlink()
        sys.exit(1)
