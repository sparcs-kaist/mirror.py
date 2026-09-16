"""
Real-time mirror status TUI for mirror.py.

Full-screen prompt_toolkit application showing package sync status,
ETA to next sync, and a live tail of the selected package's running log.
Operators can start or stop syncs via a keyboard-driven confirm dialog.
Supports sort cycling (s), name filtering (/), help overlay (?),
and polling pause (p).
"""

import asyncio
import gzip
import os
import stat
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    FloatContainer,
    Float,
    HSplit,
    VSplit,
    Layout,
    Window,
)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl, UIContent
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import Frame, TextArea

import mirror.socket.master
from mirror.command.config import _resolve_master_socket
from mirror.structure import Package


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class _WidthAwareFormattedTextControl(FormattedTextControl):
    """Formatted text control whose content factory receives its render width."""

    def __init__(
        self,
        text_factory: Callable[[int], FormattedText],
        **kwargs,
    ) -> None:
        self._render_width = 1
        super().__init__(text=lambda: text_factory(self._render_width), **kwargs)

    def _get_formatted_text_cached(self) -> FormattedText:
        from prompt_toolkit.application.current import get_app

        cache_key = (get_app().render_counter, self._render_width)
        return self._fragment_cache.get(
            cache_key, lambda: to_formatted_text(self.text, self.style)
        )

    def preferred_width(self, max_available_width: int) -> int:
        self._render_width = max(1, max_available_width)
        return super().preferred_width(max_available_width)

    def create_content(self, width: int, height: Optional[int]) -> UIContent:
        self._render_width = max(1, width)
        return super().create_content(width, height)


