import mirror
import mirror.structure
from mirror.sync.ftpsync_script import ARCHVSYNC_SCRIPT

from tempfile import TemporaryDirectory
from base64 import b64decode
from hashlib import sha256
from pathlib import Path
import subprocess
import logging
import os



def ftpsync(package: mirror.structure.Package) -> None:
    """Sync package"""
    
    package.set_status("SYNC")

    os.setgid(mirror.conf.gid)
    os.setuid(mirror.conf.uid)

    logger = logging.getLogger(f"mirror.package.{package.name}")
    tmp = Path(TemporaryDirectory().name)
    tmp.mkdir()

    _setup(tmp, package)

    command = [
        f"{tmp}/bin/ftpsync",
    ]
    result = subprocess.run(command, shell=True, check=True)
    if result.returncode == 0:
        package.set_status("ACTIVE")
    else:
        package.set_status("ERROR")

def _setup(path: Path, package: mirror.structure.Package):
    """Setup package"""
    (path / "bin").mkdir()
    (path / "etc").mkdir()

    script = b64decode(ARCHVSYNC_SCRIPT)
    if sha256(script).hexdigest() != ARCHVSYNC_HASH:
        raise ValueError("Invalid hash")

    (path / "bin" / "ftpsync").write_bytes(script)
    (path / "bin" / "ftpsync").chmod(0o744)
    
    (path / "etc" / "ftpsync.conf").write_text(_config(package))


def _config(package: mirror.structure.Package) -> str:
    """Create config file"""
    config = ""
    config += f"MIRRORNAME=\"{mirror.conf.name}\"\n"
    config += f"TO=\"{package.settings.dst}\"\n"
    config += f"MAILTO=\"{package.settings.email}\"\n"
    config += f"HUB={package.settings.hub}\n"
    config += f"RSYNC_HOST=\"{package.settings.src}\"\n"
    config += f"RSYNC_PATH=\"{package.settings.path}\"\n"

    if "user" in dir(package.settings) and "password" in dir(package.settings):
        config += f"RSYNC_USER=\"{package.settings.user}\"\n"
        config += f"RSYNC_PASSWORD=\"{package.settings.password}\"\n"

    config += f"INFO_MAINTAINER=\"{package.settings.maintainer}\"\n" if "maintainer" in dir(package.settings) else ""
    config += f"INFO_SPONSOR=\"{package.settings.sponsor}\"\n" if "sponsor" in dir(package.settings) else ""
    config += f"INFO_COUNTRY={package.settings.country}\n" if "country" in dir(package.settings) else ""
    config += f"INFO_LOCATION=\"{package.settings.location}\"\n" if "location" in dir(package.settings) else ""
    config += f"INFO_THROUGHPUT={package.settings.throughput}\n" if "throughput" in dir(package.settings) else ""
    config += f"ARCH_INCLUDE=\"{package.settings.arch_include}\"\n" if "arch_include" in dir(package.settings) else ""
    config += f"ARCH_EXCLUDE=\"{package.settings.arch_exclude}\"\n" if "arch_exclude" in dir(package.settings) else ""
    config += f"LOGDIR=\"{package.settings.logdir if 'logdir' in dir(package.settings) else mirror.conf.logdir}\"\n" 

    return config

def _test() -> mirror.structure.Package:
    """Test function"""
    package = mirror.structure.Package()
    package.settings.src = "rsync://rsync.archlinux.org/ftp"
    package.settings.dst = "/var/www/mirror/archlinux"
    package.settings.email = ""
    package.settings.hub = "rsync.archlinux.org"