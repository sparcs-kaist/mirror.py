import mirror
import mirror.config
import mirror.logger
import mirror.sync
import mirror.socket
import mirror.socket.worker
import mirror.event

import time
import signal
import sys
from pathlib import Path

def daemon(config):
    """
    Runs the mirror daemon.
    'config' is the path to the main JSON configuration file.
    """
    # Load all configurations from the single config file path.
    mirror.config.load(Path(config))
    mirror.logger.setup_logger()

    # Fire initialization complete event
    mirror.event.post_event("MASTER.INIT.PRE", wait=True)

    # Start Master Server socket
    socket_server = mirror.socket.init("master")

    mirror.log.info(f"Master Daemon listening on {socket_server.socket_path}")
    mirror.log.info("Daemon started and configuration loaded.")

    if mirror.socket.worker.is_worker_running("master"): # Or some general check
        mirror.log.info("Worker server is running and reachable.")
    else:
        mirror.log.error("Worker server is NOT running. Sync operations may fail if they rely on it.")

    def signal_handler(sig, frame):
        mirror.log.info("Master Daemon stopping...")
        socket_server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    mirror.event.post_event("MASTER.INIT.POST")
    
    try:
        while True:
            for package in mirror.packages.values():
                if package.is_disabled():
                    mirror.log.debug(f"Package {package.pkgid} is disabled. Skipping.")
                    continue
                
                if package.is_syncing():
                    if mirror.socket.worker.is_worker_running(package.pkgid):
                        continue
                    elif time.time() - package.lastsync < 60: # Because of ffts check time
                        continue
                    else:
                        mirror.log.warning(f"Package {package.pkgid} marked as syncing but no worker found.")
                        package.set_status("ERROR")
                elif mirror.socket.worker.is_worker_running(package.pkgid):
                    mirror.log.error(f"Package is synging while status is {package.status}. Changed the status to syncing.")
                    package.set_status("SYNC")
                    
                    continue

                if time.time() - package.lastsync > package.syncrate:
                    mirror.log.info(f"Package {package.pkgid} requires sync (Last sync: {package.lastsync}, Rate: {package.syncrate})")
                    mirror.sync.start(package)
                elif package.status == "ERROR" and time.time() - mirror.conf.errorcontinuetime > package.lastsync:
                    mirror.log.info(f"Package {package.pkgid} is in {package.status} state. Retrying sync.")
                    mirror.sync.start(package)
            
            time.sleep(1)
    except Exception as e:
        mirror.log.error(f"Daemon failed: {e}")
        socket_server.stop()
        sys.exit(1)
