import mirror
import mirror.structure
import mirror.socket.worker
import mirror.logger

from tempfile import TemporaryDirectory
from base64 import b64decode
from hashlib import sha256
from pathlib import Path
import subprocess
import logging
import tarfile
import shutil
import shlex
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
    import os

    logger.info(f"Starting ftpsync for {package.name}")

    tmp_base = Path("/tmp/mirror_ftpsync")
    tmp_base.mkdir(exist_ok=True)
    _tmp_handle = TemporaryDirectory(dir=tmp_base)
    tmp_dir = Path(_tmp_handle.name)

    try:
        logger.info(f"Setting up ftpsync environment in {tmp_dir}")
        setup_ftpsync(tmp_dir, package)

        command = [str(tmp_dir / "bin" / "ftpsync")]

        log_path = None
        for handler in logger.handlers:
            if isinstance(handler, logging.FileHandler):
                log_path = handler.baseFilename
                break

        logger.info(f"Delegating ftpsync to worker: {' '.join(command)}")

        package._ftpsync_tmp = _tmp_handle

        mirror.socket.worker.execute_command(
            job_id=package.pkgid,
            sync_method="ftpsync",
            commandline=command,
            env={},
            uid=os.getuid(),
            gid=os.getgid(),
            log_path=log_path,
        )

    except Exception as e:
        logger.error(f"ftpsync for {package.pkgid} failed: {e}")
        mirror.logger.close_logger(logger)
        package.set_status("ERROR")


def on_sync_done(package: mirror.structure.Package, logger: logging.Logger, success: bool, returncode):
    """Clean up ftpsync temporary directory after sync completes

    Args:
        package(mirror.structure.Package): Package object
        logger(logging.Logger): Logger for this sync session
        success(bool): Whether the sync succeeded
        returncode: Process return code
    """
    tmp_handle = getattr(package, "_ftpsync_tmp", None)
    if tmp_handle is not None:
        try:
            tmp_handle.cleanup()
        except Exception as e:
            logger.warning(f"Failed to clean up ftpsync temp dir: {e}")
        finally:
            package._ftpsync_tmp = None


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

def _config(package: mirror.structure.Package) -> str:
    """Build ftpsync config text with shell-safe quoting."""
    opts = package.settings.options

    def _q(key: str, value) -> str:
        s = str(value)
        if "\n" in s or "\r" in s:
            raise ValueError(f"ftpsync option {key} must not contain newlines")
        return shlex.quote(s)

    lines = [
        f"MIRRORNAME={_q('mirrorname', mirror.conf.name)}",
        f"TO={_q('dst', package.settings.dst)}",
        f"MAILTO={_q('email', opts['email'])}",
        f"HUB={_q('hub', opts['hub'])}",
        f"RSYNC_HOST={_q('src', package.settings.src)}",
        f"RSYNC_PATH={_q('path', opts['path'])}",
    ]
    if "user" in opts and "password" in opts:
        lines.append(f"RSYNC_USER={_q('user', opts['user'])}")
        lines.append(f"RSYNC_PASSWORD={_q('password', opts['password'])}")
    if "maintainer" in opts:
        lines.append(f"INFO_MAINTAINER={_q('maintainer', opts['maintainer'])}")
    if "sponsor" in opts:
        lines.append(f"INFO_SPONSOR={_q('sponsor', opts['sponsor'])}")
    if "country" in opts:
        lines.append(f"INFO_COUNTRY={_q('country', opts['country'])}")
    if "location" in opts:
        lines.append(f"INFO_LOCATION={_q('location', opts['location'])}")
    if "throughput" in opts:
        lines.append(f"INFO_THROUGHPUT={_q('throughput', opts['throughput'])}")
    if "arch_include" in opts:
        lines.append(f"ARCH_INCLUDE={_q('arch_include', opts['arch_include'])}")
    if "arch_exclude" in opts:
        lines.append(f"ARCH_EXCLUDE={_q('arch_exclude', opts['arch_exclude'])}")
    lines.append(f"LOGDIR={_q('logdir', opts.get('logdir', mirror.conf.logfolder))}")
    return "\n".join(lines) + "\n"