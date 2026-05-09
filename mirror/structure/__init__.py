import mirror
import mirror.toolbox
import mirror.event

from dataclasses import dataclass, asdict, field
from typing import Literal, Optional
from pathlib import Path
import json
import time

@dataclass
class Options:
    def get(self, key: str, default=None):
        """Return attribute value by name, or default if not present."""
        return getattr(self, key, default)

    def to_dict(self) -> dict:
        """Serialize dataclass fields to a dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize dataclass fields to a JSON string."""
        return json.dumps(self.to_dict())

@dataclass
class PackageSettings(Options):
    hidden: bool
    src: str
    dst: str
    options: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "PackageSettings":
        """Build PackageSettings from a config dict, ignoring unknown keys.

        Args:
            data(dict): Raw package settings dictionary.

        Return:
            settings(PackageSettings): Populated instance.
        """
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
        lasterrorlog: Optional[str] = None
        lastsuccesslog: Optional[str] = None
        runninglog: Optional[str] = None
        errorcount: int = 0
        lastsuccesstime: float = 0.0
        lasterrortime: float = 0.0

        @classmethod
        def from_dict(cls, data: dict) -> "Package.StatusInfo":
            known_fields = {
                "lasterrorlog",
                "lastsuccesslog",
                "runninglog",
                "errorcount",
                "lastsuccesstime",
                "lasterrortime",
            }
            filtered_data = {k: v for k, v in data.items() if k in known_fields}
            return cls(**filtered_data)

    pkgid: str
    name: str
    status: str
    href: str
    synctype: str
    syncrate: int
    link: list[Link]
    settings: PackageSettings
    lastsync: float = 0.0
    disabled: bool = False
    timestamp: float = 0.0
    statusinfo: StatusInfo = field(default_factory=StatusInfo)
    
    @staticmethod
    def from_dict(config: dict) -> "Package":
        import mirror.sync
        from mirror.toolbox import parse_iso_duration
        # Validation
        synctype = config["synctype"]
        if synctype not in mirror.sync.methods:
            raise ValueError(f"Sync type not in {mirror.sync.methods}")

        # Handle status and statusinfo from stat object
        status_obj = config.get("status", "UNKNOWN")
        if isinstance(status_obj, dict):
            status = status_obj.get("status", "UNKNOWN")
            statusinfo_dict = status_obj.get("statusinfo", {})
        else:
            status = status_obj
            statusinfo_dict = config.get("statusinfo", {})

        # Pull lastsync from statusinfo if present (matching mirror/config/__init__.py behavior)
        lastsync = statusinfo_dict.get("lastsync", config.get("lastsync", 0.0))

        return Package(
            pkgid=config["id"],
            name=config["name"],
            status=status,
            href=config["href"],
            synctype=synctype,
            syncrate=parse_iso_duration(config["syncrate"]),
            link=[Package.Link(lnk['rel'], lnk['href']) for lnk in config["link"]],
            settings=PackageSettings.from_dict(config["settings"]),
            lastsync=lastsync,
            statusinfo=Package.StatusInfo.from_dict(statusinfo_dict),
        )

    def __str__(self) -> str:
        return self.pkgid

    def set_status(self, status: Literal["ACTIVE", "SYNC", "ERROR", "UNKNOWN"], logfile: Optional[Path] = None) -> None:
        if status == self.status: return
        status_list = ('ACTIVE', 'SYNC', 'ERROR', 'UNKNOWN')
        if status not in status_list:
            mirror.log.error(f"Invalid status: {status}")
            if mirror.debug: raise ValueError(f"Invalid status: {status}")
            return

        if self.status == status: return
        
        mirror.event.post_event(
            "MASTER.PACKAGE_STATUS_UPDATE.PRE",
            self,
            status,
            wait=True,
        )
        self.status = status
        self.timestamp = time.time() * 1000

        now = time.time()
        if status == "ACTIVE":
            self.statusinfo.lastsuccesstime = now
            self.statusinfo.errorcount = 0
            self.statusinfo.lasterrortime = 0.0
            self.statusinfo.lasterrorlog = None
            if logfile:
                self.statusinfo.lastsuccesslog = str(logfile)
        elif status == "ERROR":
            self.statusinfo.errorcount += 1
            self.statusinfo.lasterrortime = now
            if logfile:
                self.statusinfo.lasterrorlog = str(logfile)

        mirror.event.post_event(
            "MASTER.PACKAGE_STATUS_UPDATE.POST",
            self,
            status,
        )
    
    def to_dict(self) -> dict:
        """Serialize the package to a stat-format dictionary.

        Return:
            data(dict): Package fields with "id" key and ISO 8601 syncrate.
        """
        package_dict = asdict(self)
        # Convert pkgid -> id
        package_dict["id"] = package_dict.pop("pkgid")
        package_dict["syncrate"] = mirror.toolbox.format_iso_duration(self.syncrate)
        package_dict["link"] = [link.to_dict() for link in self.link]
        package_dict["settings"] = self.settings.to_dict()

        # stat format: status is an object containing status and statusinfo
        package_dict["status"] = {
            "status": self.status,
            "statusinfo": self.statusinfo.to_dict()
        }
        if "statusinfo" in package_dict:
            del package_dict["statusinfo"]

        return package_dict

    def to_json(self) -> str:
        """Serialize the package to a JSON string."""
        return json.dumps(self.to_dict())

    def is_syncing(self) -> bool:
        """Return True if the package status is SYNC."""
        return self.status == "SYNC"

    def is_disabled(self) -> bool:
        """Return True if the package is disabled."""
        return self.disabled

    def _path_check(self, path: Path) -> None:
        if mirror.debug: return
        if not path.exists():
            raise FileNotFoundError(f"{path} does not exist")


