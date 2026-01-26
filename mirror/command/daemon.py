import mirror
import mirror.config
import mirror.logger
import mirror.sync
from mirror.socket.master import MasterServer
from mirror.socket.worker import WorkerClient

import time
import signal
import sys
from pathlib import Path

def check_worker_running(log_error=True) -> bool:
    """Check if the worker server is running."""
    # TODO: Get socket path from config if it's configurable
    socket_path = Path("/run/mirror/worker.sock")
    try:
        with WorkerClient(socket_path) as client:
            client.ping()
            return True
    except Exception as e:
        if log_error:
            mirror.log.warning(f"Worker server is not running or not reachable at {socket_path}: {e}")
        return False

def daemon(config):
    """
    Runs the mirror daemon.
    'config' is the path to the main JSON configuration file.
    """
    # Load all configurations from the single config file path.
    mirror.config.load(Path(config))
    mirror.logger.setup_logger()

    # Start Master Server socket
    socket_server = MasterServer()
    socket_server.set_version(mirror.__version__)
    socket_server.start()

    mirror.log.info(f"Master Daemon listening on {socket_server.socket_path}")
    mirror.log.info("Daemon started and configuration loaded.")

    # Check Worker Status
    if check_worker_running():
        mirror.log.info("Worker server is running and reachable.")
    else:
        mirror.log.error("Worker server is NOT running. Sync operations may fail if they rely on it.")

    def signal_handler(sig, frame):
        mirror.log.info("Master Daemon stopping...")
        socket_server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Track packages that were recently syncing to detect completion
    syncing_packages = set()

    try:
        while True:
            for package in mirror.packages.values():
                if package.is_disabled():
                    continue
                
                if package.is_syncing():
                    if package.pkgid not in syncing_packages:
                        mirror.log.info(f"Package {package.pkgid} is now syncing...")
                        syncing_packages.add(package.pkgid)
                    continue
                
                # If it was syncing but now it's not, it finished
                if package.pkgid in syncing_packages:
                    mirror.log.info(f"Package {package.pkgid} sync finished. Status: {package.status}")
                    syncing_packages.remove(package.pkgid)

                if time.time() - package.lastsync > package.syncrate:
                    mirror.log.info(f"Package {package.pkgid} requires sync (Last sync: {package.lastsync}, Rate: {package.syncrate})")
                    
                    package.set_status("SYNC")
                    mirror.sync.start(package)
            
            # Prevent CPU 100% usage
            time.sleep(1)
    except Exception as e:
        mirror.log.error(f"Daemon failed: {e}")
        socket_server.stop()
        sys.exit(1)
