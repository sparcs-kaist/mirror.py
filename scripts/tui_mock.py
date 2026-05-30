#!/usr/bin/env python3
"""Stand-alone mock master daemon for poking the mirror tui by hand.

Run in one terminal:
    uv run python scripts/tui_mock.py

Then in another terminal:
    mirror tui --socket /tmp/mock-mirror/mirror-mock.sock
"""
import threading
import time
from pathlib import Path

import mirror
import mirror.sync
from mirror.structure import Config, Package, PackageSettings
from mirror.socket.master import MasterServer

ROOT = Path("/tmp/mock-mirror")
LOG_BASE = ROOT / "log"
SOCK = ROOT / "mirror-mock.sock"
LOG_BASE.mkdir(parents=True, exist_ok=True)

mirror.conf = Config(
    name="mockmirror",
    hostname="mock.local",
    lastsettingmodified=0,
    errorcontinuetime=60,
    logfolder=LOG_BASE,
    webroot=ROOT / "www",
    statusfile=ROOT / "stat.json",
    ftpsync=Config.FTPSync(
        maintainer="me",
        sponsor="me",
        country="KR",
        location="Seoul",
        throughput="1G",
    ),
    uid=1000,
    gid=1000,
    maintainer={},
    localtimezone="Asia/Seoul",
    logger={"packagefileformat": {"base": str(LOG_BASE)}},
    max_runtime_seconds=43200,
)
for method in ("rsync", "ftpsync"):
    if method not in mirror.sync.methods:
        mirror.sync.methods.append(method)


class FakePackages:
    """Duck-typed stand-in for mirror.structure.Packages used by RPC handlers."""

    def __init__(self) -> None:
        self._keys: list[str] = []
        self._pkgs: dict[str, Package] = {}

    def add(self, pkg: Package) -> None:
        self._keys.append(pkg.pkgid)
        self._pkgs[pkg.pkgid] = pkg

    def get(self, key: str) -> Package | None:
        return self._pkgs.get(key)

    def __getitem__(self, key: str) -> Package:
        return self._pkgs[key]

    def values(self) -> list[Package]:
        return [self._pkgs[k] for k in self._keys]

    def keys(self) -> list[str]:
        return list(self._keys)

    def items(self) -> dict[str, Package]:
        return {k: self._pkgs[k] for k in self._keys}

    def __iter__(self):
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


def make_package(pkgid: str, status: str, synctype: str = "rsync") -> Package:
    return Package(
        pkgid=pkgid,
        name=pkgid,
        status=status,
        href=f"/{pkgid}/",
        synctype=synctype,
        syncrate=3600,
        link=[],
        settings=PackageSettings(hidden=False, src="", dst=""),
        lastsync=time.time() - 1800,
        disabled=False,
        timestamp=time.time() * 1000,
    )


packages = FakePackages()
for pkgid, status in [
    ("debian", "SYNC"),
    ("pypi", "ACTIVE"),
    ("centos", "ERROR"),
    ("ubuntu", "UNKNOWN"),
]:
    packages.add(make_package(pkgid, status))

runlog = LOG_BASE / "debian.live.log"
runlog.write_text("[mock] sync starting\n")
packages.get("debian").statusinfo.runninglog = str(runlog)
mirror.packages = packages

try:
    SOCK.unlink()
except FileNotFoundError:
    pass

server = MasterServer(SOCK)
server.set_version("mock")
server.start()
print(f"mock master at {SOCK}")


def tail_writer() -> None:
    n = 0
    while True:
        time.sleep(1)
        n += 1
        with runlog.open("a") as f:
            f.write(f"[mock] tick {n} {time.time():.0f}\n")


threading.Thread(target=tail_writer, daemon=True).start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    server.stop()
