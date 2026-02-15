import mirror

from prompt_toolkit import PromptSession
from pathlib import Path
import logging
import datetime
import gzip
import shutil

from .handler import PromptHandler, GzipTimedRotatingFileHandler

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

# --- Initial Handler Setup ---
logger.handlers = [PromptHandler()]
logger.setLevel(logging.INFO)
logger.handlers[0].setLevel(logging.INFO)
logger.handlers[0].setFormatter(logging.Formatter(DEFAULT_FORMAT))


def compress_file(filepath: str | Path) -> Path | None:
    """
    Compress a file with gzip.

    Args:
        filepath: Path to the file to compress

    Returns:
        Path to the compressed file, or None if compression failed
    """
    filepath = Path(filepath)
    if not filepath.exists():
        return None

    gzip_path = filepath.with_suffix(filepath.suffix + '.gz')

    try:
        with open(filepath, 'rb') as f_in:
            with gzip.open(gzip_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        filepath.unlink()
        return gzip_path
    except Exception as e:
        logger.warning(f"Failed to compress file {filepath}: {e}")
        return None


def _time_formatting(line: str, usetime: datetime.datetime, pkgid: str | None) -> str:
    """
    Format time in the log message

    Args:
        line (str): Log message
        usetime (datetime.datetime): Time to format
        pkgid (str): Package ID
    Returns:
        str: Formatted log message
    """
    return line.format(
        year=usetime.year,
        month=usetime.month,
        day=usetime.day,
        hour=usetime.hour,
        minute=usetime.minute,
        second=usetime.second,
        microsecond=usetime.microsecond,
        packageid=pkgid,
    )


def create_logger(name: str, start_time: float) -> logging.Logger:
    """
    Create Logger for package sync.

    Args:
        name: Package name
        start_time: Start time of the sync
    Returns:
        logging.Logger: Logger object
    """
    if "packageformat" not in mirror.conf.logger:
        mirror.conf.logger["packageformat"] = DEFAULT_PACKAGE_FORMAT
    if "packagelevel" not in mirror.conf.logger:
        mirror.conf.logger["packagelevel"] = DEFAULT_PACKAGE_LEVEL
    if "fileformat" not in mirror.conf.logger:
        mirror.conf.logger["fileformat"] = DEFAULT_FILE_FORMAT

    pkg_logger = logging.getLogger(f"mirror.package.{name}")
    formatter = logging.Formatter(
        mirror.conf.logger["packageformat"].format(package=name, packageid=name)
    )
    level = logging.getLevelName(mirror.conf.logger["packagelevel"])

    prompthandler = PromptHandler()
    prompthandler.setFormatter(formatter)
    prompthandler.setLevel(level)
    pkg_logger.addHandler(prompthandler)

    now = datetime.datetime.fromtimestamp(start_time)
    folder = basePath / _time_formatting(mirror.conf.logger["fileformat"]["folder"], now, name)
    if not folder.exists():
        folder.mkdir(parents=True)

    filename = _time_formatting(mirror.conf.logger["fileformat"]["filename"], now, name)
    if "/" in filename:
        filename = filename.replace("/", "-")

    filename = folder / filename
    filehandler = logging.FileHandler(filename=str(filename), encoding="utf-8")
    filehandler.setLevel(logging.INFO)
    filehandler.setFormatter(formatter)
    pkg_logger.addHandler(filehandler)

    if mirror.debug:
        pkg_logger.handlers[0].setLevel(logging.DEBUG)
        pkg_logger.handlers[1].setLevel(logging.DEBUG)

    return pkg_logger


def close_logger(pkg_logger: logging.Logger, compress: bool | None = None) -> Path | None:
    """
    Close a package logger and optionally compress the log file.

    This function should be called when package sync is complete.
    It closes all handlers and compresses the log file if configured.

    Args:
        pkg_logger: The logger to close
        compress: Override compression setting (uses config if None)

    Returns:
        Path to the log file (compressed or not), or None if no file handler
    """
    if compress is None:
        compress = mirror.conf.logger.get("fileformat", {}).get("gzip", True)

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


def setup_logger():
    """Configure the main application logger with file and console handlers."""
    global basePath

    main_logger = logging.getLogger("mirror")
    main_logger.setLevel(logging.getLevelName(mirror.conf.logger["level"]))
    formatter = logging.Formatter(mirror.conf.logger["format"])
    main_logger.handlers[0].setFormatter(formatter)

    basePath = Path(mirror.conf.logger["fileformat"]["base"]).resolve()
    if not basePath.exists():
        basePath.mkdir(parents=True)

    now = datetime.datetime.now()
    folder = basePath / _time_formatting(mirror.conf.logger["fileformat"]["folder"], now, None)
    if not folder.exists():
        folder.mkdir(parents=True)
    filename = folder / "master.log"

    gzip_enabled = mirror.conf.logger.get("fileformat", {}).get("gzip", True)

    filehandler = GzipTimedRotatingFileHandler(
        filename=str(filename),
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8',
        gzip_enabled=gzip_enabled
    )
    filehandler.setLevel(logging.INFO)
    filehandler.setFormatter(formatter)
    main_logger.addHandler(filehandler)

    if mirror.debug:
        main_logger.setLevel(logging.DEBUG)
        main_logger.handlers[0].setLevel(logging.DEBUG)
        main_logger.handlers[1].setLevel(logging.DEBUG)
    
    mirror.log = main_logger
