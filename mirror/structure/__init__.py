import mirror
import mirror.toolbox
import mirror.event

from dataclasses import dataclass, asdict, field
from typing import Literal
from pathlib import Path
import json
import time
import logging


class SyncExecuter:
    def __init__(self, package: "Package") -> None:
        self.package = package
        self.settings = package.settings
    
    def sync(self) -> None:
        pass

class Worker:
    def __init__(self, package: "Package", execute, logger: logging.Logger) -> None:
        self.package = package
        self.logger = logger
        self.sync = SyncExecuter(package)

@dataclass
class Options:
    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def to_dict(self) -> dict:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())

@dataclass
class PackageSettings(Options):
    hidden: bool
    src: str
    dst: str
    options: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "PackageSettings":
        # Filter data to only include known fields
        known_fields = {"hidden", "src", "dst", "options"}
        filtered_data = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered_data)

@dataclass
class Package:
    @dataclass
    class Link(Options):
        rel: str
        href: str
    
    @dataclass
    class StatusInfo(Options):
        lastsynclog: str
        lastsuccesslog: str
        errorcount: int

    pkgid: str
    name: str
    status: str
    href: str
    synctype: str
    syncrate: int
    link: list[Link]
    settings: PackageSettings
    lastsync: float = 0.0
    errorcount: int = 0
    disabled: bool = False
    
    @staticmethod
    def from_dict(config: dict) -> "Package":
        import mirror.sync
        from mirror.toolbox import iso_duration_parser
        # Validation
        synctype = config["synctype"]
        if synctype not in mirror.sync.methods:
            raise ValueError(f"Sync type not in {mirror.sync.methods}")
        
        return Package(
            pkgid=config["id"],
            name=config["name"],
            status=config.get("status", "UNKNOWN"),
            href=config["href"],
            synctype=synctype,
            syncrate=iso_duration_parser(config["syncrate"]),
            link=[Package.Link(lnk['rel'], lnk['href']) for lnk in config["link"]],
            settings=PackageSettings.from_dict(config["settings"]),
            lastsync=config.get("lastsync", 0.0),
            errorcount=config.get("errorcount", 0)
        )

    def __str__(self) -> str:
        return self.pkgid

    def set_status(self, status: Literal["ACTIVE", "SYNC", "ERROR", "UNKNOWN"]) -> None:
        if status == self.status: return
        status_list = ('ACTIVE', 'SYNC', 'ERROR', 'UNKNOWN')
        if status not in status_list:
            mirror.log.error(f"Invalid status: {status}")
            if mirror.debug: raise ValueError(f"Invalid status: {status}")
            return
        
        mirror.event.post_event("MASTER.PACKAGE_STATUS_UPDATE.PRE")
        self.status = status
        self.timestamp = time.time() * 1000

        if status == "ERROR":
            self.errorcount += 1
        
        mirror.event.post_event("MASTER.PACKAGE_STATUS_UPDATE.POST")
    
    def to_dict(self) -> dict:
        package_dict = asdict(self)
        package_dict["id"] = package_dict.pop("pkgid") # Convert pkgid -> id
        package_dict["syncrate"] = mirror.toolbox.iso_duration_maker(self.syncrate)
        package_dict["link"] = [link.to_dict() for link in self.link]
        package_dict["settings"] = self.settings.to_dict()
        return package_dict

    def to_json(self) -> str:
        return json.dumps(self.to_dict())
    
    def is_syncing(self) -> bool:
        return self.status == "SYNC"
    
    def is_disabled(self) -> bool:
        return self.disabled

    def _path_check(self, path: Path) -> None:
        if mirror.debug: return
        if not path.exists():
            raise FileNotFoundError(f"{path} does not exist")

class Sync:
    pkgid: str
    synctype: str
    logPath: Path | str
    options: Options
    settings: PackageSettings

    def __init__(self, pkg: "Package"):
        pkgid = pkg.pkgid
        synctype = pkg.synctype

@dataclass
class Packages(Options):
    def __init__(self, pkgs: dict) -> None:
        self._keys = list(pkgs.keys())
        for key in pkgs:
            setattr(self, key, Package.from_dict(pkgs[key]))

    def __repr__(self) -> str:
        return f"Packages(ids={self._keys})"

    def __getitem__(self, key: str) -> Package:
        if key in self._keys:
            return getattr(self, key)
        raise KeyError(key)

    def __iter__(self):
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def items(self) -> dict[str, Package]:
        return {key: getattr(self, key) for key in self._keys}

    def keys(self) -> list[str]:
        return list(self._keys)

    def values(self) -> list["Package"]:
        return [getattr(self, key) for key in self._keys]

    def to_dict(self) -> dict:
        return {key: getattr(self, key).to_dict() for key in self._keys}

@dataclass
class Config:
    @dataclass
    class FTPSync(Options):
        maintainer: str
        sponsor: str
        country: str
        location: str
        throughput: str
        include: str = ""
        exclude: str = ""

    name: str
    hostname: str
    lastsettingmodified: int
    errorcontinuetime: int

    logfolder: Path
    webroot: Path
    ftpsync: FTPSync

    uid: int
    gid: int
    
    maintainer: dict

    localtimezone: str
    logger: dict
    plugins: list[str]

    @staticmethod
    def load_from_dict(config: dict) -> "Config":
        return Config(
            name=config.get("mirrorname", ""),
            hostname=config.get("hostname", ""),
            lastsettingmodified=config.get("lastsettingmodified", 0),
            errorcontinuetime=config["settings"].get("errorcontinuetime", 60),
            logfolder=Path(config["settings"]["logfolder"]),
            webroot=Path(config["settings"]["webroot"]),
            uid=config["settings"].get("uid", 0),
            gid=config["settings"].get("gid", 0),
            ftpsync=Config.FTPSync(**config["settings"]["ftpsync"]),
            maintainer=config["settings"].get("maintainer", {}),
            localtimezone=config["settings"]["localtimezone"],
            logger=config["settings"]["logger"],
            plugins=config["settings"]["plugins"],
        )

    def _path_check(self, path: Path) -> None:
        if mirror.debug: return
        if not path.exists():
            raise FileNotFoundError(f"{path} does not exist")
        if not path.is_dir():
            raise NotADirectoryError(f"{path} is not a directory")

    def to_dict(self) -> dict:
        return {
            "mirrorname": self.name,
            "settings": {
                "logfolder": self.logfolder,
                "webroot": self.webroot,
                "gid": self.gid,
                "uid": self.uid,
                "localtimezone": self.localtimezone,
                "ftpsync": self.ftpsync.to_dict(),
                "logger": self.logger,
                "plugins": self.plugins,
            }
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())
    
    def save(self) -> None:
        mirror.confPath.write_text(self.to_json())

class Packet:
    mode: int
    sender: str
    to: str
    command: str

    def load(self, data: dict) -> None:
        self.mode = data["mode"]
        self.sender = data["sender"]
        self.to = data["to"]
        self.command = data["command"]

        return

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "sender": self.sender,
            "to": self.to,
            "command": self.command,
        }
    
    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), **kwargs)