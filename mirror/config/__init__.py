import mirror
import mirror.structure

import mirror.config.config
import mirror.config.stat
import mirror.config.status



from pathlib import Path
import json

DEFAULT_DAEMON_CONFIG = {
    "config": "/etc/mirror/config.json",
    "plugin_config": "/etc/mirror/plugin_config.json",
    "data": "/etc/mirror/stat_data.json",
    "status": "/etc/mirror/status.json",
}

CONFIG_PATH: Path
STAT_DATA_PATH: Path
STATUS_PATH: Path


def load(confPath: Path | None):
    """
    Load the configuration and status data.
    """
    if not confPath or not confPath.exists():
        raise FileNotFoundError(f"Configuration path {confPath} does not exist!")
    
    global CONFIG_PATH
    CONFIG_PATH = confPath

    _load_config()



    
def reload():
    config = json.loads(CONFIG_PATH.read_text())

def _load_config():
    """
    Load Configuration file

    Args: confPath (Path)

    Return: None

    Registration: 
        mirror.conf

    """

    confPath = mirror.config.CONFIG_PATH
    config: dict = json.loads(confPath.read_text())

    if not config.get("stat_data", ""):
        raise ValueError("Configuration file does not contain 'stat_data' key.")
     
    # Need to load STAT_DATA_PATH First in config file.
    mirror.config.STAT_DATA_PATH = Path(config.get("stat_data", ""))


    if not mirror.config.STAT_DATA_PATH.exists():
        for package in config["packages"]:
            package["status"] = "ERROR"
        mirror.config.STAT_DATA_PATH.write_text(json.dumps(config["packages"]))
    status = json.loads(mirror.config.STAT_DATA_PATH.read_text())

    conflist = list(config["packages"].keys())
    statuslist = list(status["packages"].keys())
    for package in conflist:
        if package in statuslist:
            config["packages"][package]["status"] = status["packages"][package]["status"]
            statuslist.remove(package)
        else:
            config["packages"][package]["status"] = "ERROR"
    
    if statuslist:
        mirror.logger.warning(f"Status file has extra packages: {statuslist}. You might need to delete manually.")
    
    mirror.config.STAT_DATA_PATH.write_text(json.dumps(config))
    
    # Load config
    mirror.conf = mirror.structure.Config.load_from_dict(config)
    mirror.packages = mirror.structure.Packages(config["packages"])
