import mirror

from pathlib import Path
import json

DEFAULT_DAEMON_CONFIG = {
    "config": "/etc/mirror/config.json",
    "data": "/etc/mirror/data.json",
    "status": "/etc/mirror/status.json",
}

DEFAULT_CONFIG = {
    "mirrorname": "My Mirror",
    "settings": {
        "logfolder": "/mirror/logs",
        "webroot": "/var/www/mirror",
        "gid": 1000,
        "uid": 1000,
        "localtimezone": "Asia/Seoul",
        "ftpsync": {
            "maintainer": "Admins <admins@examile.com>", # only ftpsync
            "sponsor": "Example <https://example.com>", # only ftpsync
            "country": "KR", # only ftpsync
            "location": "Seoul", # only ftpsync
            "throughput": "1G", # only ftpsync
            "include": "", # only ftpsync
            "exclude": "", # only ftpsync
        },
        "logger": {
            "level": "INFO",
            "packagelevel": "ERROR",
            "format": "[%(asctime)s] %(levelname)s # %(message)s",
            "packageformat": "[%(asctime)s][{package}] %(levelname)s # %(message)s",

            "fileformat": {
                "base": "/mirror/logs",
                "folder": "{year}/{month}/{day}",
                "filename": "{hour}:{minute}:{second}.{microsecond}.{packageid}.log",
                "gzip": True,
            }
        },
        "plugins": [
            "/mirror/plugin/someof.py",
            "/mirror/plugin/"
        ]
    },
    "packages": {
        "mirror": {
            "name": "onTDB Mirror",
            "id": "mirror",
            "href": "/mirror",
            "synctype": "rsync",
            "syncrate": "PT1H",
            "link": [
                {
                    "rel": "HOME",
                    "href": "http://www.ontdb.com"
                },
                {
                    "rel": "HTTP",
                    "href": "http://mirror.ontdb.com/mirror"
                },
                {
                    "rel": "HTTPS",
                    "href": "https://mirror.ontdb.com/mirror"
                }
            ],
            "settings": {
                "hidden": False,
                "src": "rsync://test.org/mirror", # ftp://test.org/mirror
                "dst": "/disk/mirror",
                "options": {
                    "ffts": True,
                    "fftsfile": "fullfiletimelist-mirror", # only FFTS
                }
            }
        }
    }
}

CONFIG_PATH: Path = None
DATA_PATH: Path = None
STATUS_PATH: Path = None

def load_config():
    """Load the configuration file"""

    if CONFIG_PATH == None or DATA_PATH == None or STATUS_PATH == None:
        raise Exception("CONFIG_PATH, DATA_PATH, STATUS_PATH is not set.")
        
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"{CONFIG_PATH} does not exist! Please initialize the mirror first.")
    config = json.loads(CONFIG_PATH.read_text())
    if not DATA_PATH.exists():
        for package in config["packages"]:
            package["status"] = "ERROR"
        DATA_PATH.write_text(json.dumps(config["packages"]))
    status = json.loads(DATA_PATH.read_text())

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
    
    DATA_PATH.write_text(json.dumps(config))
    




    mirror.settings = mirror.config.Settings(config)
    
def reload():
    config = json.loads(CONFIG_PATH.read_text())
