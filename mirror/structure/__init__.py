from __future__ import annotations

import mirror
import mirror.toolbox
import mirror.sync

from dataclasses import dataclass, asdict
from pathlib import Path
import json
import time
import logging

class SyncExecuter:
    def __init__(self, package: Package) -> None:
        self.package = package
        self.settings = package.settings
    
    def sync(self) -> None:
        pass

class Worker:
    def __init__(self, package: Package, execute, logger: logging.Logger) -> None:
        self.package = package
        self.logger = logger
        self.sync = SyncExecuter(package)

@dataclass
class Options:
    def to_dict(self) -> dict:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())

@dataclass
class PackageSettings(Options):
    hidden: bool
    src: str
    dst: str
    options: Options

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

    @staticmethod
    def from_dict(config: dict) -> Package:
        # Validation
        synctype = config["synctype"]
        if synctype not in mirror.sync.methods:
            raise ValueError(f"Sync type not in {mirror.sync.methods}")
        
        return Package(
            pkgid=config["id"],
            name=config["name"],
            status=config["status"],
            href=config["href"],
            synctype=synctype,
            syncrate=mirror.toolbox.iso_duration_parser(config["syncrate"]),
            link=[Package.Link(lnk['rel'], lnk['href']) for lnk in config["link"]],
            settings=PackageSettings(**config["settings"])
        )

    def __str__(self) -> str:
        return self.pkgid

    def set_status(self, status) -> None:
        if status == self.status: return
        statuslist = ["ACTIVE", "ERROR", "SYNC", "UNKNOWN"]
        if status not in statuslist:
            raise ValueError(f"Status not in {statuslist}")
        
        self.status = status
        self.timestamp = time.time() * 1000

        if status == "ERROR":
            self.errorcount += 1
    
    def to_dict(self) -> dict:
        package_dict = asdict(self)
        package_dict["syncrate"] = mirror.toolbox.iso_duration_maker(self.syncrate)
        package_dict["link"] = [link.to_dict() for link in self.link]
        package_dict["settings"] = self.settings.to_dict()
        return package_dict

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

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

    def __init__(self, pkg: Package):
        pkgid = pkg.pkgid
        synctype = pkg.synctype

@dataclass
class Packages(Options):
    def __init__(self, pkgs: dict) -> None:
        self.keys = pkgs.keys()
        for key in pkgs:
            setattr(self, key, Package(**pkgs[key]))

    def to_dict(self) -> dict:
        return {key: getattr(self, key).to_dict() for key in self.keys}

@dataclass
class Config:
    @dataclass
    class FTPSync(Options):
        maintainer: str
        sponsor: str
        country: str
        location: str
        throughput: str
        include: str
        exclude: str

    name: str
    lastsettingmodified: int

    logfolder: Path
    webroot: Path
    ftpsync: FTPSync

    uid: int
    gid: int

    localtimezone: str
    logger: dict
    plugins: list[str]

    @staticmethod
    def load_from_dict(config: dict) -> Config:
        return Config(
            name=config["mirrorname"],
            lastsettingmodified=config.get("lastsettingmodified", 0),
            logfolder=Path(config["settings"]["logfolder"]),
            webroot=Path(config["settings"]["webroot"]),
            uid=config["settings"]["uid"],
            gid=config["settings"]["gid"],
            ftpsync=Config.FTPSync(**config["settings"]["ftpsync"]),
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