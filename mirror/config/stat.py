DEFAULT_STAT_DATA = {
    "packages": {
        "mirror": {
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
