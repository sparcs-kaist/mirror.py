import sys
import json
import time
import signal
import logging
from pathlib import Path

import mirror
import mirror.socket


def worker(config, socket_path=None):
    """
    Runs the mirror worker.
    'config' is the path to the main JSON configuration file.
    """
    # Load config to get log level if provided
    log_level = logging.INFO
    if config:
        config_path = Path(config)
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    cfg = json.load(f)
                    level_str = cfg.get("settings", {}).get("logger", {}).get("level", "INFO")
                    log_level = getattr(logging, level_str.upper(), logging.INFO)
            except Exception as e:
                print(f"Warning: Failed to load config from {config}: {e}")

    # Configure basic logging for the worker
    logging.basicConfig(
        level=log_level,
        format="[%(asctime)s] %(levelname)s # %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    mirror.log = logging.getLogger("mirror")
    mirror.log.info("Worker started.")

    # Use unified init for worker server
    server = mirror.socket.init("worker", socket_path=socket_path)

    mirror.log.info(f"Worker listening on {server.socket_path}")

    def signal_handler(sig, frame):
        mirror.log.info("Worker stopping...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        while True:
            time.sleep(1)
    except Exception as e:
        mirror.log.error(f"Worker failed: {e}")
        server.stop()
        sys.exit(1)