class Packages(Options):
    def __init__(self, pkgs: dict) -> None:
        """Build the package collection from a config dict.

        Args:
            pkgs(dict): Mapping of pkgid -> package config dict.

        Raises:
            ValueError: If a pkgid collides with a Packages attribute or method name.
        """
        for pkgid in pkgs.keys():
            self._validate_id(pkgid)
        self._keys = list(pkgs.keys())
        for key in pkgs:
            setattr(self, key, Package.from_dict(pkgs[key]))

    @classmethod
    def _validate_id(cls, pkgid: str) -> None:
        """Reject pkgids that collide with reserved attributes or start with '_'.

        Args:
            pkgid(str): Candidate package identifier.

        Raises:
            ValueError: If the pkgid collides with a reserved attribute name.
        """
        if pkgid in cls._reserved_attrs() or pkgid.startswith("_"):
            raise ValueError(
                f"Invalid package id '{pkgid}': collides with a reserved attribute"
            )

    @staticmethod
    def _reserved_attrs() -> set[str]:
        """Return attribute and method names a pkgid must not collide with."""
        return {
            "get", "items", "keys", "values", "to_dict",
            "_keys", "_reserved_attrs", "_validate_id",
        }

    def __repr__(self) -> str:
        return f"Packages(ids={self._keys})"

    def get(self, key: str) -> Package | None:
        if key in self._keys:
            return getattr(self, key)
        return None

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
class PluginSettings(Options):
    enabled: bool = True
    config: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "PluginSettings":
        """Build PluginSettings from a config dict, ignoring unknown keys.

        Args:
            data(dict): Raw plugin settings dictionary.

        Return:
            settings(PluginSettings): Populated instance.
        """
        known = {"enabled", "config"}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


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
    statusfile: Path
    ftpsync: FTPSync

    uid: int
    gid: int

    maintainer: dict

    localtimezone: str
    logger: dict
    max_runtime_seconds: int = 0
    plugins: dict[str, PluginSettings] = field(default_factory=dict)

    @staticmethod
    def _parse_plugins(raw: object) -> "dict[str, PluginSettings]":
        """Parse the raw plugins config value into a dict of PluginSettings.

        Handles three shapes:
        - dict: coerce each value through PluginSettings.from_dict.
        - list: log a deprecation warning and return an empty dict.
        - missing/None: return an empty dict.

        Args:
            raw(object): The raw value read from config["settings"]["plugins"].

        Return:
            plugins(dict[str, PluginSettings]): Parsed plugin settings map.
        """
        if raw is None:
            return {}
        if isinstance(raw, list):
            mirror.log.warning(
                "Legacy 'plugins' list-of-strings shape detected; the entry-points-based"
                " plug-in system supersedes file-path entries. Migrate config to dict shape."
            )
            return {}
        if isinstance(raw, dict):
            return {name: PluginSettings.from_dict(value) for name, value in raw.items()}
        mirror.log.warning(
            f"Unexpected 'plugins' value type {type(raw).__name__!r}; ignoring."
        )
        return {}

    @staticmethod
    def load_from_dict(config: dict) -> "Config":
        """Build a Config instance from the parsed JSON config dict.

        Args:
            config(dict): Top-level config dictionary.

        Return:
            conf(Config): Populated Config instance.
        """
        from mirror.toolbox import parse_iso_duration

        raw_plugins = config["settings"].get("plugins")
        max_runtime_seconds = parse_iso_duration(config["settings"].get("max_runtime", ""))
        # 6 hours; many real syncs (initial Debian, large rsync) legitimately
        # run several hours, so a sub-6h cap is almost always a misconfiguration.
        if 0 < max_runtime_seconds < 21600:
            mirror.log.warning(
                f"settings.max_runtime={max_runtime_seconds}s is below 6h; "
                "12h or more is recommended to avoid killing legitimate long-running syncs"
            )

        return Config(
            name=config.get("mirrorname", ""),
            hostname=config.get("hostname", ""),
            lastsettingmodified=config.get("lastsettingmodified", 0),
            errorcontinuetime=config["settings"].get("errorcontinuetime", 60),
            logfolder=Path(config["settings"]["logfolder"]),
            webroot=Path(config["settings"]["webroot"]),
            statusfile=Path(config["settings"]["statusfile"]),
            uid=config["settings"].get("uid", 0),
            gid=config["settings"].get("gid", 0),
            ftpsync=Config.FTPSync(**config["settings"]["ftpsync"]),
            maintainer=config["settings"].get("maintainer", {}),
            localtimezone=config["settings"]["localtimezone"],
            logger=config["settings"]["logger"],
            max_runtime_seconds=max_runtime_seconds,
            plugins=Config._parse_plugins(raw_plugins),
        )

    def _path_check(self, path: Path) -> None:
        if mirror.debug: return
        if not path.exists():
            raise FileNotFoundError(f"{path} does not exist")
        if not path.is_dir():
            raise NotADirectoryError(f"{path} is not a directory")

    def to_dict(self) -> dict:
        """Serialize Config to a dictionary matching the config.json schema.

        Return:
            data(dict): Config as a serializable dict.
        """
        from mirror.toolbox import format_iso_duration

        return {
            "mirrorname": self.name,
            "hostname": self.hostname,
            "settings": {
                "logfolder": str(self.logfolder),
                "webroot": str(self.webroot),
                "statusfile": str(self.statusfile),
                "localtimezone": self.localtimezone,
                "errorcontinuetime": self.errorcontinuetime,
                "max_runtime": format_iso_duration(self.max_runtime_seconds),
                "maintainer": self.maintainer,
                "gid": self.gid,
                "uid": self.uid,
                "ftpsync": self.ftpsync.to_dict(),
                "logger": self.logger,
                "plugins": {name: ps.to_dict() for name, ps in self.plugins.items()},
            }
        }

    def to_json(self) -> str:
        """Serialize Config to a JSON string."""
        return json.dumps(self.to_dict())

