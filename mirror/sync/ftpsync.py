import mirror
import mirror.structure
import mirror.socket.worker
import mirror.sync
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

_ftpsync_handles: dict[str, "TemporaryDirectory"] = {}

def setup(path: Path, package: mirror.structure.Package) -> None:
    """Prepare the sync environment (no-op for ftpsync top-level setup)."""
    pass

def setup_ftpsync(path: Path, package: mirror.structure.Package) -> None:
    """Set up archvsync binary and ftpsync.conf in a temporary directory.

    Args:
        path(Path): Temporary working directory for this sync session.
        package(mirror.structure.Package): Package whose settings drive the config.
    """
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
    """Sync package via ftpsync subprocess.

    Args:
        package(mirror.structure.Package): Package to sync.
        logger(logging.Logger): Per-sync session logger.
    """
    import os

    logger.info(f"Starting ftpsync for {package.name}")

    handle = TemporaryDirectory(prefix="mirror_ftpsync_", dir=mirror.STATE_PATH)
    tmp_dir = Path(handle.name)
    _ftpsync_handles[package.pkgid] = handle

    try:
        logger.info(f"Setting up ftpsync environment in {tmp_dir}")
        setup_ftpsync(tmp_dir, package)

        command = [str(tmp_dir / "bin" / "ftpsync")]

        log_path = None
        for h in logger.handlers:
            if isinstance(h, logging.FileHandler):
                log_path = h.baseFilename
                break

        logger.info(f"Delegating ftpsync to worker: {' '.join(command)}")

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
        # Clean up temp dir; on_sync_done won't be called on this path because
        # the worker job was never created.
        h = _ftpsync_handles.pop(package.pkgid, None)
        if h is not None:
            try:
                h.cleanup()
            except Exception as cleanup_exc:
                logger.warning(f"Failed to clean up ftpsync temp dir: {cleanup_exc}")
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)


def on_sync_done(package: mirror.structure.Package, logger: logging.Logger, success: bool, returncode):
    """Clean up ftpsync temporary directory after sync completes.

    Args:
        package(mirror.structure.Package): Package object.
        logger(logging.Logger): Logger for this sync session.
        success(bool): Whether the sync succeeded.
        returncode: Process return code.
    """
    handle = _ftpsync_handles.pop(package.pkgid, None)
    if handle is not None:
        try:
            handle.cleanup()
        except Exception as e:
            logger.warning(f"Failed to clean up ftpsync temp dir: {e}")


def _check_git() -> bool:
    """Return True if git is available on PATH."""
    return shutil.which("git") is not None

def _clone_archvsync(path: Path) -> bool:
    """Clone the archvsync repository into path/archvsync.

    Args:
        path(Path): Parent directory for the clone.

    Return:
        ok(bool): True if the clone succeeded.
    """
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
    """Materialize the bundled archvsync ftpsync script into path.

    The bundled artifact is a single self-contained bash script (the archvsync
    `ftpsync` command), not a tar archive. The script is written into
    `path/archvsync/bin/ftpsync` so the layout matches what `git clone
    archvsync` would produce, allowing `setup_ftpsync` to consume either source
    interchangeably.

    Args:
        path(Path): Directory to extract into.

    Return:
        ok(bool): True if extraction succeeded.
    """
    try:
        from mirror.sync._ftpsync_script import ARCHVSYNC_HASH, ARCHVSYNC_SCRIPT

        script = b64decode(ARCHVSYNC_SCRIPT)
        if sha256(script).hexdigest() != ARCHVSYNC_HASH:
            raise ValueError("Invalid hash")

        bin_dir = path / "archvsync" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        ftpsync_path = bin_dir / "ftpsync"
        ftpsync_path.write_bytes(script)
        ftpsync_path.chmod(0o755)

        return True
    except (ValueError, OSError, ImportError) as exc:
        logger = logging.getLogger("mirror")
        logger.warning("archvsync extraction failed: %s", exc)
        return False

def _config(package: mirror.structure.Package) -> str:
    """Build ftpsync.conf content with shell-safe quoting.

    Args:
        package(mirror.structure.Package): Package whose options populate the config.

    Return:
        config_text(str): Newline-separated KEY=VALUE lines for ftpsync.conf.
    """
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


def plugin():
    """Entry-point factory for the ftpsync plug-in."""
    from mirror.plugin import sync_plugin
    return sync_plugin(name="ftpsync", execute=execute, on_sync_done=on_sync_done)