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
import shutil
import shlex
import os
import re
import sys
import uuid
import datetime
import urllib.parse

ARCHVSYNC_REPO = "https://salsa.debian.org/mirror-team/archvsync.git"

_ftpsync_handles: dict[str, "TemporaryDirectory"] = {}

def setup(path: Path, package: mirror.structure.Package) -> None:
    """Prepare the sync environment (no-op for ftpsync top-level setup)."""
    pass

def setup_ftpsync(
    path: Path,
    package: mirror.structure.Package,
    log_dir: Path | None = None,
    log_name: str | None = None,
) -> None:
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

    (path / "etc" / "ftpsync.conf").write_text(_config(package, log_dir, log_name))

def execute(package: mirror.structure.Package, logger: logging.Logger):
    """Sync package via ftpsync subprocess.

    Args:
        package(mirror.structure.Package): Package to sync.
        logger(logging.Logger): Per-sync session logger.
    """
    logger.info(f"Starting ftpsync for {package.name}")

    handle = TemporaryDirectory(prefix="mirror_ftpsync_", dir=mirror.STATE_PATH)
    tmp_dir = Path(handle.name)
    _ftpsync_handles[package.pkgid] = handle

    try:
        log_path = None
        for h in logger.handlers:
            if isinstance(h, logging.FileHandler):
                log_path = h.baseFilename
                break

        ftpsync_log_dir = None
        ftpsync_log_name = None
        prelude_log_path = None
        log_helper_command = None
        if log_path:
            _prepare_log_path(Path(log_path))
            ftpsync_log_dir, ftpsync_log_name = _run_log_dir(package)
            _prepare_log_path(ftpsync_log_dir)
            prelude_log_path = ftpsync_log_dir / "prelude.log"
            _prepare_log_path(prelude_log_path)
            log_helper_command = _log_helper_command(
                Path(log_path),
                prelude_log_path,
                ftpsync_log_dir,
                ftpsync_log_name,
            )

        logger.info(f"Setting up ftpsync environment in {tmp_dir}")
        setup_ftpsync(tmp_dir, package, ftpsync_log_dir, ftpsync_log_name)

        command = [str(tmp_dir / "bin" / "ftpsync")]

        logger.info(f"Delegating ftpsync to worker: {' '.join(command)}")

        env = dict(mirror.sync.get_extra_args(package.pkgid))
        mirror.socket.worker.execute_command(
            job_id=package.pkgid,
            sync_method="ftpsync",
            commandline=command,
            env=env,
            uid=mirror.conf.uid,
            gid=mirror.conf.gid,
            log_path=prelude_log_path if prelude_log_path else log_path,
            log_helper_command=log_helper_command,
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

def _split_rsync_src(src: str, opts: dict) -> tuple[str, str]:
    """Split an rsync source into the RSYNC_HOST and RSYNC_PATH ftpsync expects.

    Args:
        src(str): Package source. Either a full rsync URL
            (e.g. "rsync://host/module/") or a bare host (legacy shape).
        opts(dict): Package sync options; supplies "path" for the bare-host
            shape and may override the module derived from a URL.

    Return:
        host_path(tuple[str, str]): (host, module path) for RSYNC_HOST/RSYNC_PATH.

    Raises:
        ValueError: A URL src has no host, or a bare host src has no "path".
    """
    explicit_path = opts.get("path")
    if "://" in src:
        parsed = urllib.parse.urlparse(src)
        host = parsed.netloc
        if not host:
            raise ValueError(f"ftpsync src {src!r} has no host")
        path = explicit_path if explicit_path is not None else parsed.path.lstrip("/")
        return host, path
    if explicit_path is None:
        raise ValueError(
            "ftpsync requires either an rsync:// src URL or a 'path' option"
        )
    return src, explicit_path


def _config(
    package: mirror.structure.Package,
    log_dir: Path | None = None,
    log_name: str | None = None,
) -> str:
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

    rsync_host, rsync_path = _split_rsync_src(package.settings.src, opts)

    lines = [
        f"MIRRORNAME={_q('mirrorname', mirror.conf.name)}",
        f"TO={_q('dst', package.settings.dst)}",
        f"HUB={_q('hub', opts.get('hub', 'false'))}",
        f"RSYNC_HOST={_q('src', rsync_host)}",
        f"RSYNC_PATH={_q('path', rsync_path)}",
    ]
    if log_name is not None:
        lines.append(f"NAME={_q('name', log_name)}")
    if "user" in opts and "password" in opts:
        lines.append(f"RSYNC_USER={_q('user', opts['user'])}")
        lines.append(f"RSYNC_PASSWORD={_q('password', opts['password'])}")
    email = opts.get('email')
    if email:
        lines.append(f"MAILTO={_q('email', email)}")
    # INFO_* and ARCH_* default from the global settings.ftpsync block;
    # per-package options override on a per-key basis; emitted only when non-empty.
    gftp = mirror.conf.ftpsync
    maintainer = opts.get('maintainer', gftp.maintainer)
    if maintainer:
        lines.append(f"INFO_MAINTAINER={_q('maintainer', maintainer)}")
    sponsor = opts.get('sponsor', gftp.sponsor)
    if sponsor:
        lines.append(f"INFO_SPONSOR={_q('sponsor', sponsor)}")
    country = opts.get('country', gftp.country)
    if country:
        lines.append(f"INFO_COUNTRY={_q('country', country)}")
    location = opts.get('location', gftp.location)
    if location:
        lines.append(f"INFO_LOCATION={_q('location', location)}")
    throughput = opts.get('throughput', gftp.throughput)
    if throughput:
        lines.append(f"INFO_THROUGHPUT={_q('throughput', throughput)}")
    arch_include = opts.get('arch_include', gftp.include)
    if arch_include:
        lines.append(f"ARCH_INCLUDE={_q('arch_include', arch_include)}")
    arch_exclude = opts.get('arch_exclude', gftp.exclude)
    if arch_exclude:
        lines.append(f"ARCH_EXCLUDE={_q('arch_exclude', arch_exclude)}")
    lines.append(f"LOGDIR={_q('logdir', log_dir or opts.get('logdir', mirror.conf.logfolder))}")
    return "\n".join(lines) + "\n"


def _safe_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]", "_", value)
    clean = clean.strip("._-") or "ftpsync"
    if clean != value:
        digest = uuid.uuid5(uuid.NAMESPACE_URL, value).hex[:8]
        clean = f"{clean[:111]}-{digest}"
    return clean[:120]