def format_duration(seconds: float) -> str:
    """Format a positive duration as 'HH:MM:SS' or 'Nd HH:MM' if >= 1 day.

    Args:
        seconds(float): Duration in seconds.

    Return:
        formatted(str): Human-readable duration string.
    """
    if seconds <= 0:
        return "0"
    total = int(seconds)
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours:02d}:{minutes:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_datetime(epoch: float) -> str:
    """Format an epoch-seconds value as 'YYYY-MM-DD HH:MM:SS' in local time.

    Args:
        epoch(float): Epoch seconds. Non-positive values yield "-".

    Return:
        text(str): Localtime datetime string, or "-" for missing values.
    """
    if epoch <= 0:
        return "-"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def format_started(package: Package, now: float) -> str:
    """Return the localtime datetime when the current sync started, else '-'.

    Only meaningful for SYNC packages; `Package.timestamp` is set to
    `time.time() * 1000` (ms) when status flips to SYNC.

    Args:
        package(Package): Package to inspect.
        now(float): Current epoch seconds (unused; accepted for symmetry).

    Return:
        text(str): Datetime string, "-" for non-SYNC, "(unknown)" for SYNC
            without a timestamp.
    """
    if package.status != "SYNC":
        return "-"
    if not package.timestamp:
        return "(unknown)"
    return format_datetime(package.timestamp / 1000.0)


def format_elapsed(package: Package, now: float) -> str:
    """Return the running duration since the current sync started, else '-'.

    Args:
        package(Package): Package to inspect.
        now(float): Current epoch seconds.

    Return:
        text(str): Duration string for SYNC packages, "-" otherwise.
    """
    if package.status != "SYNC" or not package.timestamp:
        return "-"
    return format_duration(now - package.timestamp / 1000.0)


def format_last_success(package: Package) -> str:
    """Return the localtime datetime of the most recent successful sync.

    Args:
        package(Package): Package to inspect.

    Return:
        text(str): Datetime string, "(never)" if no successful sync.
    """
    epoch = package.statusinfo.lastsuccesstime or 0.0
    if epoch <= 0:
        return "(never)"
    return format_datetime(epoch)


def format_ago(epoch: float, now: float) -> str:
    """Return a duration string for 'how long ago' an epoch event happened.

    Args:
        epoch(float): Past epoch seconds. 0 or future values yield "-".
        now(float): Current epoch seconds.

    Return:
        text(str): Human-readable duration string, "-" if not applicable.
    """
    if epoch <= 0:
        return "-"
    delta = now - epoch
    if delta < 0:
        return "-"
    return format_duration(delta)


def status_style(status: str) -> str:
    """Map status to a prompt_toolkit class name.

    Args:
        status(str): Package status string.

    Return:
        style_class(str): prompt_toolkit style class.
    """
    mapping = {
        "SYNC": "class:status.sync",
        "ACTIVE": "class:status.active",
        "ERROR": "class:status.error",
    }
    return mapping.get(status, "class:status.unknown")


_COL_PKGID = 22
_COL_STATUS = 10
_COL_STARTED = 19
_COL_ELAPSED = 12
_COL_LAST_SUCCESS = 19
_COL_AGO = 12

# All available columns in display order.
_ALL_COLUMNS = ("PACKAGE", "STATUS", "STARTED", "ELAPSED", "LAST SUCCESS", "AGO")


def _fit_field(text: str, width: int) -> str:
    """Truncate text to exactly width, padding on the right with spaces.

    Truncation uses a trailing ".." marker when width allows so that
    over-long values do not shift later columns and the header / body
    stay byte-for-byte aligned.
    """
    if width <= 0:
        return ""

    display_width = get_cwidth(text)
    if display_width <= width:
        return text + (" " * (width - display_width))

    marker = ".." if width >= 3 else ""
    content_width = width - get_cwidth(marker)
    chars: list[str] = []
    used = 0
    for char in text:
        char_width = get_cwidth(char)
        if used + char_width > content_width:
            break
        chars.append(char)
        used += char_width
    return "".join(chars) + (" " * (content_width - used)) + marker


def _col_width(col: str) -> int:
    """Return the fixed column width for the given column name.

    Args:
        col(str): Column identifier (e.g. "PACKAGE", "STATUS").

    Return:
        width(int): Column width in characters.
    """
    widths = {
        "PACKAGE": _COL_PKGID,
        "STATUS": _COL_STATUS,
        "STARTED": _COL_STARTED,
        "ELAPSED": _COL_ELAPSED,
        "LAST SUCCESS": _COL_LAST_SUCCESS,
        "AGO": _COL_AGO,
    }
    return widths[col]


def _format_table_row(prefix: str, cells: list[tuple[str, int]]) -> str:
    """Build a single table line with consistent column widths.

    Args:
        prefix(str): Two-character row prefix (e.g. "> " for selected, "  " otherwise).
        cells(list[tuple[str, int]]): List of (text, width) pairs for each visible column.

    Return:
        line(str): Formatted row string terminated with a newline.
    """
    if not cells:
        return prefix + "\n"
    # All columns except the last get a trailing space separator.
    # The last column keeps its full padded width then terminates with newline.
    parts = [prefix]
    for i, (text, width) in enumerate(cells):
        fitted = _fit_field(text, width)
        if i < len(cells) - 1:
            parts.append(fitted + " ")
        else:
            parts.append(fitted + "\n")
    return "".join(parts)


def _visible_columns(terminal_cols: int) -> tuple[str, ...]:
    """Determine which columns to show based on terminal width.

    Args:
        terminal_cols(int): Available table pane width in terminal cells.

    Return:
        visible(tuple[str, ...]): Tuple of column names to render.
    """
    visible = list(_ALL_COLUMNS)
    for column in ("AGO", "ELAPSED", "STARTED", "LAST SUCCESS"):
        required = 2 + sum(_col_width(col) for col in visible) + len(visible) - 1
        if required <= terminal_cols:
            break
        visible.remove(column)
    return tuple(visible)


def _column_widths(visible: tuple[str, ...], available_width: Optional[int]) -> dict[str, int]:
    """Return column widths fitted to the available table pane width."""
    widths = {column: _col_width(column) for column in visible}
    if available_width is None or tuple(visible) != ("PACKAGE", "STATUS"):
        return widths

    overflow = 2 + sum(widths.values()) + len(visible) - 1 - available_width
    if overflow <= 0:
        return widths

    package_reduction = min(overflow, widths["PACKAGE"] - 8)
    widths["PACKAGE"] -= package_reduction
    overflow -= package_reduction
    status_reduction = min(overflow, widths["STATUS"] - 3)
    widths["STATUS"] -= status_reduction
    overflow -= status_reduction
    if overflow > 0:
        widths["PACKAGE"] = max(1, widths["PACKAGE"] - overflow)
    return widths


def build_table_header(
    visible: tuple[str, ...] = _ALL_COLUMNS,
    available_width: Optional[int] = None,
) -> list[tuple[str, str]]:
    """Build the column header rows shown above the package table.

    Args:
        visible(tuple[str, ...]): Ordered column names to include.
        available_width(int, optional): Available table pane width in terminal cells.

    Return:
        rows(list[tuple[str, str]]): Two (style, text) rows: the label row
            and a separator made of dashes.
    """
    widths = _column_widths(visible, available_width)
    cells = [(col, widths[col]) for col in visible]
    label_row = _format_table_row("  ", cells)
    divider_cells = [("-" * widths[col], widths[col]) for col in visible]
    divider_row = _format_table_row("  ", divider_cells)
    return [
        ("class:tableheader", label_row),
        ("class:tableheader.divider", divider_row),
    ]


def _build_status_cell(pkg: Package) -> str:
    """Build the STATUS cell text, appending error count when applicable.

    Args:
        pkg(Package): Package to format.

    Return:
        text(str): Status cell text (e.g. "ERROR x3" or "SYNC").
    """
    if pkg.status == "ERROR" and pkg.statusinfo.errorcount > 0:
        return f"ERROR x{pkg.statusinfo.errorcount}"
    return pkg.status


def build_table_rows(
    packages: list[Package],
    selected: int,
    now: float,
    visible: tuple[str, ...] = _ALL_COLUMNS,
    selected_pkgid: str = "",
    available_width: Optional[int] = None,
) -> list[tuple[str, str]]:
    """Build FormattedText rows for the table control.

    Args:
        packages(list[Package]): List of packages to render (already sorted/filtered).
        selected(int): Legacy index-based selection (ignored when selected_pkgid is set).
        now(float): Current epoch seconds.
        visible(tuple[str, ...]): Ordered column names to include.
        selected_pkgid(str): pkgid of the currently selected package.
        available_width(int, optional): Available table pane width in terminal cells.

    Return:
        rows(list[tuple[str, str]]): List of (style, text) tuples.
    """
    rows: list[tuple[str, str]] = []
    widths = _column_widths(visible, available_width)
    for idx, pkg in enumerate(packages):
        # Identity-based selection is preferred; fall back to index only when
        # selected_pkgid is not set (empty string means no identity provided).
        if selected_pkgid:
            is_selected = pkg.pkgid == selected_pkgid
        else:
            is_selected = idx == selected

        pkgid_text = pkg.pkgid
        if pkg.disabled:
            pkgid_text = pkgid_text + " (off)"

        cell_map = {
            "PACKAGE": pkgid_text,
            "STATUS": _build_status_cell(pkg),
            "STARTED": format_started(pkg, now),
            "ELAPSED": format_elapsed(pkg, now),
            "LAST SUCCESS": format_last_success(pkg),
            "AGO": format_ago(pkg.statusinfo.lastsuccesstime or 0.0, now),
        }
        cells = [(cell_map[col], widths[col]) for col in visible]
        prefix = "> " if is_selected else "  "
        line = _format_table_row(prefix, cells)

        if is_selected:
            row_style = "class:selected"
        elif pkg.disabled:
            row_style = "class:status.disabled"
        else:
            row_style = status_style(pkg.status)

        rows.append((row_style, line))
    return rows


def compute_status_counts(packages: list[Package]) -> dict:
    """Count packages by status category.

    Args:
        packages(list[Package]): List of packages to count.

    Return:
        counts(dict): Keys: "total", "SYNC", "ACTIVE", "ERROR", "UNKNOWN", "disabled".
    """
    counts = {"total": len(packages), "SYNC": 0, "ACTIVE": 0, "ERROR": 0, "UNKNOWN": 0, "disabled": 0}
    for pkg in packages:
        if pkg.disabled:
            counts["disabled"] += 1
        status = pkg.status
        if status in counts:
            counts[status] += 1
        else:
            counts["UNKNOWN"] += 1
    return counts


def apply_sort(packages: list[Package], mode: str) -> list[Package]:
    """Return a sorted copy of packages according to the given mode.

    Args:
        packages(list[Package]): Input package list.
        mode(str): One of "default", "status", "ago", "pkgid".

    Return:
        sorted_list(list[Package]): Sorted copy (input unchanged).
    """
    if mode == "default":
        return list(packages)
    if mode == "status":
        priority = {"ERROR": 0, "SYNC": 1, "ACTIVE": 2, "UNKNOWN": 3}
        return sorted(packages, key=lambda p: (priority.get(p.status, 3), p.pkgid.lower()))
    if mode == "ago":
        # Smallest lastsuccesstime first (never synced → 0.0 → sorts first)
        return sorted(packages, key=lambda p: (p.statusinfo.lastsuccesstime or 0.0))
    if mode == "pkgid":
        return sorted(packages, key=lambda p: p.pkgid.lower())
    return list(packages)


def apply_filter(packages: list[Package], needle: str) -> list[Package]:
    """Return packages whose pkgid contains needle (case-insensitive).

    Args:
        packages(list[Package]): Input package list.
        needle(str): Substring to match. Empty string returns all packages.

    Return:
        filtered(list[Package]): Matching packages in original order.
    """
    if not needle:
        return list(packages)
    lower = needle.lower()
    return [p for p in packages if lower in p.pkgid.lower()]


def visible_packages(state: "TUIState") -> list[Package]:
    """Return the sorted and filtered package list for the current state.

    Args:
        state(TUIState): Current TUI state.

    Return:
        packages(list[Package]): Sorted and filtered package list.
    """
    return apply_filter(apply_sort(state.packages, state.sort_mode), state.filter_text)


def build_help_text() -> FormattedText:
    """Build the key bindings help overlay content.

    Return:
        text(FormattedText): Formatted help table.
    """
    lines = [
        ("class:tableheader", "  Key        Action\n"),
        ("class:tableheader.divider", "  ---------- ----------------------------------\n"),
        ("", "  j / k      Move in focused pane\n"),
        ("", "  g / G      First / last package or whole log\n"),
        ("", "  Home/End   First / last package or whole log\n"),
        ("", "  PgUp/PgDn  Move through the focused log\n"),
        ("", "  x          Start or stop sync for selection\n"),
        ("", "  r          Refresh display\n"),
        ("", "  l          Toggle log pane\n"),
        ("", "  Tab        Toggle focus between table and log\n"),
        ("", "  s          Cycle sort mode\n"),
        ("", "  /          Enter filter mode\n"),
        ("", "  p          Toggle polling pause\n"),
        ("", "  ?          Toggle this help overlay\n"),
        ("", "  q / Ctrl-C Quit\n"),
    ]
    return FormattedText(lines)


def _modal_active(state: "TUIState") -> bool:
    """Return True if any modal overlay is currently active.

    Args:
        state(TUIState): Current TUI state.

    Return:
        active(bool): True when dialog, help, or filter input is open.
    """
    return (
        state.dialog is not None
        or state.show_help
        or state.filter_input_active
    )


def _fallback_package_from_dict(raw: dict) -> Package:
    """Build a minimal Package from raw RPC dict when from_dict rejects it.

    Used when Package.from_dict raises ValueError due to an unknown synctype
    that the TUI process cannot resolve (plugins not loaded). Builds the bare
    minimum the TUI needs to render the row and tail its log.

    Args:
        raw(dict): Raw package dict from the RPC payload.

    Return:
        package(Package): Minimal Package instance for display purposes.
    """
    from mirror.structure import PackageSettings

    status_obj = raw.get("status", "UNKNOWN")
    if isinstance(status_obj, dict):
        status = status_obj.get("status", "UNKNOWN")
        info = Package.StatusInfo.from_dict(status_obj.get("statusinfo", {}))
    else:
        status = str(status_obj)
        info = Package.StatusInfo.from_dict(raw.get("statusinfo", {}))

    settings = PackageSettings(hidden=False, src="", dst="")
    return Package(
        pkgid=raw["id"],
        name=raw.get("name", raw["id"]),
        status=status,
        href=raw.get("href", ""),
        synctype=raw.get("synctype", "unknown"),
        syncrate=0,
        link=[],
        settings=settings,
        lastsync=float(raw.get("lastsync", 0.0)),
        disabled=bool(raw.get("disabled", False)),
        timestamp=float(raw.get("timestamp", 0.0)),
        statusinfo=info,
    )


def packages_from_rpc(payload: dict) -> list[Package]:
    """Rehydrate Package objects from a list_packages RPC response.

    The RPC returns {"packages": [pkg.to_dict(), ...]} where each dict is
    in stat format. Package.from_dict accepts that exact shape (it reads
    status as either str or dict).

    Args:
        payload(dict): RPC response from MasterClient.list_packages().

    Return:
        packages(list[Package]): Rehydrated Package instances.
    """
    result = []
    for raw in payload.get("packages", []):
        try:
            result.append(Package.from_dict(raw))
        except ValueError:
            # Unknown synctype (e.g. plugin-provided): build a fallback so the
            # row still appears in the table with correct status/log path.
            try:
                result.append(_fallback_package_from_dict(raw))
            except Exception:
                pass
        except Exception:
            pass
    return result


def safe_open_log_for_read(path: Path, base: Optional[Path]) -> Optional[int]:
    """O_RDONLY|O_NOFOLLOW open with regular-file and base-path checks.

    Returns the opened fd, or None on any rejection. base is the package
    log root from config.json (settings.logger.packagefileformat.base) when
    available; if None, the base check is skipped but symlink and
    regular-file checks still apply.

    Args:
        path(Path): Path to the log file to open.
        base(Path, optional): Package log base directory for path traversal check.

    Return:
        fd(int, optional): Opened file descriptor, or None on rejection.
    """
    # Defense-in-depth: reject symlinks before open
    if path.is_symlink():
        return None

    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            return None

        if base is not None:
            try:
                path.resolve(strict=False).relative_to(
                    Path(base).resolve(strict=False)
                )
            except (ValueError, OSError):
                os.close(fd)
                return None
    except OSError:
        os.close(fd)
        return None

    return fd


def latest_completed_log(pkg: Package) -> Optional[Path]:
    """Return the package's most recent completed (success/error) log path.

    Picks whichever of lastsuccesslog / lasterrorlog carries the newer
    timestamp. Returns None when the package has no completed log recorded.

    Args:
        pkg(Package): Package whose status logs to inspect.

    Return:
        path(Path, optional): Newest completed log path, or None if none.
    """
    info = pkg.statusinfo
    candidates: list[tuple[float, str]] = []
    if info.lastsuccesslog:
        candidates.append((info.lastsuccesstime, info.lastsuccesslog))
    if info.lasterrorlog:
        candidates.append((info.lasterrortime, info.lasterrorlog))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    return Path(candidates[0][1])


# Windowed log loading: lines loaded initially, per page-up, and the caps that
# bound the in-memory window while following / when scrolled up.
LOG_INITIAL_LINES = 1000
LOG_PAGE_LINES = 1000
LOG_FOLLOW_MAX_LINES = 5000
LOG_MAX_LOADED_LINES = 50000
LOG_READ_BLOCK_BYTES = 64 * 1024
LOG_MAX_LOADED_BYTES = 8 * 1024 * 1024


def _front_cut_offset(data: bytes, drop_lines: int) -> int:
    """Return the byte offset just past the first drop_lines newlines in data.

    Args:
        data(bytes): Buffer to scan from the front.
        drop_lines(int): Number of leading lines to drop.

    Return:
        offset(int): Byte offset where the retained content begins.
    """
    idx = 0
    for _ in range(drop_lines):
        nl = data.find(b"\n", idx)
        if nl == -1:
            return len(data)
        idx = nl + 1
    return idx


class _LogCancelled(Exception):
    """Raised when a background log read is cancelled."""


@dataclass(frozen=True)
class _LogSnapshot:
    """A bounded byte window into a log file."""

    data: bytes
    start: int
    end: int
    size: int
    more_above: bool
    more_below: bool
    reset: bool
    segmented: bool


class _LogReader:
    """Read a bounded, pageable window from plain or gzip log files.

    Instances are deliberately stateful and must be used by one worker thread.
    Gzip sources are streamed into a private temporary file so paging has the
    same byte-offset semantics as a plain log without retaining the full log in
    memory.
    """

    def __init__(self) -> None:
        self._path: Optional[Path] = None
        self._base: Optional[Path] = None
        self._live = False
        self._fd: Optional[int] = None
        self._temporary = None
        self._source_identity: Optional[tuple[int, int]] = None
        self._source_size = 0
        self._data = b""
        self._start = 0
        self._end = 0
        self._segmented = False

    @staticmethod
    def _cancelled(cancel: "threading.Event") -> None:
        if cancel.is_set():
            raise _LogCancelled()

    @staticmethod
    def _read_range(
        fd: int, start: int, end: int, cancel: "threading.Event"
    ) -> bytes:
        """Read a caller-bounded byte range in fixed-size chunks."""
        if end <= start:
            return b""
        os.lseek(fd, start, os.SEEK_SET)
        remaining = min(end - start, LOG_MAX_LOADED_BYTES)
        output = bytearray()
        while remaining:
            _LogReader._cancelled(cancel)
            chunk = os.read(fd, min(LOG_READ_BLOCK_BYTES, remaining))
            if not chunk:
                break
            output.extend(chunk)
            remaining -= len(chunk)
        return bytes(output)

    @staticmethod
    def _align_start(
        fd: int, start: int, end: int, cancel: "threading.Event"
    ) -> int:
        """Advance a byte cap past UTF-8 continuation bytes."""
        original = start
        for _ in range(4):
            if start >= end:
                return start
            _LogReader._cancelled(cancel)
            os.lseek(fd, start, os.SEEK_SET)
            byte = os.read(fd, 1)
            if not byte or byte[0] & 0xC0 != 0x80:
                return start
            start += 1
        return original

    @staticmethod
    def _align_end(
        fd: int,
        start: int,
        end: int,
        size: int,
        cancel: "threading.Event",
    ) -> int:
        """Retreat a byte cap to a UTF-8 codepoint boundary."""
        if end >= size:
            return end
        original = end
        for _ in range(4):
            if end <= start:
                return end
            _LogReader._cancelled(cancel)
            os.lseek(fd, end, os.SEEK_SET)
            byte = os.read(fd, 1)
            if not byte or byte[0] & 0xC0 != 0x80:
                return end
            end -= 1
        return original

    @staticmethod
    def _backward_start(
        fd: int, end: int, lines: int, cancel: "threading.Event"
    ) -> tuple[int, bool]:
        """Find a page start, stopping at the raw-byte window limit."""
        if end <= 0:
            return 0, False
        lower = max(0, end - LOG_MAX_LOADED_BYTES)
        pos = end
        newlines = 0
        while pos > lower:
            _LogReader._cancelled(cancel)
            size = min(LOG_READ_BLOCK_BYTES, pos - lower)
            pos -= size
            os.lseek(fd, pos, os.SEEK_SET)
            chunk = os.read(fd, size)
            for index in range(len(chunk) - 1, -1, -1):
                absolute = pos + index
                if chunk[index] == 0x0A and absolute != end - 1:
                    newlines += 1
                    if newlines == lines:
                        return absolute + 1, False
        aligned = _LogReader._align_start(fd, lower, end, cancel)
        return aligned, lower > 0

    @staticmethod
    def _forward_end(
        fd: int, start: int, size: int, lines: int, cancel: "threading.Event"
    ) -> tuple[int, bool]:
        """Find a page end, stopping at the raw-byte window limit."""
        upper = min(size, start + LOG_MAX_LOADED_BYTES)
        pos = start
        newlines = 0
        while pos < upper:
            _LogReader._cancelled(cancel)
            amount = min(LOG_READ_BLOCK_BYTES, upper - pos)
            os.lseek(fd, pos, os.SEEK_SET)
            chunk = os.read(fd, amount)
            if not chunk:
                return pos, False
            for index, byte in enumerate(chunk):
                if byte == 0x0A:
                    newlines += 1
                    if newlines == lines:
                        return pos + index + 1, False
            pos += len(chunk)
        aligned = _LogReader._align_end(fd, start, upper, size, cancel)
        return aligned, upper < size

    @staticmethod
    def _line_count(data: bytes) -> int:
        """Count complete lines plus a final unterminated line."""
        return data.count(b"\n") + int(bool(data) and not data.endswith(b"\n"))

    @staticmethod
    def _trim_front(
        data: bytes, start: int, max_lines: int
    ) -> tuple[bytes, int, bool]:
        """Apply line and byte caps, discarding from the oldest edge."""
        segmented = False
        excess = max(0, _LogReader._line_count(data) - max_lines)
        cut = _front_cut_offset(data, excess) if excess else 0
        if len(data) - cut > LOG_MAX_LOADED_BYTES:
            cut = len(data) - LOG_MAX_LOADED_BYTES
            original = cut
            moves = 0
            while (
                moves < 3
                and cut < len(data)
                and data[cut] & 0xC0 == 0x80
            ):
                cut += 1
                moves += 1
            if cut < len(data) and data[cut] & 0xC0 == 0x80:
                cut = original
            segmented = True
        return data[cut:], start + cut, segmented

    @staticmethod
    def _trim_back(
        data: bytes, start: int, max_lines: int
    ) -> tuple[bytes, int, bool]:
        """Apply line and byte caps, discarding from the newest edge."""
        segmented = False
        if _LogReader._line_count(data) > max_lines:
            cursor = 0
            for _ in range(max_lines):
                cursor = data.find(b"\n", cursor) + 1
            data = data[:cursor]
        if len(data) > LOG_MAX_LOADED_BYTES:
            cut = LOG_MAX_LOADED_BYTES
            original = cut
            moves = 0
            while moves < 3 and cut > 0 and data[cut] & 0xC0 == 0x80:
                cut -= 1
                moves += 1
            if data[cut] & 0xC0 == 0x80:
                cut = original
            data = data[:cut]
            segmented = True
        return data, start + len(data), segmented

    def _close_backing(self) -> None:
        if self._temporary is not None:
            self._temporary.close()
        elif self._fd is not None:
            os.close(self._fd)
        self._fd = None
        self._temporary = None

    def close(self) -> None:
        """Close the current source and discard its paging state."""
        self._close_backing()
        self._path = None
        self._base = None
        self._source_identity = None
        self._source_size = 0
        self._data = b""
        self._start = 0
        self._end = 0
        self._segmented = False

    def _open(
        self,
        path: Path,
        base: Optional[Path],
        live: bool,
        cancel: "threading.Event",
    ) -> None:
        """Open and validate a source, preparing seekable gzip backing."""
        import errno
        import tempfile

        self.close()
        self._cancelled(cancel)
        source_fd = safe_open_log_for_read(path, base)
        if source_fd is None:
            raise OSError(errno.EACCES, "log file rejected", str(path))
        try:
            source_stat = os.fstat(source_fd)
        except BaseException:
            os.close(source_fd)
            raise
        self._path = path
        self._base = base
        self._live = live
        self._source_identity = (source_stat.st_dev, source_stat.st_ino)
        self._source_size = source_stat.st_size

        if path.suffix != ".gz":
            self._fd = source_fd
            return

        temporary = None
        try:
            temporary = tempfile.TemporaryFile(mode="w+b")
            with os.fdopen(source_fd, "rb", closefd=True) as raw:
                with gzip.GzipFile(fileobj=raw, mode="rb") as compressed:
                    while True:
                        self._cancelled(cancel)
                        chunk = compressed.read(LOG_READ_BLOCK_BYTES)
                        if not chunk:
                            break
                        temporary.write(chunk)
            temporary.flush()
            self._temporary = temporary
            self._fd = temporary.fileno()
        except BaseException:
            if temporary is not None:
                temporary.close()
            else:
                os.close(source_fd)
            self.close()
            raise

    def _source_changed(self, path: Path) -> bool:
        """Return whether the on-disk source must be reopened."""
        current = path.stat()
        identity = (current.st_dev, current.st_ino)
        if identity != self._source_identity:
            return True
        if current.st_size < self._source_size:
            return True
        if path.suffix == ".gz" and current.st_size != self._source_size:
            return True
        self._source_size = current.st_size
        return False

    def _load_tail(self, size: int, cancel: "threading.Event") -> None:
        assert self._fd is not None
        start, segmented = self._backward_start(
            self._fd, size, LOG_INITIAL_LINES, cancel
        )
        self._data = self._read_range(self._fd, start, size, cancel)
        self._start = start
        self._end = start + len(self._data)
        self._segmented = segmented

    def _load_start(self, size: int, cancel: "threading.Event") -> None:
        assert self._fd is not None
        end, segmented = self._forward_end(
            self._fd, 0, size, LOG_INITIAL_LINES, cancel
        )
        self._data = self._read_range(self._fd, 0, end, cancel)
        self._start = 0
        self._end = len(self._data)
        self._segmented = segmented

    def _read_unchecked(
        self,
        path: Path,
        base: Optional[Path],
        live: bool,
        action: str,
        cancel: "threading.Event",
        following: bool = True,
    ) -> _LogSnapshot:
        """Read or move the bounded window for ``path``."""
        if action not in {"tail", "start", "up", "down", "poll"}:
            raise ValueError(f"unknown log reader action: {action}")
        self._cancelled(cancel)

        reset = False
        source_key_changed = (
            self._fd is None
            or path != self._path
            or base != self._base
            or live != self._live
        )
        if source_key_changed:
            self._open(path, base, live, cancel)
            reset = True
        else:
            if self._source_changed(path):
                self._open(path, base, live, cancel)
                reset = True

        assert self._fd is not None
        size = os.fstat(self._fd).st_size
        max_lines = (
            LOG_FOLLOW_MAX_LINES if live and following else LOG_MAX_LOADED_LINES
        )

        effective_action = action
        if reset and action in {"up", "down", "poll"}:
            effective_action = "tail" if live and following else "start"

        if effective_action == "tail":
            self._load_tail(size, cancel)
        elif effective_action == "start":
            self._load_start(size, cancel)
        elif effective_action == "up" and self._start > 0:
            page_start, segmented = self._backward_start(
                self._fd, self._start, LOG_PAGE_LINES, cancel
            )
            prefix = self._read_range(self._fd, page_start, self._start, cancel)
            if len(prefix) != self._start - page_start:
                raise OSError("log changed during backward read")
            merged = prefix + self._data
            merged, new_end, capped = self._trim_back(
                merged, page_start, max_lines
            )
            self._data = merged
            self._start = page_start
            self._end = new_end
            self._segmented = segmented or capped
        elif effective_action == "down" and self._end < size:
            old_end = self._end
            page_end, segmented = self._forward_end(
                self._fd, old_end, size, LOG_PAGE_LINES, cancel
            )
            suffix = self._read_range(self._fd, old_end, page_end, cancel)
            merged, new_start, capped = self._trim_front(
                self._data + suffix, self._start, max_lines
            )
            self._data = merged
            self._start = new_start
            self._end = self._start + len(self._data)
            self._segmented = segmented or capped
        elif effective_action == "poll" and live and following and size > self._end:
            growth = size - self._end
            if growth > LOG_MAX_LOADED_BYTES:
                self._load_tail(size, cancel)
            else:
                suffix = self._read_range(self._fd, self._end, size, cancel)
                merged, new_start, capped = self._trim_front(
                    self._data + suffix, self._start, max_lines
                )
                self._data = merged
                self._start = new_start
                self._end = self._start + len(self._data)
                self._segmented = capped

        size = os.fstat(self._fd).st_size
        if size < self._end:
            raise OSError("log truncated during read")

        return _LogSnapshot(
            data=self._data,
            start=self._start,
            end=self._end,
            size=size,
            more_above=self._start > 0,
            more_below=self._end < size,
            reset=reset,
            segmented=self._segmented,
        )

    def read(
        self,
        path: Path,
        base: Optional[Path],
        live: bool,
        action: str,
        cancel: "threading.Event",
        following: bool = True,
    ) -> _LogSnapshot:
        """Read a window, closing the source if reading fails or is cancelled."""
        try:
            return self._read_unchecked(
                path, base, live, action, cancel, following
            )
        except BaseException:
            self.close()
            raise


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class ConfirmDialog:
    """State for the modal confirm dialog."""
    action: str           # "start" or "stop"
    pkgid: str
    selected: int = 0     # 0 = Yes, 1 = No


_SORT_CYCLE = ("default", "status", "ago", "pkgid")


@dataclass
class TUIState:
    """Mutable application state shared between UI and background tasks."""
    packages: list[Package] = field(default_factory=list)
    selected: int = 0
    connected: bool = False
    last_poll_error: Optional[str] = None
    toast: Optional[tuple[float, str, str]] = None  # (expires_at, class, text)
    dialog: Optional[ConfirmDialog] = None
    show_log: bool = True
    log_tail_path: Optional[Path] = None
    # True while live-tailing a running log; False when showing a static last log
    log_tail_live: bool = False
    # Windowed-loading state (see MirrorTUI log paging methods)
    log_more_above: bool = False  # True when older lines exist above the window
    log_more_below: bool = False
    log_message: str = ""
    log_following: bool = True
    # Identity-based selection (replaces index-based selected as source of truth)
    selected_pkgid: str = ""
    sort_mode: str = "default"
    filter_text: str = ""
    filter_input_active: bool = False
    show_help: bool = False
    paused: bool = False

    def _set_toast(self, style_class: str, text: str) -> None:
        self.toast = (time.time() + 3.0, style_class, text)

    def open_dialog(self, action: str, pkgid: str) -> None:
        """Open the confirm dialog for the given action and package.

        Args:
            action(str): "start" or "stop".
            pkgid(str): Package identifier.
        """
        self.dialog = ConfirmDialog(action=action, pkgid=pkgid)

    def cancel_dialog(self) -> None:
        """Close the confirm dialog without performing any action."""
        self.dialog = None

    async def confirm_dialog_async(
        self, client: mirror.socket.master.MasterClient, app: "Application"
    ) -> None:
        """Execute the confirmed action in a thread and flash a toast.

        Runs the blocking RPC call in a thread pool via asyncio.to_thread so
        the event loop (and TUI) remain responsive during the socket round-trip.

        Args:
            client(MasterClient): Connected master client.
            app(Application): Running application (to invalidate after update).
        """
        if self.dialog is None:
            return
        action = self.dialog.action
        pkgid = self.dialog.pkgid
        self.dialog = None
        try:
            if action == "start":
                result = await asyncio.to_thread(client.start_sync, pkgid)
            else:
                result = await asyncio.to_thread(client.stop_sync, pkgid)
            status = result.get("status", "unknown")
            self._set_toast(
                "class:success",
                f"[OK] Manual sync {action}ed for '{pkgid}' -> {status}",
            )
        except Exception as exc:
            self._set_toast(
                "class:error",
                f"[ERROR] {action} '{pkgid}' failed: {exc}",
            )
        app.invalidate()

    def confirm_dialog(self, client: mirror.socket.master.MasterClient) -> None:
        """Execute the confirmed action and flash a toast.

        Args:
            client(MasterClient): Connected master client.
        """
        if self.dialog is None:
            return
        action = self.dialog.action
        pkgid = self.dialog.pkgid
        self.dialog = None
        try:
            if action == "start":
                result = client.start_sync(pkgid)
            else:
                result = client.stop_sync(pkgid)
            status = result.get("status", "unknown")
            self._set_toast(
                "class:success",
                f"[OK] Manual sync {action}ed for '{pkgid}' -> {status}",
            )
        except Exception as exc:
            self._set_toast(
                "class:error",
                f"[ERROR] {action} '{pkgid}' failed: {exc}",
            )

    def toggle_log(self) -> None:
        """Toggle the log pane visibility and emit a toast."""
        self.show_log = not self.show_log
        state_label = "on" if self.show_log else "off"
        self._set_toast("class:success", f"Toggle show log: {state_label}")
        if self.show_log:
            # Force a fresh tail reload (drops any stale window) on re-open.
            self.log_tail_path = None
            self.log_tail_live = False
            self.log_more_above = False
            self.log_more_below = False

    def current_package(self) -> Optional[Package]:
        """Return the currently selected package, or None if list is empty.

        Uses identity-based selection (selected_pkgid) over the visible list.

        Return:
            package(Package, optional): Selected package or None.
        """
        visible = visible_packages(self)
        if not visible:
            return None
        return next((p for p in visible if p.pkgid == self.selected_pkgid), None)

    def fix_selection(self) -> None:
        """Ensure selected_pkgid refers to a package in the visible list.

        If the current pkgid is absent from the visible list, fall back to
        the first visible package, or empty string when the list is empty.
        """
        visible = visible_packages(self)
        if not visible:
            self.selected_pkgid = ""
            return
        ids = [p.pkgid for p in visible]
        if self.selected_pkgid not in ids:
            self.selected_pkgid = ids[0]


# ---------------------------------------------------------------------------
# MirrorTUI class
# ---------------------------------------------------------------------------


_STYLE = Style.from_dict(
    {
        "header": "bold",
        "statusbar": "fg:ansibrightblack",
        "statusbar.paused": "fg:ansired bold",
        "footer": "fg:ansigray",
        "selected": "bg:ansiblue fg:ansiwhite bold",
        "status.sync": "fg:ansiyellow bold",
        "status.active": "fg:ansigreen",
        "status.error": "fg:ansired bold",
        "status.unknown": "fg:ansigray",
        "status.disabled": "fg:ansibrightblack italic",
        "tableheader": "fg:ansibrightcyan bold",
        "tableheader.divider": "fg:ansibrightblack",
        "dialog": "bg:ansidarkgray fg:ansiwhite",
        "error": "fg:ansired bold",
        "success": "fg:ansigreen",
        "warning": "fg:ansiyellow",
    }
)


class MirrorTUI:
    """Full-screen real-time TUI for mirror daemon status.

    Args:
        socket_path(str): Path to the master daemon Unix socket.
        mirrorname(str): Mirror name from config (shown in header).
        log_base(Path, optional): Package log base directory for safe open.
    """

    def __init__(
        self,
        socket_path: str,
        mirrorname: str = "",
        log_base: Optional[Path] = None,
    ) -> None:
        self._socket_path = socket_path
        self._mirrorname = mirrorname
        self._log_base = log_base
        self._daemon_started_at: float = 0.0
        self._localtimezone: str = ""
        self._state = TUIState()
        self._client: Optional[mirror.socket.master.MasterClient] = None
        self._log_reader = _LogReader()
        self._log_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mirror-tui-log"
        )
        self._log_cancel = threading.Event()
        self._log_generation = 0
        self._log_target: Optional[tuple[str, Path, bool, Optional[Path]]] = None
        self._log_snapshot: Optional[_LogSnapshot] = None
        self._log_jump: Optional[str] = None
        self._log_page_position: Optional[int] = None
        self._log_area: Optional[TextArea] = None
        self._app: Optional[Application] = None
        self._filter_buffer = Buffer(name="filter_input")

    def _apply_runtime_info(self, info: Optional[dict]) -> None:
        """Update mirrorname, log_base, daemon_started_at, and localtimezone.

        Args:
            info(dict, optional): Dict returned by get_runtime_info RPC. None is a no-op.
        """
        if info is None:
            return
        self._mirrorname = info.get("mirrorname", "") or ""
        lb = info.get("log_base")
        self._log_base = Path(lb) if lb else None
        started = info.get("daemon_started_at")
        if started is not None:
            self._daemon_started_at = float(started)
        tz = info.get("localtimezone")
        if tz:
            self._localtimezone = str(tz)

    def _build_layout(self) -> tuple[Layout, TextArea]:
        """Build the prompt_toolkit layout and return (layout, log_area).

        Return:
            layout(Layout): Application layout.
            log_area(TextArea): Log tail text area for external reference.
        """
        state = self._state

        # --- Header (identity line) ---
        def get_header() -> FormattedText:
            name_part = f" {self._mirrorname}" if self._mirrorname else ""
            conn_style = "class:success" if state.connected else "class:error"
            conn_text = "connected" if state.connected else "disconnected"
            if state.last_poll_error and not state.connected:
                conn_text += f" ({state.last_poll_error})"
            ts = time.strftime("%H:%M:%S")
            parts: list[tuple[str, str]] = [
                ("class:header", f"mirror tui{name_part}  "),
                (conn_style, f"[{conn_text}]"),
                ("class:header", f"  {ts}"),
            ]
            if self._localtimezone:
                parts.append(("class:header", f"  TZ {self._localtimezone}"))
            return FormattedText(parts)

        header = Window(
            content=FormattedTextControl(get_header),
            height=1,
        )

        # --- Status bar (counts + sort/pause/filter indicators) ---
        def get_statusbar() -> FormattedText:
            pkgs = state.packages
            counts = compute_status_counts(pkgs)
            now = time.time()
            uptime = ""
            if self._daemon_started_at > 0:
                uptime = f"  up {format_duration(now - self._daemon_started_at)}"

            count_text = (
                f" total:{counts['total']}"
                f"  SYNC:{counts['SYNC']}"
                f"  ACTIVE:{counts['ACTIVE']}"
                f"  ERROR:{counts['ERROR']}"
                f"  UNKNOWN:{counts['UNKNOWN']}"
                f"  off:{counts['disabled']}"
                f"{uptime}"
                f"  sort:{state.sort_mode}"
            )
            parts: list[tuple[str, str]] = []
            if state.paused:
                parts.append(("class:statusbar.paused", " [PAUSED]"))
            if state.filter_text:
                parts.append(("class:statusbar", f"  filter:{state.filter_text}"))
            parts.append(("class:statusbar", count_text))
            return FormattedText(parts)

        statusbar = Window(
            content=FormattedTextControl(get_statusbar),
            height=1,
        )

        # --- Table ---
        def get_toast() -> FormattedText:
            if state.toast:
                expires_at, cls, text = state.toast
                if time.time() < expires_at:
                    return FormattedText([(cls, text)])
                state.toast = None
            return FormattedText([])

        def toast_visible() -> bool:
            if state.toast is None:
                return False
            if time.time() >= state.toast[0]:
                state.toast = None
                return False
            return True

        toast_win = ConditionalContainer(
            content=Window(content=FormattedTextControl(get_toast), height=1),
            filter=Condition(toast_visible),
        )

        def get_table_header(width: int) -> FormattedText:
            return FormattedText(
                build_table_header(_visible_columns(width), width)
            )

        table_header_control = _WidthAwareFormattedTextControl(get_table_header)
        table_header_win = Window(
            content=table_header_control,
            height=2,
            wrap_lines=False,
        )

        def get_table_body(width: int) -> FormattedText:
            now = time.time()
            vis_cols = _visible_columns(width)
            vis_pkgs = visible_packages(state)
            if not vis_pkgs:
                return FormattedText(
                    [("class:status.unknown", "  (no packages)\n")]
                )
            return FormattedText(
                build_table_rows(
                    vis_pkgs,
                    state.selected,
                    now,
                    vis_cols,
                    state.selected_pkgid,
                    width,
                )
            )

        def get_table_cursor_position() -> Point:
            packages = visible_packages(state)
            index = next(
                (
                    index
                    for index, package in enumerate(packages)
                    if package.pkgid == state.selected_pkgid
                ),
                0,
            )
            return Point(x=0, y=index)

        table_control = _WidthAwareFormattedTextControl(
            get_table_body,
            focusable=True,
            get_cursor_position=get_table_cursor_position,
        )
        table_win = Window(
            content=table_control,
            wrap_lines=False,
            width=Dimension(weight=1, preferred=1),
            always_hide_cursor=True,
        )
        table_container = Frame(
            body=HSplit([toast_win, table_header_win, table_win]),
            title="Packages",
        )

        # --- Filter input bar (shown when filter_input_active) ---
        def filter_changed(_buffer: Buffer) -> None:
            old_pkgid = state.selected_pkgid
            state.filter_text = self._filter_buffer.text
            state.fix_selection()
            if state.selected_pkgid != old_pkgid:
                self._on_selection_change()

        self._filter_buffer.on_text_changed += filter_changed
        filter_control = BufferControl(buffer=self._filter_buffer, focusable=True)

        filter_win = ConditionalContainer(
            content=VSplit(
                [
                    Window(
                        content=FormattedTextControl(
                            FormattedText([("class:statusbar", " Filter: ")])
                        ),
                        width=9,
                    ),
                    Window(content=filter_control, height=1),
                ],
                height=1,
            ),
            filter=Condition(lambda: state.filter_input_active),
        )

        # --- Log pane ---
        log_area = TextArea(
            text="",
            read_only=True,
            scrollbar=True,
            focusable=True,
            wrap_lines=False,
            width=Dimension(weight=1, preferred=1),
        )

        def get_log_subtitle() -> FormattedText:
            pkg = state.current_package()
            if pkg is None:
                return FormattedText([("class:header", " (no package selected) ")])
            message = state.log_message
            above = " ↑more" if state.log_more_above else ""
            below = " ↓more" if state.log_more_below else ""
            more = above + below
            if message:
                return FormattedText(
                    [("class:header", f" {pkg.pkgid}  {message}{more} ")]
                )
            if state.log_tail_path is None:
                return FormattedText([("class:header", f" {pkg.pkgid}  (idle) ")])
            if not state.log_tail_live:
                return FormattedText([("class:header", f" {pkg.pkgid}  (last log){more} ")])
            follow = "on" if state.log_following else "off"
            return FormattedText(
                [("class:header", f" {pkg.pkgid}  Follow: {follow}{more} ")]
            )

        log_subtitle_win = Window(
            content=FormattedTextControl(get_log_subtitle),
            height=1,
            width=Dimension(weight=1, preferred=1),
        )
        log_container = ConditionalContainer(
            content=Frame(
                body=HSplit([log_subtitle_win, log_area]),
                title="Log",
            ),
            filter=Condition(lambda: state.show_log),
        )

        # --- Footer ---
        def get_footer() -> FormattedText:
            return FormattedText(
                [
                    (
                        "class:footer",
                        "j/k: move  x: start/stop  l: log  r: refresh  s: sort  /: filter  p: pause  ?: help  q: quit",
                    )
                ]
            )

        footer = Window(content=FormattedTextControl(get_footer), height=1)

        # --- Dialog float ---
        def get_dialog() -> FormattedText:
            dlg = state.dialog
            if dlg is None:
                return FormattedText([])
            verb = "Stop" if dlg.action == "stop" else "Start"
            yes_style = "class:selected" if dlg.selected == 0 else "class:dialog"
            no_style = "class:selected" if dlg.selected == 1 else "class:dialog"
            lines = [
                ("class:dialog", f" {verb} sync for '{dlg.pkgid}'?\n\n"),
                (yes_style, "  [ Yes ]  "),
                ("class:dialog", "  "),
                (no_style, "  [ No ]  "),
                ("class:dialog", "\n"),
            ]
            return FormattedText(lines)

        dialog_win = ConditionalContainer(
            content=Frame(
                body=Window(content=FormattedTextControl(get_dialog), width=40, height=5),
                style="class:dialog",
            ),
            filter=Condition(lambda: state.dialog is not None),
        )

        # --- Help overlay float ---
        help_win = ConditionalContainer(
            content=Frame(
                body=Window(
                    content=FormattedTextControl(build_help_text),
                    width=50,
                ),
                title="Key bindings",
            ),
            filter=Condition(lambda: state.show_help),
        )

        body = VSplit(
            [
                HSplit([table_container, filter_win]),
                log_container,
            ]
        )

        root = FloatContainer(
            content=HSplit([header, statusbar, body, footer]),
            floats=[
                Float(content=dialog_win, xcursor=True, ycursor=True),
                Float(content=help_win, xcursor=True, ycursor=True),
            ],
        )

        self._table_window = table_win
        self._table_control = table_control
        self._table_header_control = table_header_control
        self._filter_control = filter_control
        self._log_area = log_area
        return Layout(root, focused_element=table_control), log_area

    def _build_keybindings(self, log_area: TextArea) -> KeyBindings:
        """Build keybindings for the application.

        Args:
            log_area(TextArea): Log text area (used for End/G re-follow).

        Return:
            kb(KeyBindings): Configured key bindings.
        """
        from prompt_toolkit.document import Document
        from prompt_toolkit.filters import has_focus
        from prompt_toolkit.key_binding.bindings.scroll import (
            scroll_page_down,
            scroll_page_up,
        )

        kb = KeyBindings()
        state = self._state
        no_modal = Condition(lambda: not _modal_active(state))
        dialog_active = Condition(lambda: state.dialog is not None)
        help_active = Condition(lambda: state.show_help)
        filter_active = Condition(lambda: state.filter_input_active)
        overlay_active = dialog_active | help_active
        table_focused = has_focus(self._table_control)
        log_focused = has_focus(log_area)

        def change_selection(offset: int) -> None:
            packages = visible_packages(state)
            if not packages:
                return
            ids = [package.pkgid for package in packages]
            try:
                old_index = ids.index(state.selected_pkgid)
            except ValueError:
                old_index = 0
            new_index = min(max(old_index + offset, 0), len(ids) - 1)
            new_pkgid = ids[new_index]
            if new_pkgid != state.selected_pkgid:
                state.selected_pkgid = new_pkgid
                self._on_selection_change()

        def restore_filter_focus(event) -> None:
            control = getattr(self, "_focus_before_filter", self._table_control)
            try:
                event.app.layout.focus(control)
            except ValueError:
                event.app.layout.focus(self._table_control)

        def _guard_overlay_navigation(event) -> None:
            pass

        for key in (
            "up",
            "down",
            "left",
            "right",
            "pageup",
            "pagedown",
            "home",
            "end",
            "tab",
            "j",
            "k",
            "g",
            "G",
        ):
            kb.add(key, filter=overlay_active, eager=True)(
                _guard_overlay_navigation
            )

        @kb.add("q", filter=no_modal)
        @kb.add("c-c", filter=no_modal)
        def _quit(event) -> None:
            event.app.exit()

        @kb.add("j", filter=no_modal & table_focused)
        @kb.add("down", filter=no_modal & table_focused)
        def _down(event) -> None:
            change_selection(1)

        @kb.add("k", filter=no_modal & table_focused)
        @kb.add("up", filter=no_modal & table_focused)
        def _up(event) -> None:
            change_selection(-1)

        @kb.add("j", filter=no_modal & log_focused)
        @kb.add("down", filter=no_modal & log_focused)
        def _log_down(event) -> None:
            document = log_area.buffer.document
            if (
                document.cursor_position_row >= document.line_count - 1
                and state.log_more_below
            ):
                self._request_log_page("down")
            else:
                log_area.buffer.cursor_down(count=1)

        @kb.add("k", filter=no_modal & log_focused)
        @kb.add("up", filter=no_modal & log_focused)
        def _log_up(event) -> None:
            if (
                log_area.buffer.document.cursor_position_row == 0
                and state.log_more_above
            ):
                self._request_log_page("up")
            else:
                log_area.buffer.cursor_up(count=1)

        @kb.add("pageup", filter=no_modal & log_focused)
        def _log_page_up(event) -> None:
            render_info = log_area.window.render_info
            if (
                state.log_more_above
                and render_info is not None
                and render_info.first_visible_line() == 0
            ):
                self._request_log_page("up")
            else:
                scroll_page_up(event)

        @kb.add("pagedown", filter=no_modal & log_focused)
        def _log_page_down(event) -> None:
            render_info = log_area.window.render_info
            if (
                state.log_more_below
                and render_info is not None
                and render_info.last_visible_line()
                >= log_area.buffer.document.line_count - 1
            ):
                self._request_log_page("down")
            else:
                scroll_page_down(event)

        @kb.add("g", filter=no_modal & table_focused)
        @kb.add("home", filter=no_modal & table_focused)
        def _first(event) -> None:
            packages = visible_packages(state)
            if packages and packages[0].pkgid != state.selected_pkgid:
                state.selected_pkgid = packages[0].pkgid
                self._on_selection_change()

        @kb.add("G", filter=no_modal & table_focused)
        @kb.add("end", filter=no_modal & table_focused)
        def _last(event) -> None:
            packages = visible_packages(state)
            if packages and packages[-1].pkgid != state.selected_pkgid:
                state.selected_pkgid = packages[-1].pkgid
                self._on_selection_change()

        @kb.add("g", filter=no_modal & log_focused)
        @kb.add("home", filter=no_modal & log_focused)
        def _log_first(event) -> None:
            self._request_log_jump("start")

        @kb.add("G", filter=no_modal & log_focused)
        @kb.add("end", filter=no_modal & log_focused)
        def _log_last(event) -> None:
            self._request_log_jump("end")

        @kb.add("r", filter=no_modal)
        def _refresh(event) -> None:
            event.app.invalidate()

        @kb.add("l", filter=no_modal)
        def _toggle_log(event) -> None:
            state.toggle_log()
            self._on_selection_change()
            if not state.show_log:
                event.app.layout.focus(self._table_control)

        @kb.add("tab", filter=no_modal)
        def _tab_focus(event) -> None:
            if not state.show_log:
                event.app.layout.focus(self._table_control)
            elif event.app.layout.has_focus(log_area):
                event.app.layout.focus(self._table_control)
            else:
                event.app.layout.focus(log_area)

        @kb.add("s", filter=no_modal)
        def _sort(event) -> None:
            old_pkgid = state.selected_pkgid
            idx = _SORT_CYCLE.index(state.sort_mode)
            state.sort_mode = _SORT_CYCLE[(idx + 1) % len(_SORT_CYCLE)]
            state.fix_selection()
            if state.selected_pkgid != old_pkgid:
                self._on_selection_change()

        @kb.add("/", filter=no_modal)
        def _filter_enter(event) -> None:
            self._focus_before_filter = event.app.layout.current_control
            state.filter_input_active = True
            self._filter_buffer.set_document(
                Document(state.filter_text, cursor_position=len(state.filter_text)),
                bypass_readonly=False,
            )
            event.app.layout.focus(self._filter_control)

        @kb.add("p", filter=no_modal)
        def _pause(event) -> None:
            state.paused = not state.paused

        @kb.add("?", filter=~dialog_active & ~filter_active)
        def _help(event) -> None:
            state.show_help = not state.show_help

        @kb.add("escape", filter=filter_active, eager=True)
        def _filter_escape(event) -> None:
            state.filter_input_active = False
            restore_filter_focus(event)

        @kb.add("enter", filter=filter_active)
        def _filter_accept(event) -> None:
            state.filter_input_active = False
            restore_filter_focus(event)

        @kb.add("escape", filter=help_active, eager=True)
        def _help_escape(event) -> None:
            state.show_help = False

        @kb.add("escape", filter=dialog_active, eager=True)
        def _dialog_escape(event) -> None:
            state.cancel_dialog()

        @kb.add("enter", filter=dialog_active)
        def _dialog_enter(event) -> None:
            if state.dialog is None:
                return
            if state.dialog.selected == 0:
                if self._client is not None:
                    event.app.create_background_task(
                        state.confirm_dialog_async(self._client, event.app)
                    )
                else:
                    state.cancel_dialog()
            else:
                state.cancel_dialog()

        @kb.add("left", filter=dialog_active)
        def _dialog_left(event) -> None:
            state.dialog.selected = 0

        @kb.add("right", filter=dialog_active)
        def _dialog_right(event) -> None:
            state.dialog.selected = 1

        @kb.add("x", filter=no_modal)
        def _trigger(event) -> None:
            pkg = state.current_package()
            if pkg is None:
                return
            if pkg.disabled:
                state._set_toast("class:warning", f"[WARN] '{pkg.pkgid}' is disabled")
                return
            action = "stop" if pkg.status == "SYNC" else "start"
            state.open_dialog(action, pkg.pkgid)

        return kb

    def _on_selection_change(self) -> None:
        """Cancel obsolete reads and immediately remove the previous log."""
        self._log_cancel.set()
        self._log_cancel = threading.Event()
        self._log_generation += 1
        self._log_target = None
        self._log_snapshot = None
        self._log_jump = None
        self._log_page_position = None
        state = self._state
        state.log_tail_path = None
        state.log_tail_live = False
        state.log_more_above = False
        state.log_more_below = False
        state.log_following = False
        state.log_message = ""
        if self._log_area is not None:
            self._set_log_text(self._log_area, "", 0)
            self._log_area.window.vertical_scroll = 0

    def _request_log_jump(self, destination: str) -> None:
        """Request a jump to the physical start or end of the log."""
        self._log_jump = "tail" if destination == "end" else "start"
        self._state.log_following = destination == "end"
        if self._app is not None:
            self._app.invalidate()

    def _request_log_page(self, direction: str) -> None:
        """Load an adjacent disk window after navigation reaches a buffer edge."""
        self._log_jump = direction
        self._state.log_following = False
        if self._app is not None:
            self._app.invalidate()

    async def _connect_client(self) -> bool:
        """Connect off the UI thread and clean up an unclaimed connection."""
        client = mirror.socket.master.MasterClient(socket_path=self._socket_path)
        pending = asyncio.create_task(asyncio.to_thread(client.connect))
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            # Let the bounded handshake finish before releasing its socket.
            try:
                await pending
            except Exception:
                pass  # Cancellation owns cleanup regardless of handshake outcome.
            finally:
                await asyncio.to_thread(client.disconnect)
            raise
        except Exception as exc:
            self._state.last_poll_error = str(exc)
            await asyncio.to_thread(client.disconnect)
            return False
        self._client = client
        return True

    async def _poll_once(self, app: Application, was_connected: bool) -> bool:
        """Poll package state without blocking the terminal event loop."""
        state = self._state
        if state.paused:
            return was_connected
        client = self._client
        try:
            if client is None:
                if not await self._connect_client():
                    state.connected = False
                    await asyncio.sleep(2.0)
                    return False
                client = self._client
            payload = await asyncio.to_thread(client.list_packages)
            state.packages = packages_from_rpc(payload)
            old_selection = state.selected_pkgid
            state.fix_selection()
            if old_selection != state.selected_pkgid:
                self._on_selection_change()
            # A path or live/static transition also invalidates old content.
            if self._log_target is not None and self._current_log_target() != self._log_target:
                self._on_selection_change()
            state.selected = max(0, min(state.selected, len(state.packages) - 1))
            state.connected = True
            state.last_poll_error = None
            if (not self._mirrorname and self._log_base is None) or not was_connected:
                try:
                    info = await asyncio.to_thread(client.get_runtime_info)
                    self._apply_runtime_info(info)
                except Exception as exc:
                    state._set_toast("class:warning", f"Runtime info unavailable: {exc}")
            return True
        except Exception as exc:
            state.connected = False
            state.last_poll_error = str(exc)
            if client is not None:
                await asyncio.to_thread(client.disconnect)
            if self._client is client:
                self._client = None
            return False

    async def _status_poller(self, app: Application) -> None:
        """Refresh daemon state once per second."""
        was_connected = False
        while True:
            was_connected = await self._poll_once(app, was_connected)
            app.invalidate()
            await asyncio.sleep(1.0)

    def _current_log_target(self) -> Optional[tuple[str, Path, bool, Optional[Path]]]:
        """Identify the package and physical log currently requested by the UI."""
        if not self._state.show_log:
            return None
        pkg = self._state.current_package()
        if pkg is None:
            return None
        running = pkg.statusinfo.runninglog
        path = Path(running) if running else latest_completed_log(pkg)
        if path is None:
            return None
        return pkg.pkgid, path, bool(running), self._log_base

    def _set_log_text(self, log_area: TextArea, text: str, cursor: int) -> None:
        """Replace log text on the UI thread with a bounded cursor position."""
        from prompt_toolkit.document import Document

        log_area.buffer.set_document(
            Document(text, cursor_position=max(0, min(cursor, len(text)))),
            bypass_readonly=True,
        )

    def _log_action(self, log_area: TextArea) -> tuple[str, bool]:
        """Choose paging from the current viewport and the physical file edges."""
        snapshot = self._log_snapshot
        if self._log_jump is not None:
            action, self._log_jump = self._log_jump, None
            return action, action == "tail"
        if snapshot is None:
            return "tail", True
        buf = log_area.buffer
        at_end = buf.cursor_position == len(buf.text)
        following = at_end and (
            self._state.log_following or not snapshot.more_below
        )
        self._state.log_following = following and self._state.log_tail_live
        position = buf.cursor_position
        if position == self._log_page_position:
            return "poll", following
        self._log_page_position = None
        if self._app is not None and self._app.layout.has_focus(log_area):
            ri = log_area.window.render_info
            if (
                snapshot.more_above and not following and ri is not None
                and ri.vertical_scroll == 0
                and (not snapshot.segmented or buf.cursor_position == 0)
            ):
                return "up", False
            if snapshot.more_below and not following and ri is not None:
                last_line = max(ri.displayed_lines, default=-1)
                if (
                    last_line >= buf.document.line_count - 1
                    and (not snapshot.segmented or at_end)
                ):
                    return "down", False
        return "poll", following

    def _apply_log_snapshot(
        self, log_area: TextArea, snapshot: "_LogSnapshot", action: str
    ) -> None:
        """Install a read result while retaining absolute cursor and viewport anchors."""
        import re

        previous = self._log_snapshot
        buf = log_area.buffer
        cursor_byte = view_byte = snapshot.start
        was_at_end = buf.cursor_position == len(buf.text)
        if previous is not None:
            decoded = previous.data.decode("utf-8", errors="surrogateescape")
            cursor_byte = previous.start + len(
                decoded[:buf.cursor_position].encode("utf-8", errors="surrogateescape")
            )
            view_char = buf.document.translate_row_col_to_index(
                log_area.window.vertical_scroll, 0
            )
            view_byte = previous.start + len(
                decoded[:view_char].encode("utf-8", errors="surrogateescape")
            )
        decoded = snapshot.data.decode("utf-8", errors="surrogateescape")
        text = re.sub(r"[\udc80-\udcff]", "\ufffd", decoded)
        jump_end = action == "tail" or (snapshot.reset and action != "start")
        keep_end = (
            action == "poll" and was_at_end and previous is not None
            and (self._state.log_following or not previous.more_below)
        )
        if jump_end or keep_end:
            cursor = len(text)
        elif action == "start":
            cursor = 0
        else:
            offset = max(0, min(cursor_byte - snapshot.start, len(snapshot.data)))
            cursor = len(snapshot.data[:offset].decode("utf-8", errors="surrogateescape"))
        self._set_log_text(log_area, text, cursor)
        if action == "start":
            log_area.window.vertical_scroll = 0
        elif not jump_end and not keep_end:
            offset = max(0, min(view_byte - snapshot.start, len(snapshot.data)))
            log_area.window.vertical_scroll = snapshot.data[:offset].count(b"\n")
        self._log_snapshot = snapshot
        state = self._state
        state.log_more_above = snapshot.more_above
        state.log_more_below = snapshot.more_below
        state.log_following = (
            state.log_tail_live and cursor == len(text)
            and (jump_end or keep_end or not snapshot.more_below)
        )
        state.log_message = "partial line segment" if snapshot.segmented else ""
        if action in {"up", "down"}:
            self._log_page_position = buf.cursor_position

    async def _poll_log_once(self, app: Application, log_area: TextArea) -> None:
        """Perform at most one serialized disk operation and reject obsolete results."""
        self._log_area = log_area
        target = self._current_log_target()
        if target != self._log_target:
            pending_jump = self._log_jump
            self._on_selection_change()
            self._log_target = target
            self._log_jump = pending_jump
        loop = asyncio.get_running_loop()
        if target is None:
            self._log_jump = None
            if self._log_snapshot is not None or log_area.text:
                self._on_selection_change()
            await loop.run_in_executor(self._log_executor, self._log_reader.close)
            self._state.log_message = "no log"
            return
        generation = self._log_generation
        cancel = self._log_cancel
        _, path, live, base = target
        action, following = self._log_action(log_area)
        self._state.log_tail_path = path
        self._state.log_tail_live = live
        if self._log_snapshot is None:
            self._state.log_message = "loading"
            app.invalidate()
        try:
            snapshot = await loop.run_in_executor(
                self._log_executor, self._log_reader.read,
                path, base, live, action, cancel, following,
            )
        except _LogCancelled:
            return
        except (OSError, EOFError, zlib.error) as exc:
            await loop.run_in_executor(self._log_executor, self._log_reader.close)
            if generation == self._log_generation and target == self._current_log_target():
                self._log_snapshot = None
                self._set_log_text(log_area, "", 0)
                self._state.log_following = False
                self._state.log_more_above = False
                self._state.log_more_below = False
                self._state.log_message = f"unable to read log: {exc}"
                app.invalidate()
            return
        if (
            generation != self._log_generation
            or target != self._current_log_target()
        ):
            return
        self._apply_log_snapshot(log_area, snapshot, action)
        app.invalidate()

    async def _log_tailer(self, app: Application, log_area: TextArea) -> None:
        """Read logs in a single worker while the terminal remains responsive."""
        while True:
            await self._poll_log_once(app, log_area)
            if self._log_jump is None:
                await asyncio.sleep(0.5)

    async def _close_log_reader(self) -> None:
        """Cancel pending reads and close worker-owned resources in order."""
        self._log_cancel.set()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._log_executor, self._log_reader.close)
        await asyncio.to_thread(self._log_executor.shutdown, wait=True)

    async def _run_async(self) -> None:
        """Build and run the prompt_toolkit application asynchronously.

        Starts background tasks for status polling and log tailing, awaits the
        application, then cancels those tasks on exit.
        """
        layout, log_area = self._build_layout()
        kb = self._build_keybindings(log_area)

        app = Application(
            layout=layout,
            key_bindings=kb,
            style=_STYLE,
            full_screen=True,
            mouse_support=True,
            refresh_interval=0.2,
        )
        self._app = app

        poller = app.create_background_task(self._status_poller(app))
        tailer = app.create_background_task(self._log_tailer(app, log_area))
        try:
            await app.run_async()
        finally:
            for t in (poller, tailer):
                t.cancel()
            try:
                if self._client is not None:
                    await asyncio.to_thread(self._client.disconnect)
            finally:
                await asyncio.gather(poller, tailer, return_exceptions=True)
                await self._close_log_reader()
                self._client = None

    def run(self) -> None:
        """Build and run the prompt_toolkit application."""
        asyncio.run(self._run_async())


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def tui(socket_path: Optional[str]) -> None:
    """Run the real-time mirror status TUI.

    Resolves the master socket path and opens the full-screen application.
    Runtime information is fetched by the background status poller.

    Args:
        socket_path(str, optional): Explicit master socket path override.
    """
    sock = _resolve_master_socket(socket_path)

    tui_app = MirrorTUI(socket_path=sock)
    tui_app.run()
