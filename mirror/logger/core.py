import mirror

from prompt_toolkit import PromptSession
from pathlib import Path
import logging
import datetime
import gzip
import shutil

from .handler import PromptHandler, GzipTimedRotatingFileHandler, DynamicGzipRotatingFileHandler, _time_formatting, compress_file

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
    if "packagefileformat" not in mirror.conf.logger:
        mirror.conf.logger["packagefileformat"] = DEFAULT_PACKAGE_FILE_FORMAT

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
    pkg_base_path = Path(mirror.conf.logger["packagefileformat"]["base"]).resolve()
    if not pkg_base_path.exists():
        pkg_base_path.mkdir(parents=True)

    folder = pkg_base_path / _time_formatting(mirror.conf.logger["packagefileformat"]["folder"], now, name)
    if not folder.exists():
        folder.mkdir(parents=True)

    filename = _time_formatting(mirror.conf.logger["packagefileformat"]["filename"], now, name)
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


def setup_logger():
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
