from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import ANSI
from logging.handlers import TimedRotatingFileHandler
import logging
import gzip
import shutil
import os
import time
import datetime
from pathlib import Path


def _time_formatting(line: str, usetime: datetime.datetime, pkgid: str | None = None) -> str:
    """
    Format time in the log message or path.
    Pre-formats components with zero-padding (e.g., month as '02').

    Args:
        line (str): Template string
        usetime (datetime.datetime): Time to format
        pkgid (str): Package ID (optional)
    Returns:
        str: Formatted string
    """
    return line.format(
        year=f"{usetime.year:04d}",
        month=f"{usetime.month:02d}",
        day=f"{usetime.day:02d}",
        hour=f"{usetime.hour:02d}",
        minute=f"{usetime.minute:02d}",
        second=f"{usetime.second:02d}",
        microsecond=f"{usetime.microsecond:06d}",
        packageid=pkgid if pkgid is not None else "",
    )


def compress_file(filepath: str | Path) -> Path | None:
    """
    Compress a file with gzip and remove the original.

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
        logging.getLogger("mirror").warning(f"Failed to compress file {filepath}: {e}")
        return None


class PromptHandler(logging.StreamHandler):
    """Handler that outputs to prompt_toolkit formatted text."""

    def emit(self, record):
        msg = self.format(record)
        print_formatted_text(ANSI(msg))


class DynamicGzipRotatingFileHandler(logging.FileHandler):
    """
    FileHandler that rotates when the formatted path changes.
    Supports dynamic folders and filenames based on time templates.
    """

    def __init__(self, base_path: str | Path, folder_template: str, filename_template: str,
                 gzip_enabled: bool = True, encoding: str | None = 'utf-8', delay: bool = False):
        self.base_path = Path(base_path)
        self.folder_template = folder_template
        self.filename_template = filename_template
        self.gzip_enabled = gzip_enabled

        now = datetime.datetime.now()
        initial_path = self._resolve_path(now)
        initial_path.parent.mkdir(parents=True, exist_ok=True)

        super().__init__(str(initial_path), encoding=encoding, delay=delay)
        self.current_formatted_path = str(initial_path)

    def _resolve_path(self, dt: datetime.datetime) -> Path:
        folder = _time_formatting(self.folder_template, dt, None)
        filename = _time_formatting(self.filename_template, dt, None)
        # Ensure no directory traversal in filename
        if "/" in filename:
            filename = filename.replace("/", "-")
        return self.base_path / folder / filename

    def emit(self, record):
        """Check if path needs rotation before emitting."""
        dt = datetime.datetime.fromtimestamp(record.created)
        new_path = str(self._resolve_path(dt))

        if new_path != self.current_formatted_path:
            self.do_rotation(new_path)

        super().emit(record)

    def do_rotation(self, new_path: str):
        """Close current file, optionally compress it, and open the new one."""
        old_path = self.baseFilename
        if self.stream:
            self.stream.close()
            self.stream = None

        if self.gzip_enabled and os.path.exists(old_path):
            compress_file(old_path)

        Path(new_path).parent.mkdir(parents=True, exist_ok=True)
        self.baseFilename = new_path
        self.current_formatted_path = new_path


class GzipTimedRotatingFileHandler(TimedRotatingFileHandler):
    """
    TimedRotatingFileHandler with optional gzip compression on rotation.
    Rotates daily at midnight and compresses the old file with gzip if enabled.
    """

    def __init__(self, filename: str, when: str = 'midnight', interval: int = 1,
                 backupCount: int = 0, encoding: str | None = None,
                 delay: bool = False, utc: bool = False, atTime=None,
                 gzip_enabled: bool = True):
        super().__init__(filename, when, interval, backupCount,
                         encoding, delay, utc, atTime)
        self.gzip_enabled = gzip_enabled
        self.suffix = "%Y-%m-%d"

    def doRollover(self):
        """Perform rollover and compress the old file if gzip is enabled."""
        if self.stream:
            self.stream.close()
            self.stream = None  # type: ignore

        currentTime = int(time.time())
        timeTuple = time.localtime(currentTime)
        dfn = self.rotation_filename(
            self.baseFilename + "." + time.strftime(self.suffix, timeTuple)
        )

        if os.path.exists(dfn):
            os.remove(dfn)

        self.rotate(self.baseFilename, dfn)

        if self.gzip_enabled and os.path.exists(dfn):
            compress_file(dfn)

        if self.backupCount > 0:
            self._delete_old_files()

        if not self.delay:
            self.stream = self._open()

        newRolloverAt = self.computeRollover(currentTime)
        while newRolloverAt <= currentTime:
            newRolloverAt = newRolloverAt + self.interval
        self.rolloverAt = newRolloverAt

    def _delete_old_files(self) -> None:
        """Delete old backup files beyond backupCount."""
        dirName, baseName = os.path.split(self.baseFilename)
        fileNames = os.listdir(dirName) if os.path.isdir(dirName) else []

        result = []
        prefix = baseName + "."
        for fileName in fileNames:
            if fileName.startswith(prefix):
                suffix = fileName[len(prefix):]
                if suffix.endswith('.gz'):
                    suffix = suffix[:-3]
                try:
                    time.strptime(suffix, self.suffix)
                    result.append(os.path.join(dirName, fileName))
                except ValueError:
                    pass

        result.sort(reverse=True)

        if len(result) > self.backupCount:
            for filepath in result[self.backupCount:]:
                try:
                    os.remove(filepath)
                except OSError:
                    pass
