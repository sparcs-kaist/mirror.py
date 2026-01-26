import mirror
import mirror.config
import mirror.logger
import mirror.sync
from mirror.socket.master import MasterServer

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

    # Start Master Server socket
    socket_server = MasterServer()
    socket_server.set_version(mirror.__version__)
    socket_server.start()

    mirror.log.info(f"Master Daemon listening on {socket_server.socket_path}")
    mirror.log.info("Daemon started and configuration loaded.")

    def signal_handler(sig, frame):
        mirror.log.info("Master Daemon stopping...")
        socket_server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        while True:
            for package in mirror.packages.values():
                if package.is_disabled(): continue
                if package.is_syncing(): continue

                if time.time() - package.lastsync > package.syncrate:
                    package.set_status("SYNC")
                    mirror.sync.start(package)
            
            # Prevent CPU 100% usage
            time.sleep(1)
    except Exception as e:
        mirror.log.error(f"Daemon failed: {e}")
        socket_server.stop()
        sys.exit(1)