def _run_log_dir(package: mirror.structure.Package) -> tuple[Path, str]:
    opts = package.settings.options
    base = Path(opts.get("logdir", mirror.conf.logfolder))
    safe_pkgid = _safe_name(package.pkgid)
    token = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    token = f"{token}-{uuid.uuid4().hex[:6]}"
    log_name = f"{safe_pkgid}-{token}"
    return base / "ftpsync-runs" / safe_pkgid / token, log_name


def _prepare_log_path(path: Path) -> None:
    from mirror.logger.handler import apply_configured_owner

    if path.suffix:
        path.parent.mkdir(parents=True, exist_ok=True)
        apply_configured_owner(path.parent)
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o644)
        os.close(fd)
    else:
        path.mkdir(parents=True, exist_ok=True)
    apply_configured_owner(path)


def _log_helper_command(
    package_log_path: Path,
    prelude_log_path: Path,
    ftpsync_log_dir: Path,
    ftpsync_log_name: str,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "mirror.worker.logmerge",
        "--dest",
        str(package_log_path),
        "--source",
        f"ftpsync:prelude={prelude_log_path}",
        "--source",
        f"ftpsync={ftpsync_log_dir / (ftpsync_log_name + '.log')}",
        "--source",
        f"rsync={ftpsync_log_dir / ('rsync-' + ftpsync_log_name + '.log')}",
        "--source",
        f"rsync:error={ftpsync_log_dir / ('rsync-' + ftpsync_log_name + '.error')}",
    ]


def plugin():
    """Entry-point factory for the ftpsync plug-in."""
    from mirror.plugin import sync_plugin
    return sync_plugin(name="ftpsync", execute=execute, on_sync_done=on_sync_done)
