import mirror

from prompt_toolkit import PromptSession
from pathlib import Path
import logging
import datetime
import os

from .handler import PromptHandler, DynamicGzipRotatingFileHandler, _time_formatting, compress_file, apply_configured_owner

# --- Module State ---
psession = PromptSession()
input = psession.prompt
logger = logging.getLogger("mirror")
basePath: Path

# --- Defaults ---
DEFAULT_LEVEL = "INFO"
DEFAULT_PACKAGE_LEVEL = "ERROR"
DEFAULT_FORMAT = "[%(asctime)s] %(levelname)s # %(message)s"
DEFAULT_PACKAGE_FORMAT = "[%(asctime)s][{package}] %(levelname)s # %(message)s"
DEFAULT_FILE_FORMAT = {
    "base": "/var/log/mirror",
    "folder": "{year}/{month}/{day}",
    "filename": "{hour}:{minute}:{second}.{microsecond}.{packageid}.log",
    "gzip": True,
}
DEFAULT_PACKAGE_FILE_FORMAT = {
    "base": "/var/log/mirror/packages",
    "folder": "{year}/{month}/{day}",
    "filename": "{hour}:{minute}:{second}.{microsecond}.{packageid}.log",
    "gzip": True,
}

# --- Initial Handler Setup ---
logger.handlers = [PromptHandler()]
logger.setLevel(logging.INFO)
logger.handlers[0].setLevel(logging.INFO)
logger.handlers[0].setFormatter(logging.Formatter(DEFAULT_FORMAT))


def create_logger(name: str, start_time: float) -> logging.Logger:
    """Create a per-package logger for a sync session.

    Args:
        name(str): Package name used to identify the logger and format paths.
        start_time(float): Unix timestamp of when the sync started.

    Return:
        pkg_logger(logging.Logger): Configured logger with file and prompt handlers.
    """
    if "packageformat" not in mirror.conf.logger:
        mirror.conf.logger["packageformat"] = DEFAULT_PACKAGE_FORMAT
    if "packagelevel" not in mirror.conf.logger:
        mirror.conf.logger["packagelevel"] = DEFAULT_PACKAGE_LEVEL
    if "packagefileformat" not in mirror.conf.logger:
        mirror.conf.logger["packagefileformat"] = DEFAULT_PACKAGE_FILE_FORMAT

    pkg_logger = logging.getLogger(f"mirror.package.{name}")
    for handler in pkg_logger.handlers[:]:
        handler.close()
        pkg_logger.removeHandler(handler)

    formatter = logging.Formatter(
        mirror.conf.logger["packageformat"].format(package=name, packageid=name)
    )
    level = logging.getLevelName(mirror.conf.logger["packagelevel"])

    prompthandler = PromptHandler()
    prompthandler.setFormatter(formatter)
    prompthandler.setLevel(level)
    pkg_logger.addHandler(prompthandler)

    now = datetime.datetime.fromtimestamp(start_time)
    pkg_base_path = Path(mirror.conf.logger["packagefileformat"]["base"]).resolve()
    if not pkg_base_path.exists():
        pkg_base_path.mkdir(parents=True)
    apply_configured_owner(pkg_base_path)

    folder = pkg_base_path / _time_formatting(mirror.conf.logger["packagefileformat"]["folder"], now, name)
    if not folder.exists():
        folder.mkdir(parents=True)
    apply_configured_owner(folder)

    filename = _time_formatting(mirror.conf.logger["packagefileformat"]["filename"], now, name)
    if "/" in filename:
        filename = filename.replace("/", "-")

    filename = folder / filename
    filehandler = logging.FileHandler(filename=str(filename), encoding="utf-8")
    apply_configured_owner(filename)
    filehandler.setLevel(logging.INFO)
    filehandler.setFormatter(formatter)
    pkg_logger.addHandler(filehandler)

    if mirror.debug:
        for handler in pkg_logger.handlers:
            handler.setLevel(logging.DEBUG)

    return pkg_logger


