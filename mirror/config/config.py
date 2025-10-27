DEFAULT_CONFIG = {
    "mirrorname": "My Mirror",
    "settings": {
        "logfolder": "/mirror/logs",
        "webroot": "/var/www/mirror",
        "statfile": "/mirror/stat_data.json",
        "gid": 1000,
        "uid": 1000,
        "localtimezone": "Asia/Seoul",
        "logger": {
            "level": "INFO",
            "packagelevel": "ERROR",
            "format": "[%(asctime)s] %(levelname)s # %(message)s",
            "packageformat": "[%(asctime)s][{package}] %(levelname)s # %(message)s",

            "fileformat": {
                "base": "/var/log/mirror",
                "folder": "{year}/{month}/{day}",
                "filename": "{hour}:{minute}:{second}.{microsecond}.{packageid}.log",
                "gzip": True,
            }
        },
        "ftpsync": {
            "maintainer": "Admins <admins@examile.com>", # only ftpsync
            "sponsor": "Example <https://example.com>", # only ftpsync
            "country": "KR", # only ftpsync
            "location": "Seoul", # only ftpsync
            "throughput": "1G", # only ftpsync
            "include": "", # only ftpsync
            "exclude": "", # only ftpsync
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
            "link": [
                {
                    "rel": "HOME",
                    "href": "http://www.example.com"
                },
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

