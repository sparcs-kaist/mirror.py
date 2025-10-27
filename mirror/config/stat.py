import mirror

from pathlib import Path

DEFAULT_STAT_DATA = {
    "mirrorname": "My Mirror",
    "settings": {
        "logfolder": "/mirror/logs",
        "webroot": "/var/www/mirror",
        "gid": 1000,
        "uid": 1000,
        "localtimezone": "Asia/Seoul",
        "logger": {
            "level": "INFO",
            "packagelevel": "ERROR",
            "format": "[%(asctime)s] %(levelname)s # %(message)s",
            "packageformat": "[%(asctime)s][{package}] %(levelname)s # %(message)s",

            "fileformat": {
                "base": "/mirror/logs",
                "folder": "{year}/{month}/{day}",
                "filename": "{packageid}-{hour}:{minute}:{second}.{microsecond}.log",
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
            "name": "Name Mirror",
            "id": "mirror",
            "href": "/mirror",
            "synctype": "rsync",
            "syncrate": "PT1H",
            "src": "rsync://test.org/mirror",
            "link": [
                {
                    "rel": "HOME",
                    "href": "http://www.example.com"
                },
            ],
            "status": { # This is the status when error
                "status": "ERROR",
                "statusinfo": {
                    "lasterrorlog": "2024/01/01/mirror-00:00:00.000000.log.gz",
                    "lastsuccesslog": "2024/01/01/mirror-00:00:00.000000.log.gz",
                    "runninglog": None,
                    "errorcount": 1
                }
            }
        },
        "geoul": {
            "name": "KAI's Mirror",
            "id": "geoul",
            "href": "/geoul",
            "synctype": "rsync",
            "syncrate": "PT1H",
            "src": "rsync://test.org/geoul",
            "link": [
                {
                    "rel": "HOME",
                    "href": "http://www.example.com"
                },
                {
                    "rel": "Tracker",
                    "href": "http://www.example.com/tracker"
                },
            ],
            "status": {
                "status": "SYNC",
                "statusinfo": {
                    "lasterrorlog": None,
                    "lastsuccesslog": "2024/01/01/geoul-00:00:00.000000.log.gz",
                    "runninglog": "2024/01/01/geoul-00:00:00.000000.log",
                    "errorcount": 0
                }
            }
        }
    }
}
