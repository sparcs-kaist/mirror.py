import mirror
import mirror.config

import time
import json
from pathlib import Path

def daemon(config):
    daemonConfigPath = Path("/etc/mirror/daemon.json")
    if not daemonConfigPath.exists():
        raise FileNotFoundError(f"{daemonConfigPath} does not exist! Please initialize the mirror first.")
    
    daemonConfig = json.loads(daemonConfigPath.read_text())
    
    mirror.config.CONFIG_PATH = daemonConfig["config"]
    mirror.config.DATA_PATH = daemonConfig["data"]
    mirror.config.STATUS_PATH = daemonConfig["status"]

    mirror.config.load_config(configPath)

    pass


def check_daemon():
    pass
