from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import ANSI
import logging
import gzip
import re
import shutil
import os
import sys
import datetime
from pathlib import Path

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_owner_skip_reported = False


def apply_configured_owner(path: str | Path) -> None:
    """Apply configured mirror uid/gid ownership to one path when permitted."""
    global _owner_skip_reported

    try:
        import mirror
        conf = getattr(mirror, "conf", None)
        uid = getattr(conf, "uid", None)
        gid = getattr(conf, "gid", None)
    except Exception:
        return

    if uid is None or gid is None:
        return
    if not isinstance(uid, int) or not isinstance(gid, int):
        return

    try:
        if os.geteuid() == 0:
            os.chown(path, uid, gid, follow_symlinks=False)
            return
        if os.geteuid() == uid and (os.getegid() == gid or gid in os.getgroups()):
            return
    except OSError as exc:
        logging.getLogger("mirror").warning(f"Failed to chown {path}: {exc}")
        return

    if not _owner_skip_reported:
        logging.getLogger("mirror").debug(
            "Skipping configured ownership for logs: process is not root"
        )
        _owner_skip_reported = True


def _time_formatting(line: str, usetime: datetime.datetime, pkgid: str | None = None) -> str:
    """Format a template string with zero-padded time components.

    Args:
        line(str): Template string with placeholders like {year}, {month}, etc.
        usetime(datetime.datetime): Timestamp to format from.
        pkgid(str, optional): Package ID substituted into {packageid} placeholder.

    Return:
        formatted(str): Template with all placeholders replaced.
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
    """Compress a file with gzip and remove the original.

    Args:
        filepath(str | Path): Path to the file to compress.

    Return:
        gz_path(Path | None): Path to the .gz file, or None if compression failed.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        return None

    gzip_path = filepath.with_suffix(filepath.suffix + '.gz')

    try:
        with open(filepath, 'rb') as f_in:
            with gzip.open(gzip_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        apply_configured_owner(gzip_path)
        filepath.unlink()
        return gzip_path
    except Exception as e:
        logging.getLogger("mirror").warning(f"Failed to compress file {filepath}: {e}")
        return None


class PromptHandler(logging.StreamHandler):
    """Log handler that prints via prompt_toolkit ANSI when the terminal
    supports it, and falls back to plain text otherwise.
    """

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        if self._supports_ansi():
            print_formatted_text(ANSI(msg))
        else:
            sys.stdout.write(_ANSI_ESCAPE_RE.sub("", msg) + "\n")
            sys.stdout.flush()

    @staticmethod
    def _supports_ansi() -> bool:
        """Return True when stdout is a TTY and TERM is not 'dumb'."""
        try:
            isatty = sys.stdout.isatty()
        except Exception:
            isatty = False
        if not isatty:
            return False
        return os.environ.get("TERM", "").lower() != "dumb"


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
        apply_configured_owner(initial_path.parent)

        super().__init__(str(initial_path), encoding=encoding, delay=delay)
        apply_configured_owner(initial_path)
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

        rotated = new_path != self.current_formatted_path
        if rotated:
            self.do_rotation(new_path)

        super().emit(record)
        if rotated:
            apply_configured_owner(new_path)

    def do_rotation(self, new_path: str):
        """Close current file, optionally compress it, and open the new one."""
        old_path = self.baseFilename
        if self.stream:
            self.stream.close()
            self.stream = None

        if self.gzip_enabled and os.path.exists(old_path):
            compress_file(old_path)

        Path(new_path).parent.mkdir(parents=True, exist_ok=True)
        apply_configured_owner(Path(new_path).parent)
        self.baseFilename = new_path
        self.current_formatted_path = new_path
