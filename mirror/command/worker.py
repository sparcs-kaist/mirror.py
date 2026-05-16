import sys
import json
import signal
import logging
from pathlib import Path
from typing import Optional

import mirror
import mirror.config
import mirror.socket
import mirror.worker


def worker(config: str, socket_path: Optional[str] = None) -> None:
    """Run the mirror worker server.

    Args:
        config(str): Path to the main JSON configuration file.
        socket_path(str, optional): Override path for the worker Unix socket.
    """
    log_level = logging.INFO
    configured_socket_path = None
    if config:
        config_path = Path(config)
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    cfg = json.load(f)
                    settings = cfg.get("settings", {})
                    level_str = settings.get("logger", {}).get("level", "INFO")
                    log_level = getattr(logging, level_str.upper(), logging.INFO)
                    configured_socket_path = settings.get("socket_path")
            except Exception as e:
                logging.warning(f"Failed to load config from {config}: {e}")

    logging.basicConfig(
        level=log_level,
        format="[%(asctime)s] %(levelname)s # %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    mirror.log = logging.getLogger("mirror")
    mirror.log.info("Worker started.")

    if socket_path is None and isinstance(configured_socket_path, str) and configured_socket_path:
        mirror.config.SOCKET_PATH = configured_socket_path

    server = mirror.socket.init("worker", socket_path=socket_path)

    mirror.log.info(f"Worker listening on {server.socket_path}")

    def signal_handler(sig, frame):
        mirror.log.info("Worker stopping...")
        mirror.exit = True
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        mirror.worker.manage()
    except Exception as e:
        mirror.log.error(f"Worker failed: {e}")
        server.stop()
        sys.exit(1)
