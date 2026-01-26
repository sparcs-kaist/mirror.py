from __future__ import annotations

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import ANSI
from logging.handlers import TimedRotatingFileHandler
import logging
import gzip
import shutil
import os
import time


class PromptHandler(logging.StreamHandler):
    """Handler that outputs to prompt_toolkit formatted text."""

    def emit(self, record):
        msg = self.format(record)
        print_formatted_text(ANSI(msg))


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
            self.stream = None

        currentTime = int(time.time())
        timeTuple = time.localtime(currentTime)
        dfn = self.rotation_filename(
            self.baseFilename + "." + time.strftime(self.suffix, timeTuple)
        )

        if os.path.exists(dfn):
            os.remove(dfn)

        self.rotate(self.baseFilename, dfn)

        if self.gzip_enabled and os.path.exists(dfn):
            self._compress_rotated_file(dfn)

        if self.backupCount > 0:
            self._delete_old_files()

        if not self.delay:
            self.stream = self._open()

        newRolloverAt = self.computeRollover(currentTime)
        while newRolloverAt <= currentTime:
            newRolloverAt = newRolloverAt + self.interval
        self.rolloverAt = newRolloverAt

    def _compress_rotated_file(self, filepath: str) -> None:
        """Compress a rotated file with gzip and remove the original."""
        gzip_path = filepath + ".gz"
        try:
            with open(filepath, 'rb') as f_in:
                with gzip.open(gzip_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            os.remove(filepath)
        except Exception as e:
            import logging
            logging.getLogger("mirror").warning(f"Failed to compress log file {filepath}: {e}")

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
