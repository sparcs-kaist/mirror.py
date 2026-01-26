import mirror.config

DEFAULT_STATUS = {
    "lastupdate": 17535432060580.473,
    "mirrorname": "KAIST FTP TEST",
    "lists": [
        "mirror",
        "linux",
        "almalinux",
        "openwrt",
        "rpmfusion",
        "tinycorelinux-tc"
    ],
    "mirror": {
        "name": "KAIST FTP TEST Mirror",
        "id": "mirror",
        "status": "ACTIVE",
        "synctype": None,
        "synctime": [],
        "syncrate": "",
        "syncurl": "https://test.ftp.kaist.ac.kr/",
        "href": "/mirror",
        "lastsync": 0,
        "links": [
            {
                "rel": "HOME",
                "href": "http://www.kaist.ac.kr"
            },
            {
                "rel": "HTTP",
                "href": "http://test.ftp.kaist.ac.kr/mirror"
            },
            {
                "rel": "HTTPS",
                "href": "https://test.ftp.kaist.ac.kr/mirror"
            }
        ]
    },
    "linux": {
        "name": "Linux Kernel",
        "id": "linux",
        "status": "ACTIVE",
        "synctype": "rsync-rhel",
        "syncrate": "1D",
        "syncurl": "rsync://rsync.kernel.org/pub/linux",
        "href": "/pub/linux",
        "lastsync": 1753538506384.4377,
        "links": [
            {
                "rel": "HOME",
                "href": "https://www.kernel.org/"
            },
            {
                "rel": "HTTP",
                "href": "http://test.ftp.kaist.ac.kr/pub/linux"
            },
            {
                "rel": "HTTPS",
                "href": "https://test.ftp.kaist.ac.kr/pub/linux"
            },
            {
                "rel": "RSYNC",
                "href": "rsync://test.ftp.kaist.ac.kr/linux"
            }
        ]
    },
    "almalinux": {
        "name": "Alma Linux",
        "id": "almalinux",
        "status": "ACTIVE",
        "synctype": "rsync-rhel",
        "syncrate": "6H",
        "syncurl": "rsync://rsync.repo.almalinux.org/almalinux",
        "href": "/almalinux/",
        "lastsync": 1753527741325.361,
        "links": [
            {
                "rel": "HOME",
                "href": "https://almalinux.org/"
            },
            {
                "rel": "HTTP",
                "href": "http://test.ftp.kaist.ac.kr/almalinux"
            },
            {
                "rel": "HTTPS",
                "href": "https://test.ftp.kaist.ac.kr/almalinux"
            },
            {
                "rel": "RSYNC",
                "href": "rsync://test.ftp.kaist.ac.kr/almalinux"
            }
        ]
    },
    "openwrt": {
        "name": "OpenWRT",
        "id": "openwrt",
        "status": "ACTIVE",
        "synctype": "rsync-rhel",
        "synctime": [
            2,
            14
        ],
        "syncrate": "1D",
        "syncurl": "https://www.openwrt.org/",
        "href": "/openwrt/",
        "lastsync": 1753524498890.6692,
        "links": [
            {
                "rel": "HOME",
                "href": "https://www.openwrt.org/"
            },
            {
                "rel": "HTTP",
                "href": "http://test.ftp.kaist.ac.kr/openwrt"
            },
            {
                "rel": "HTTPS",
                "href": "https://test.ftp.kaist.ac.kr/openwrt"
            }
        ]
    },
    "rpmfusion": {
        "name": "RPM Fusion",
        "id": "rpmfusion",
        "status": "ACTIVE",
        "synctype": "rsync-rhel",
        "synctime": [
            4,
            10,
            16,
            22
        ],
        "syncrate": "6H",
        "syncurl": "rsync://download1.rpmfusion.org/rpmfusion",
        "href": "/rpmfusion/",
        "lastsync": 1753534952508.5088,
        "links": [
            {
                "rel": "HOME",
                "href": "https://rpmfusion.org/"
            },
            {
                "rel": "HTTP",
                "href": "http://test.ftp.kaist.ac.kr/rpmfusion"
            },
            {
                "rel": "HTTPS",
                "href": "https://test.ftp.kaist.ac.kr/rpmfusion"
            },
            {
                "rel": "RSYNC",
                "href": "rsync://test.ftp.kaist.ac.kr/rpmfusion"
            }
        ]
    },
    "tinycorelinux-tc": {
        "name": "TinyCore Linux",
        "id": "tinycorelinux-tc",
        "status": "ACTIVE",
        "synctype": "reposync",
        "synctime": [
            23
        ],
        "syncrate": "1D",
        "syncurl": "rsync://repo.tinycorelinux.net/tc",
        "href": "/tc/",
        "lastsync": 1753538402021.8132,
        "links": [
            {
                "rel": "HOME",
                "href": "https://www.whitewaterfoundry.com/"
            },
            {
                "rel": "HTTP",
                "href": "http://test.ftp.kaist.ac.kr/tc/"
            },
            {
                "rel": "HTTPS",
                "href": "https://test.ftp.kaist.ac.kr/tc/"
            },
            {
                "rel": "RSYNC",
                "href": "rsync://test.ftp.kaist.ac.kr/tc"
            }
        ]
    }
}
