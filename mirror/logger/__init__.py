import mirror
import mirror.structure

from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.formatted_text import ANSI
from pathlib import Path
import logging
import datetime

class PromptHandler(logging.StreamHandler):
    def emit(self, record):
        msg = self.format(record)
        print_formatted_text(ANSI(msg))


psession = PromptSession()
input = psession.prompt
logger = logging.getLogger("mirror")
basePath: Path

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

logger.handlers = [PromptHandler()]
logger.setLevel(logging.INFO)
logger.handlers[0].setLevel(logging.INFO)
logger.handlers[0].setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s # %(message)s"))

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

def create_logger(package: mirror.structure.Package, start_time: float) -> logging.Logger:
    """
    Create Logger for package sync.

    Args:
        package (mirror.structure.Package): Package object
        start_time (float): Start time of the sync
    Returns:
        logging.Logger: Logger object
    """

    if "packageformat" not in mirror.conf.logger:
        mirror.conf.logger["packageformat"] = DEFAULT_PACKAGE_FORMAT
    if "packagelevel" not in mirror.conf.logger:
        mirror.conf.logger["packagelevel"] = DEFAULT_PACKAGE_LEVEL
    if "fileformat" not in mirror.conf.logger:
        mirror.conf.logger["fileformat"] = DEFAULT_FILE_FORMAT

    logger = logging.getLogger(f"mirror.package.{package.name}")
    formatter = logging.Formatter(mirror.conf.logger["packageformat"].format(package=package.name, packageid=package.pkgid))
    level = logging.getLevelName(mirror.conf.logger["packagelevel"])
    
    prompthandler = PromptHandler()
    prompthandler.setFormatter(formatter)
    prompthandler.setLevel(level)
    logger.addHandler(prompthandler)

    now = datetime.datetime.fromtimestamp(start_time)
    folder = basePath / _time_formatting(mirror.conf.logger["fileformat"]["folder"], now, package.pkgid)
    if not folder.exists():
        folder.mkdir(parents=True)

    filename = _time_formatting(mirror.conf.logger["fileformat"]["filename"], now, package.pkgid)
    if "/" in filename: filename = filename.replace("/", "-")

    filename = folder / filename
    filehandler = logging.FileHandler(filename=str(filename), encoding="utf-8")
    filehandler.setLevel(logging.INFO)
    filehandler.setFormatter(formatter)
    logger.addHandler(filehandler)

    if mirror.debug:
        logger.handlers[0].setLevel(logging.DEBUG)
        logger.handlers[1].setLevel(logging.DEBUG)

    return logger

def setup_logger():
    global basePath

    logger = logging.getLogger("mirror")
    logger.setLevel(logging.getLevelName(mirror.conf.logger["level"]))
    formatter = logging.Formatter(mirror.conf.logger["format"])
    logger.handlers[0].setFormatter(formatter)

    basePath = Path(mirror.conf.logger["fileformat"]["base"]).resolve()
    if not basePath.exists():
        basePath.mkdir(parents=True)
    
    now = datetime.datetime.now()
    folder = basePath / _time_formatting(mirror.conf.logger["fileformat"]["folder"], now, None)
    if not folder.exists():
        folder.mkdir(parents=True)
    filename = folder / "master.log"

    filehandler = logging.FileHandler(filename=str(filename), encoding="utf-8")
    filehandler.setLevel(logging.INFO)
    filehandler.setFormatter(formatter)
    logger.addHandler(filehandler)

    if mirror.debug:
        logger.setLevel(logging.DEBUG)
        logger.handlers[0].setLevel(logging.DEBUG)
        logger.handlers[1].setLevel(logging.DEBUG)
