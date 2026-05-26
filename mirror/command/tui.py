"""
Real-time mirror status TUI for mirror.py.

Full-screen prompt_toolkit application showing package sync status,
ETA to next sync, and a live tail of the selected package's running log.
Operators can start or stop syncs via a keyboard-driven confirm dialog.
"""

import asyncio
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
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
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, TextArea

import mirror.socket.master
from mirror.command.config import _resolve_master_socket
from mirror.structure import Package


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


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


def format_eta(package: Package, now: float) -> str:
    """Return human-readable ETA to next scheduled sync, or '-' if disabled.

    Args:
        package(Package): Package to compute ETA for.
        now(float): Current epoch seconds.

    Return:
        eta(str): Human-readable ETA string.
    """
    if package.disabled or package.syncrate <= 0:
        return "-"
    next_sync = package.lastsync + package.syncrate
    remaining = next_sync - now
    if remaining <= 0:
        return "overdue"
    return f"next in {format_duration(remaining)}"


def latest_change_epoch(package: Package) -> float:
    """Return the latest change epoch in SECONDS, normalizing units.

    `Package.timestamp` is set as `time.time() * 1000` in
    `Package.set_status` (mirror/structure/__init__.py:143), i.e. ms;
    `statusinfo.lastsuccesstime` and `statusinfo.lasterrortime` are
    plain epoch seconds. This helper divides `timestamp` by 1000 before
    taking the max, so callers can compare against `time.time()` directly.

    Args:
        package(Package): Package to inspect.

    Return:
        epoch(float): Latest change time in seconds since epoch.
    """
    ts_seconds = package.timestamp / 1000.0 if package.timestamp else 0.0
    return max(
        ts_seconds,
        package.statusinfo.lastsuccesstime or 0.0,
        package.statusinfo.lasterrortime or 0.0,
    )


def format_last_change(package: Package, now: float) -> str:
    """Return '<HH:MM:SS> ago' from latest_change_epoch(package).

    Args:
        package(Package): Package to inspect.
        now(float): Current epoch seconds.

    Return:
        text(str): Human-readable time-since-change string.
    """
    epoch = latest_change_epoch(package)
    if epoch <= 0:
        return "never"
    delta = now - epoch
    if delta < 0:
        delta = 0.0
    return f"{format_duration(delta)} ago"


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


def build_table_rows(
    packages: list[Package], selected: int, now: float
) -> list[tuple[str, str]]:
    """Build FormattedText rows for the table control.

    Args:
        packages(list[Package]): List of packages to render.
        selected(int): Index of the currently selected package.
        now(float): Current epoch seconds.

    Return:
        rows(list[tuple[str, str]]): List of (style, text) tuples.
    """
    rows: list[tuple[str, str]] = []
    for idx, pkg in enumerate(packages):
        prefix = "> " if idx == selected else "  "
        pkgid = pkg.pkgid.ljust(20)
        status = pkg.status.ljust(8)
        eta = format_eta(pkg, now).ljust(20)
        last = format_last_change(pkg, now)
        line = f"{prefix}{pkgid} {status} {eta} {last}\n"

        if idx == selected:
            row_style = "class:selected"
        else:
            row_style = status_style(pkg.status)

        rows.append((row_style, line))
    return rows


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
    # disabled flag is currently lost over the RPC (Package.to_dict drops it);
    # the TUI cannot distinguish disabled packages until the daemon-side
    # serializer is fixed.
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


def _is_rotated(fd: int, path: Path) -> bool:
    """Return True if path-on-disk has a different inode/device than fd.

    Args:
        fd(int): Open file descriptor for the current log.
        path(Path): Filesystem path that should still point to the same file.

    Return:
        rotated(bool): True when path now refers to a different inode or device.
    """
    try:
        on_disk = path.stat()
        on_fd = os.fstat(fd)
    except OSError:
        return True
    return on_disk.st_ino != on_fd.st_ino or on_disk.st_dev != on_fd.st_dev


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class ConfirmDialog:
    """State for the modal confirm dialog."""
    action: str           # "start" or "stop"
    pkgid: str
    selected: int = 0     # 0 = Yes, 1 = No


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
    log_tail_offset: int = 0

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
            # Reset to current end-of-file to avoid backlog flood
            self.log_tail_offset = _get_file_end(self.log_tail_path)

    def current_package(self) -> Optional[Package]:
        """Return the currently selected package, or None if list is empty.

        Return:
            package(Package, optional): Selected package or None.
        """
        if not self.packages:
            return None
        idx = max(0, min(self.selected, len(self.packages) - 1))
        return self.packages[idx]


