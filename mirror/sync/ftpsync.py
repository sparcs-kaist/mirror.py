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
    from mirror.socket.worker import WorkerClient
    import time
    import os

    # Set status to SYNC as soon as we enter execute
    package.set_status("SYNC")
    logger.info(f"Starting ftpsync for {package.name}")

    # Temporary directory for ftpsync scripts and config
    # Note: Using a fixed-prefix path in /tmp so worker can access it
    tmp_base = Path("/tmp/mirror_ftpsync")
    tmp_base.mkdir(exist_ok=True)
    tmp_dir = Path(TemporaryDirectory(dir=tmp_base).name)
    
    try:
        # 1. Setup ftpsync environment (scripts and config)
        logger.info(f"Setting up ftpsync environment in {tmp_dir}")
        setup_ftpsync(tmp_dir, package)

        # 2. Prepare commandline
        # The main script is located in the bin directory we just set up
        command = [str(tmp_dir / "bin" / "ftpsync")]

        # 3. Delegate to Worker
        socket_path = Path("/run/mirror/worker.sock")
        
        log_path = None
        for handler in logger.handlers:
            if isinstance(handler, logging.FileHandler):
                log_path = handler.baseFilename
                break

        with WorkerClient(socket_path) as client:
            logger.info(f"Delegating ftpsync to worker: {' '.join(command)}")
            
            response = client.execute_command(
                job_id=package.pkgid,
                sync_method="ftpsync",
                commandline=command,
                env={}, 
                uid=os.getuid(),
                gid=os.getgid(),
                log_path=log_path
            )

            if response.get("status") == "started":
                logger.info(f"Worker started ftpsync (PID: {response.get('job_pid')})")
                package.lastsync = time.time()
                package.set_status("ACTIVE")
            else:
                raise RuntimeError(f"Worker failed to start ftpsync: {response.get('message')}")

    except Exception as e:
        logger.error(f"ftpsync for {package.pkgid} failed: {e}")
        package.set_status("ERROR")
        # Cleanup temp dir on failure (on success, it might be needed by the worker process)
        # However, since start_sync is asynchronous, we might need a better cleanup strategy.
        # For now, we leave it for the system /tmp cleanup or manual intervention if it fails.
    finally:
        # Note: We don't close_logger here because it might be closed by the caller 
        # but mirror.logger.close_logger(logger) is used in rsync.py
        import mirror.logger
        mirror.logger.close_logger(logger)

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