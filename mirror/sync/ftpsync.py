import mirror
import mirror.structure

from tempfile import TemporaryDirectory
from base64 import b64decode
from hashlib import sha256
from pathlib import Path
import subprocess
import logging
import tarfile
import shutil
import io
import os

ARCHVSYNC_REPO = "https://salsa.debian.org/mirror-team/archvsync.git"

def setup(path: Path, package: mirror.structure.Package):
    pass

def setup_ftpsync(path: Path, package: mirror.structure.Package):
    """Setup archvsync and package config"""
    (path / "bin").mkdir(exist_ok=True)
    (path / "etc").mkdir(exist_ok=True)

    # Fetch archvsync: Try git clone first, fallback to base64 extraction
    if _check_git() and _clone_archvsync(path):
        archvsync_path = path / "archvsync"
    elif _extract_archvsync(path):
        dirs = [d for d in path.iterdir() if d.is_dir() and d.name != "bin" and d.name != "etc"]
        if not dirs:
            raise RuntimeError("Failed to find archvsync directory after extraction")
        archvsync_path = dirs[0]
    else:
        raise RuntimeError("Failed to setup archvsync: git clone failed and fallback extraction failed")

    # Copy required files from archvsync bin directory
    src_bin = archvsync_path / "bin"
    for script in src_bin.iterdir():
        if script.is_file():
            dst = path / "bin" / script.name
            shutil.copy2(script, dst)
            dst.chmod(0o755)

    (path / "etc" / "ftpsync.conf").write_text(_config(package))

def execute(package: mirror.structure.Package, logger: logging.Logger):
    """Sync package"""
    
    pass

def _check_git() -> bool:
    """Check if git command is available"""
    return shutil.which("git") is not None

def _clone_archvsync(path: Path) -> bool:
    """Clone archvsync repository to path"""
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", ARCHVSYNC_REPO, str(path / "archvsync")],
            capture_output=True,
            timeout=60
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False

def _extract_archvsync(path: Path) -> bool:
    """Extract archvsync from base64 encoded tar.gz"""
    try:
        from mirror.sync._ftpsync_script import ARCHVSYNC_HASH, ARCHVSYNC_SCRIPT

        script = b64decode(ARCHVSYNC_SCRIPT)
        if sha256(script).hexdigest() != ARCHVSYNC_HASH:
            raise ValueError("Invalid hash")

        tar_buffer = io.BytesIO(script)
        with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
            tar.extractall(path=path)

        return True
    except Exception:
        return False

def ftpsync(package: mirror.structure.Package) -> None:
    """Sync package"""

    package.set_status("SYNC")

    os.setgid(mirror.conf.gid)
    os.setuid(mirror.conf.uid)

    logger = logging.getLogger(f"mirror.package.{package.name}")
    tmp = Path(TemporaryDirectory().name)
    tmp.mkdir()

    setup_ftpsync(tmp, package)

    command = [
        f"{tmp}/bin/ftpsync",
    ]
    result = subprocess.run(command, shell=True, check=True)
    if result.returncode == 0:
        package.set_status("ACTIVE")
    else:
        package.set_status("ERROR")

def _config(package: mirror.structure.Package) -> str:
    """Create config file"""
    opts = package.settings.options

    config = ""
    config += f"MIRRORNAME=\"{mirror.conf.name}\"\n"
    config += f"TO=\"{package.settings.dst}\"\n"
    config += f"MAILTO=\"{opts['email']}\"\n"
    config += f"HUB={opts['hub']}\n"
    config += f"RSYNC_HOST=\"{package.settings.src}\"\n"
    config += f"RSYNC_PATH=\"{opts['path']}\"\n"

    if "user" in opts and "password" in opts:
        config += f"RSYNC_USER=\"{opts['user']}\"\n"
        config += f"RSYNC_PASSWORD=\"{opts['password']}\"\n"
    if "maintainer" in opts:
        config += f"INFO_MAINTAINER=\"{opts['maintainer']}\"\n"
    if "sponsor" in opts:
        config += f"INFO_SPONSOR=\"{opts['sponsor']}\"\n"
    if "country" in opts:
        config += f"INFO_COUNTRY={opts['country']}\n"
    if "location" in opts:
        config += f"INFO_LOCATION=\"{opts['location']}\"\n"
    if "throughput" in opts:
        config += f"INFO_THROUGHPUT={opts['throughput']}\n"
    if "arch_include" in opts:
        config += f"ARCH_INCLUDE=\"{opts['arch_include']}\"\n"
    if "arch_exclude" in opts:
        config += f"ARCH_EXCLUDE=\"{opts['arch_exclude']}\"\n"

    config += f"LOGDIR=\"{opts.get('logdir', mirror.conf.logfolder)}\"\n"

    return config