def _get_file_end(path: Optional[Path]) -> int:
    """Return the current file size, or 0 on any error.

    Args:
        path(Path, optional): File path to stat.

    Return:
        size(int): File size in bytes or 0.
    """
    if path is None:
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Log buffer helpers
# ---------------------------------------------------------------------------

LOG_BUFFER_MAX_LINES = 5000


def _trim_log_text(text: str) -> str:
    """Trim text to at most LOG_BUFFER_MAX_LINES lines, keeping the tail.

    Args:
        text(str): Raw log text, possibly very long.

    Return:
        trimmed(str): Text with at most LOG_BUFFER_MAX_LINES lines.
    """
    lines = text.splitlines(keepends=True)
    if len(lines) > LOG_BUFFER_MAX_LINES:
        lines = lines[-LOG_BUFFER_MAX_LINES:]
        return "".join(lines)
    return text


# ---------------------------------------------------------------------------
# MirrorTUI class
# ---------------------------------------------------------------------------


_STYLE = Style.from_dict(
    {
        "header": "bold",
        "footer": "fg:ansigray",
        "selected": "bg:ansiblue fg:ansiwhite bold",
        "status.sync": "fg:ansiyellow bold",
        "status.active": "fg:ansigreen",
        "status.error": "fg:ansired bold",
        "status.unknown": "fg:ansigray",
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
        self._state = TUIState()
        self._client: Optional[mirror.socket.master.MasterClient] = None
        self._log_fd: Optional[int] = None
        self._app: Optional[Application] = None

    def _apply_runtime_info(self, info: Optional[dict]) -> None:
        """Update mirrorname and log_base from a get_runtime_info response.

        Args:
            info(dict, optional): Dict returned by get_runtime_info RPC. None is a no-op.
        """
        if info is None:
            return
        self._mirrorname = info.get("mirrorname", "") or ""
        lb = info.get("log_base")
        self._log_base = Path(lb) if lb else None

    def _build_layout(self) -> tuple[Layout, TextArea]:
        """Build the prompt_toolkit layout and return (layout, log_area).

        Return:
            layout(Layout): Application layout.
            log_area(TextArea): Log tail text area for external reference.
        """
        state = self._state

        # --- Header ---
        def get_header() -> FormattedText:
            name_part = f" {self._mirrorname}" if self._mirrorname else ""
            conn_style = "class:success" if state.connected else "class:error"
            conn_text = "connected" if state.connected else "disconnected"
            if state.last_poll_error and not state.connected:
                conn_text += f" ({state.last_poll_error})"
            ts = time.strftime("%H:%M:%S")
            return FormattedText(
                [
                    ("class:header", f"mirror tui{name_part}  "),
                    (conn_style, f"[{conn_text}]"),
                    ("class:header", f"  {ts}"),
                ]
            )

        header = Window(
            content=FormattedTextControl(get_header),
            height=1,
        )

        # --- Table ---
        def get_table() -> FormattedText:
            now = time.time()
            # Toast row
            toast_rows: list[tuple[str, str]] = []
            if state.toast:
                expires_at, cls, text = state.toast
                if time.time() < expires_at:
                    toast_rows = [(cls, text + "\n")]
                else:
                    state.toast = None

            if not state.packages:
                body = [("class:status.unknown", "(no packages)\n")]
            else:
                body = build_table_rows(state.packages, state.selected, now)

            return FormattedText(toast_rows + body)

        table_win = Window(
            content=FormattedTextControl(get_table),
            wrap_lines=False,
        )

        # --- Log pane ---
        log_area = TextArea(
            text="",
            read_only=True,
            scrollbar=True,
            focusable=True,
            wrap_lines=False,
        )

        def get_log_header() -> FormattedText:
            pkg = state.current_package()
            follow = "on" if state.log_tail_path else "off"
            if pkg:
                label = f" Log: {pkg.pkgid}  Follow: {follow} "
            else:
                label = " Log (no package selected) "
            return FormattedText([("class:header", label)])

        log_header = Window(content=FormattedTextControl(get_log_header), height=1)
        log_container = ConditionalContainer(
            content=Frame(
                body=HSplit([log_header, log_area]),
                title="",
            ),
            filter=Condition(lambda: state.show_log),
        )

        # --- Footer ---
        def get_footer() -> FormattedText:
            return FormattedText(
                [
                    (
                        "class:footer",
                        "j/k: move  x: start/stop  l: log  r: refresh  q: quit  Tab: focus",
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

        body = VSplit(
            [
                table_win,
                log_container,
            ]
        )

        root = FloatContainer(
            content=HSplit([header, body, footer]),
            floats=[
                Float(content=dialog_win, xcursor=True, ycursor=True),
            ],
        )

        return Layout(root), log_area

    def _build_keybindings(self, log_area: TextArea) -> KeyBindings:
        """Build keybindings for the application.

        Args:
            log_area(TextArea): Log text area (used for End/G re-follow).

        Return:
            kb(KeyBindings): Configured key bindings.
        """
        kb = KeyBindings()
        state = self._state

        @kb.add("q")
        @kb.add("c-c")
        def _quit(event) -> None:
            event.app.exit()

        @kb.add("j")
        @kb.add("down")
        def _down(event) -> None:
            if state.dialog is not None:
                return
            if state.packages:
                state.selected = min(state.selected + 1, len(state.packages) - 1)
                self._on_selection_change()

        @kb.add("k")
        @kb.add("up")
        def _up(event) -> None:
            if state.dialog is not None:
                return
            if state.packages:
                state.selected = max(state.selected - 1, 0)
                self._on_selection_change()

        @kb.add("g")
        @kb.add("home")
        def _first(event) -> None:
            if state.dialog is not None:
                return
            if state.packages:
                state.selected = 0
                self._on_selection_change()

        @kb.add("G")
        @kb.add("end")
        def _last(event) -> None:
            if state.dialog is not None:
                return
            if state.packages:
                state.selected = len(state.packages) - 1
                self._on_selection_change()
            # Re-enable follow in log pane
            self._reset_log_follow(log_area)

        @kb.add("r")
        def _refresh(event) -> None:
            if state.dialog is not None:
                return
            # The poller will pick this up on next tick; just invalidate
            event.app.invalidate()

        @kb.add("l")
        def _toggle_log(event) -> None:
            if state.dialog is not None:
                return
            state.toggle_log()

        @kb.add("tab")
        def _tab_focus(event) -> None:
            if state.dialog is not None:
                return
            if state.show_log:
                event.app.layout.focus_next()

        @kb.add("escape")
        def _escape(event) -> None:
            state.cancel_dialog()

        @kb.add("left")
        def _dialog_left(event) -> None:
            if state.dialog is not None:
                state.dialog.selected = 0

        @kb.add("right")
        def _dialog_right(event) -> None:
            if state.dialog is not None:
                state.dialog.selected = 1

        @kb.add("enter")
        def _enter(event) -> None:
            if state.dialog is not None:
                if state.dialog.selected == 0:
                    # Yes — schedule the RPC as a task to avoid blocking the loop
                    if self._client is not None:
                        asyncio.ensure_future(
                            state.confirm_dialog_async(self._client, event.app)
                        )
                    else:
                        state.cancel_dialog()
                else:
                    state.cancel_dialog()
                return

        @kb.add("x")
        def _trigger(event) -> None:
            if state.dialog is not None:
                return
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
        """Handle package selection change: reset log tail state."""
        self._state.log_tail_offset = 0
        self._state.log_tail_path = None
        if self._log_fd is not None:
            os.close(self._log_fd)
            self._log_fd = None

    def _reset_log_follow(self, log_area: TextArea) -> None:
        """Move log area cursor to end to re-enable auto-follow.

        Args:
            log_area(TextArea): The log pane text area.
        """
        buf = log_area.buffer
        buf.cursor_position = len(buf.text)

    def _connect_client(self) -> bool:
        """Connect or reconnect the master client.

        Return:
            ok(bool): True if connected successfully.
        """
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        try:
            client = mirror.socket.master.MasterClient(socket_path=self._socket_path)
            client.connect()
            self._client = client
            return True
        except Exception as exc:
            self._state.last_poll_error = str(exc)
            return False

    async def _poll_once(self, app: Application, was_connected: bool) -> bool:
        """Run one polling iteration. Returns the new was_connected value.

        Args:
            app(Application): Running application (for invalidate calls).
            was_connected(bool): Connection state at entry to this tick.

        Return:
            was_connected(bool): True if list_packages succeeded this tick,
                False otherwise.
        """
        state = self._state
        try:
            if self._client is None:
                if not self._connect_client():
                    state.connected = False
                    await asyncio.sleep(2.0)
                    return False

            payload = await asyncio.to_thread(self._client.list_packages)
            state.packages = packages_from_rpc(payload)
            state.selected = max(0, min(state.selected, len(state.packages) - 1))
            state.connected = True
            state.last_poll_error = None

            # Fetch runtime info when not yet populated, or on reconnect
            needs_runtime = (
                self._mirrorname == "" and self._log_base is None
            ) or (not was_connected)
            if needs_runtime:
                try:
                    info = await asyncio.to_thread(self._client.get_runtime_info)
                    self._apply_runtime_info(info)
                except Exception:
                    pass

            return True
        except Exception as exc:
            state.connected = False
            state.last_poll_error = str(exc)
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
            return False

    async def _status_poller(self, app: Application) -> None:
        """Background task: poll daemon every 1.0s and update state.

        Args:
            app(Application): Running application (for invalidate calls).
        """
        was_connected = False
        while True:
            was_connected = await self._poll_once(app, was_connected)
            app.invalidate()
            await asyncio.sleep(1.0)

    async def _log_tailer(self, app: Application, log_area: TextArea) -> None:
        """Background task: tail the selected package's running log at 0.5s.

        Args:
            app(Application): Running application (for invalidate calls).
            log_area(TextArea): Log pane text area to append content to.
        """
        state = self._state
        while True:
            await asyncio.sleep(0.5)

            if not state.show_log:
                continue

            pkg = state.current_package()
            if pkg is None:
                continue

            runninglog = pkg.statusinfo.runninglog
            if not runninglog:
                # No active log; clear pane if we had one
                if state.log_tail_path is not None:
                    state.log_tail_path = None
                    state.log_tail_offset = 0
                    if self._log_fd is not None:
                        os.close(self._log_fd)
                        self._log_fd = None
                    log_area.buffer.set_document(
                        log_area.buffer.document.__class__("(idle, no running log)\n"),
                        bypass_readonly=True,
                    )
                    app.invalidate()
                continue

            new_path = Path(runninglog)
            path_changed = new_path != state.log_tail_path

            if path_changed:
                # Close old fd
                if self._log_fd is not None:
                    os.close(self._log_fd)
                    self._log_fd = None
                state.log_tail_path = new_path
                state.log_tail_offset = 0
                log_area.buffer.set_document(
                    log_area.buffer.document.__class__(""),
                    bypass_readonly=True,
                )

            # Open fd if not open
            if self._log_fd is None:
                fd = safe_open_log_for_read(new_path, self._log_base)
                if fd is None:
                    continue
                self._log_fd = fd

            # Check for rotation (inode change) or truncation
            try:
                st = os.fstat(self._log_fd)
            except OSError:
                os.close(self._log_fd)
                self._log_fd = None
                state.log_tail_offset = 0
                continue

            if _is_rotated(self._log_fd, new_path) or st.st_size < state.log_tail_offset:
                # Rotated or truncated: close fd and reset
                os.close(self._log_fd)
                self._log_fd = None
                state.log_tail_offset = 0
                log_area.buffer.set_document(
                    log_area.buffer.document.__class__(""),
                    bypass_readonly=True,
                )
                continue

            # Read new bytes
            try:
                os.lseek(self._log_fd, state.log_tail_offset, os.SEEK_SET)
                chunk = os.read(self._log_fd, 65536)
            except OSError:
                os.close(self._log_fd)
                self._log_fd = None
                state.log_tail_offset = 0
                continue

            if not chunk:
                continue

            state.log_tail_offset += len(chunk)
            text = chunk.decode("utf-8", errors="replace")

            buf = log_area.buffer
            # Determine if we should auto-follow
            current_text = buf.text
            was_at_end = buf.cursor_position >= len(current_text)
            combined = _trim_log_text(current_text + text)
            # Trimming may shift the cursor when the user has scrolled up; auto-follow resumes correctly via End/G.
            buf.set_document(
                buf.document.__class__(combined, cursor_position=len(combined) if was_at_end else buf.cursor_position),
                bypass_readonly=True,
            )
            app.invalidate()

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

        poller = asyncio.create_task(self._status_poller(app))
        tailer = asyncio.create_task(self._log_tailer(app, log_area))
        try:
            await app.run_async()
        finally:
            for t in (poller, tailer):
                t.cancel()
            await asyncio.gather(poller, tailer, return_exceptions=True)

        # Cleanup
        if self._log_fd is not None:
            os.close(self._log_fd)
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass

    def run(self) -> None:
        """Build and run the prompt_toolkit application."""
        asyncio.run(self._run_async())


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def tui(socket_path: Optional[str]) -> None:
    """Run the real-time mirror status TUI.

    Resolves the master socket path, fetches runtime info from the daemon
    best-effort, then opens the full-screen application.

    Args:
        socket_path(str, optional): Explicit master socket path override.
    """
    sock = _resolve_master_socket(socket_path)

    mirrorname = ""
    log_base: Optional[Path] = None
    try:
        info = mirror.socket.master.get_runtime_info(socket_path=sock)
        mirrorname = info.get("mirrorname", "") or ""
        lb = info.get("log_base")
        if lb:
            log_base = Path(lb)
    except Exception:
        # Daemon offline at startup; the TUI still opens and the poller will
        # reconnect. The header simply omits the name and the log helper
        # falls back to symlink/regular-file checks without containment.
        pass

    tui_app = MirrorTUI(
        socket_path=sock,
        mirrorname=mirrorname,
        log_base=log_base,
    )
    tui_app.run()
