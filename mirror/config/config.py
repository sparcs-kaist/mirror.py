DEFAULT_CONFIG = {
    "mirrorname": "My Mirror",
    "hostname": "ftp.kaist.ac.kr",
    "settings": {
        "logfolder": "/var/log/mirror/ftpsync",
        "webroot": "/var/www/mirror",
        "statusfile": "/var/www/mirror/status.json",
        "statfile": "/var/lib/mirror/stat.json",
        "localtimezone": "Asia/Seoul",
        "errorcontinuetime": 60,
        "maintainer": {
            "name": "Roul",
            "email": "op@ftp.kaist.ac.kr"
        },
        "logger": {
            "level": "INFO",
            "packagelevel": "INFO",
            "format": "[%(asctime)s] %(levelname)s # %(message)s",
            "packageformat": "[%(asctime)s][{package}] %(levelname)s # %(message)s",

            "fileformat": {
                "base": "/var/log/mirror",
                "folder": "{year}/{month}",
                "filename": "{year}-{month}-{day}.log",
                "gzip": True
            },
            
            "packagefileformat": {
                "base": "/var/log/mirror/packages",
                "folder": "{year}/{month}/{day}",
                "filename": "{packageid}.{hour}:{minute}:{second}.{microsecond}.log",
                "gzip": True
            }
        },
        "ftpsync": {
            "maintainer": "Admins <admins@example.com>", # only ftpsync
            "sponsor": "Example <https://example.com>", # only ftpsync
            "country": "KR", # only ftpsync
            "location": "Seoul", # only ftpsync
            "throughput": "1G", # only ftpsync
        }
    },
    "packages": {}
}