def close_logger(pkg_logger: logging.Logger, compress: bool | None = None) -> Path | None:
    """Close a package logger and optionally compress the log file.

    Args:
        pkg_logger(logging.Logger): The logger to close.
        compress(bool, optional): Override gzip setting. Uses config value if None.

    Return:
        log_path(Path | None): Path to the (compressed) log file, or None if no file handler.
    """
    if compress is None:
        compress = mirror.conf.logger.get("packagefileformat", {}).get("gzip", True)

    log_file_path: Path | None = None

    for handler in pkg_logger.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            log_file_path = Path(handler.baseFilename)
            handler.close()
            pkg_logger.removeHandler(handler)
        elif isinstance(handler, logging.StreamHandler):
            handler.close()
            pkg_logger.removeHandler(handler)

    if compress and log_file_path and log_file_path.exists():
        compressed_path = compress_file(log_file_path)
        return compressed_path or log_file_path

    return log_file_path


def setup_logger() -> None:
    """Configure the main application logger with file and console handlers."""
    global basePath

    main_logger = logging.getLogger("mirror")
    main_logger.setLevel(logging.getLevelName(mirror.conf.logger["level"]))
    formatter = logging.Formatter(mirror.conf.logger["format"])
    main_logger.handlers[0].setFormatter(formatter)

    basePath = Path(mirror.conf.logger["fileformat"]["base"]).resolve()
    gzip_enabled = mirror.conf.logger.get("fileformat", {}).get("gzip", True)

    filehandler = DynamicGzipRotatingFileHandler(
        base_path=basePath,
        folder_template=mirror.conf.logger["fileformat"]["folder"],
        filename_template=mirror.conf.logger["fileformat"]["filename"],
        gzip_enabled=gzip_enabled,
        encoding='utf-8'
    )
    filehandler.setLevel(logging.INFO)
    filehandler.setFormatter(formatter)
    main_logger.addHandler(filehandler)

    if mirror.debug:
        main_logger.setLevel(logging.DEBUG)
        main_logger.handlers[0].setLevel(logging.DEBUG)
        main_logger.handlers[1].setLevel(logging.DEBUG)
    
    mirror.log = main_logger

def get_log_path(pkg_logger: logging.Logger) -> Path | None:
    """Return the file path used by the logger's FileHandler, or None.

    Args:
        pkg_logger(logging.Logger): Package logger to inspect.

    Return:
        path(Path | None): Log file path, or None if no FileHandler is attached.
    """
    for handler in pkg_logger.handlers:
        if isinstance(handler, logging.FileHandler):
            return Path(handler.baseFilename)
    return None

def get(pkgid: str) -> logging.Logger:
    """Return the logger for the given package ID.

    Args:
        pkgid(str): Package identifier.

    Return:
        logger(logging.Logger): Logger scoped to this package.
    """
    return logging.getLogger(f"mirror.package.{pkgid}")


def exists(pkgid: str) -> bool:
    """Return True if the package logger has at least one FileHandler attached.

    Args:
        pkgid(str): Package identifier.

    Return:
        attached(bool): True if a FileHandler is present.
    """
    pkg_logger = logging.getLogger(f"mirror.package.{pkgid}")
    return any(isinstance(h, logging.FileHandler) for h in pkg_logger.handlers)


class SafeAppendFileHandler(logging.FileHandler):
    """FileHandler that adopts a pre-validated file descriptor as its stream.

    Skips FileHandler.__init__'s implicit open so the caller can perform
    O_NOFOLLOW + fstat validation, then pass the validated fd here. If the
    handler is closed and later forced to reopen (e.g. logging.shutdown
    re-emit), the override of _open() uses O_NOFOLLOW so symlink
    redirection still cannot succeed.

    Args:
        fd(int): Pre-opened, pre-validated file descriptor (will be owned by
            this handler from this point on).
        filename(str | Path): Original path (stored as baseFilename for the
            close_logger compress + unlink chain to see).
        encoding(str): Text encoding for the stream.
    """

    def __init__(self, fd: int, filename, encoding: str = "utf-8"):
        logging.Handler.__init__(self)
        self.baseFilename = str(filename)
        self.mode = "a"
        self.encoding = encoding
        self.errors = None
        self.delay = False
        self.stream = os.fdopen(fd, "a", encoding=encoding)

    def _open(self):
        # Used only if the stream was closed and logging tries to reopen.
        # Stay safe with O_NOFOLLOW.
        fd = os.open(
            self.baseFilename,
            os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
        )
        return os.fdopen(fd, "a", encoding=self.encoding or "utf-8")


def reattach_logger(pkg_logger: logging.Logger, log_file_path: Path, pkgid: str) -> bool:
    """Reattach a FileHandler to an existing in-base log file.

    Used by on_sync_done after master restart. Performs strict validation:
    - path resolves inside the configured package log base
    - opens with O_NOFOLLOW (refuses symlinks atomically)
    - fstat on the SAME fd confirms regular file with st_nlink == 1
    - that same fd is then adopted by SafeAppendFileHandler — no reopen
    Returns True if a handler was attached, False otherwise.

    Args:
        pkg_logger(logging.Logger): Logger to attach a FileHandler to.
        log_file_path(Path): Path to the log file (from stat.json runninglog).
        pkgid(str): Package identifier (used in warning messages).

    Return:
        attached(bool): True if a handler was successfully attached.
    """
    import stat as _stat

    if any(isinstance(h, logging.FileHandler) for h in pkg_logger.handlers):
        return False

    if "packagefileformat" not in mirror.conf.logger:
        mirror.conf.logger["packagefileformat"] = DEFAULT_PACKAGE_FILE_FORMAT
    base = Path(mirror.conf.logger["packagefileformat"]["base"]).resolve(strict=False)

    try:
        resolved = log_file_path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        mirror.log.warning(f"reattach_logger({pkgid}): resolve failed: {exc}")
        return False

    try:
        resolved.relative_to(base)
    except ValueError:
        mirror.log.warning(
            f"reattach_logger({pkgid}): refusing path outside package log base "
            f"(base={base}, requested={log_file_path})"
        )
        return False

    if log_file_path.is_symlink():
        mirror.log.warning(f"reattach_logger({pkgid}): refusing symlink path: {log_file_path}")
        return False

    if not resolved.exists():
        return False

    try:
        fd = os.open(str(resolved), os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    except OSError as exc:
        mirror.log.warning(f"reattach_logger({pkgid}): O_NOFOLLOW open failed: {exc}")
        return False

    try:
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode):
            os.close(fd)
            mirror.log.warning(f"reattach_logger({pkgid}): not a regular file: {log_file_path}")
            return False
        if st.st_nlink > 1:
            os.close(fd)
            mirror.log.warning(
                f"reattach_logger({pkgid}): refusing hardlinked file (st_nlink={st.st_nlink}): "
                f"{log_file_path}"
            )
            return False
    except Exception:
        os.close(fd)
        raise

    if "packageformat" not in mirror.conf.logger:
        mirror.conf.logger["packageformat"] = DEFAULT_PACKAGE_FORMAT
    if "packagelevel" not in mirror.conf.logger:
        mirror.conf.logger["packagelevel"] = DEFAULT_PACKAGE_LEVEL

    formatter = logging.Formatter(
        mirror.conf.logger["packageformat"].format(package=pkgid, packageid=pkgid)
    )
    level = logging.getLevelName(mirror.conf.logger["packagelevel"])

    # Adopt the validated fd directly — no reopen, validation applies to the
    # actually-used file descriptor.
    filehandler = SafeAppendFileHandler(fd=fd, filename=resolved, encoding="utf-8")
    filehandler.setLevel(level)
    filehandler.setFormatter(formatter)
    pkg_logger.addHandler(filehandler)
    pkg_logger.setLevel(level)
    return True

