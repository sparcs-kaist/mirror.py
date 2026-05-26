"""
Tests for mirror.command.tui helpers and state logic.

All tests operate on pure functions or isolated state; no prompt_toolkit
Application is started. The plan specifies 12 test groups.
"""

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

import mirror.structure
from mirror.command.tui import (
    LOG_BUFFER_MAX_LINES,
    MirrorTUI,
    TUIState,
    _fallback_package_from_dict,
    _is_rotated,
    _trim_log_text,
    build_table_rows,
    format_duration,
    format_eta,
    format_last_change,
    latest_change_epoch,
    packages_from_rpc,
    safe_open_log_for_read,
    status_style,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_package(
    pkgid: str = "debian",
    status: str = "UNKNOWN",
    syncrate: int = 3600,
    lastsync: float = 0.0,
    disabled: bool = False,
    timestamp: float = 0.0,
    lastsuccesstime: float = 0.0,
    lasterrortime: float = 0.0,
    runninglog: str = None,
) -> mirror.structure.Package:
    settings = mirror.structure.PackageSettings(
        hidden=False, src="rsync://example.com/debian", dst="/srv/mirror/debian", options={}
    )
    pkg = mirror.structure.Package(
        pkgid=pkgid,
        name=pkgid,
        status=status,
        href=f"/{pkgid}",
        synctype="rsync",
        syncrate=syncrate,
        link=[],
        settings=settings,
        lastsync=lastsync,
        disabled=disabled,
        timestamp=timestamp,
    )
    pkg.statusinfo.lastsuccesstime = lastsuccesstime
    pkg.statusinfo.lasterrortime = lasterrortime
    if runninglog is not None:
        pkg.statusinfo.runninglog = runninglog
    return pkg


# ---------------------------------------------------------------------------
# 1. format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_zero(self):
        assert format_duration(0) == "0"

    def test_negative(self):
        assert format_duration(-5) == "0"

    def test_59_seconds(self):
        assert format_duration(59) == "00:00:59"

    def test_3600_seconds(self):
        assert format_duration(3600) == "01:00:00"

    def test_86400_seconds_exactly_one_day(self):
        result = format_duration(86400)
        assert result.startswith("1d")

    def test_86461_seconds(self):
        # 1 day + 1 minute + 1 second — day format is "Nd HH:MM"
        result = format_duration(86461)
        assert result.startswith("1d")
        # 86461 = 1 day + 61s => hours=0, minutes=1 => "1d 00:01"
        assert "00:01" in result


# ---------------------------------------------------------------------------
# 2. format_eta
# ---------------------------------------------------------------------------


class TestFormatEta:
    def test_disabled_package(self):
        pkg = _make_package(disabled=True, syncrate=3600)
        assert format_eta(pkg, time.time()) == "-"

    def test_zero_syncrate(self):
        pkg = _make_package(syncrate=0)
        assert format_eta(pkg, time.time()) == "-"

    def test_next_in_future(self):
        now = 1000000.0
        pkg = _make_package(syncrate=3600, lastsync=now - 1800)
        result = format_eta(pkg, now)
        assert result.startswith("next in")

    def test_overdue(self):
        now = 1000000.0
        pkg = _make_package(syncrate=3600, lastsync=now - 7200)
        assert format_eta(pkg, now) == "overdue"


# ---------------------------------------------------------------------------
# 3. latest_change_epoch
# ---------------------------------------------------------------------------


class TestLatestChangeEpoch:
    def test_normalizes_ms_timestamp(self):
        # timestamp is ms (year ~2033), lastsuccesstime is plain seconds
        pkg = _make_package(
            timestamp=2000000000000,  # ms
            lastsuccesstime=1700000000,  # seconds
        )
        result = latest_change_epoch(pkg)
        # timestamp/1000 = 2000000000.0 > 1700000000 => should return 2000000000.0
        assert result == pytest.approx(2000000000.0)

    def test_all_zero_fields(self):
        pkg = _make_package(timestamp=0.0, lastsuccesstime=0.0, lasterrortime=0.0)
        assert latest_change_epoch(pkg) == 0.0

    def test_lasterrortime_wins(self):
        pkg = _make_package(
            timestamp=1000,  # ms => 1.0s
            lastsuccesstime=500.0,
            lasterrortime=1000.0,
        )
        assert latest_change_epoch(pkg) == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# 4. format_last_change
# ---------------------------------------------------------------------------


class TestFormatLastChange:
    def test_returns_ago_string(self):
        now = 1000000.0
        # timestamp in ms: 999900000 ms = 999900.0 s => 100s before now
        pkg = _make_package(timestamp=999900000)
        result = format_last_change(pkg, now)
        assert result.endswith("ago")
        assert "01:40" in result or "100" in result or "00:01:40" in result

    def test_never_when_all_zero(self):
        pkg = _make_package(timestamp=0.0, lastsuccesstime=0.0, lasterrortime=0.0)
        assert format_last_change(pkg, time.time()) == "never"


# ---------------------------------------------------------------------------
# 5. status_style
# ---------------------------------------------------------------------------


class TestStatusStyle:
    def test_active(self):
        assert status_style("ACTIVE") == "class:status.active"

    def test_sync(self):
        assert status_style("SYNC") == "class:status.sync"

    def test_error(self):
        assert status_style("ERROR") == "class:status.error"

    def test_unknown(self):
        assert status_style("UNKNOWN") == "class:status.unknown"

    def test_all_return_nonempty(self):
        for s in ("ACTIVE", "SYNC", "ERROR", "UNKNOWN"):
            assert status_style(s)


# ---------------------------------------------------------------------------
# 6. build_table_rows
# ---------------------------------------------------------------------------


class TestBuildTableRows:
    def test_one_row_per_package(self):
        pkgs = [_make_package("a"), _make_package("b"), _make_package("c")]
        rows = build_table_rows(pkgs, selected=0, now=time.time())
        assert len(rows) == 3

    def test_selected_row_has_selected_style(self):
        pkgs = [_make_package("a"), _make_package("b")]
        rows = build_table_rows(pkgs, selected=1, now=time.time())
        assert rows[1][0] == "class:selected"

    def test_row_text_contains_pkgid(self):
        pkgs = [_make_package("debian")]
        rows = build_table_rows(pkgs, selected=0, now=time.time())
        assert "debian" in rows[0][1]

    def test_row_text_contains_status(self):
        pkgs = [_make_package("debian", status="SYNC")]
        rows = build_table_rows(pkgs, selected=0, now=time.time())
        assert "SYNC" in rows[0][1]


# ---------------------------------------------------------------------------
# 7. packages_from_rpc
# ---------------------------------------------------------------------------


class TestPackagesFromRpc:
    def _build_payload(self, pkgid: str, runninglog: str = None):
        pkg = _make_package(pkgid=pkgid, status="ACTIVE", runninglog=runninglog)
        raw = pkg.to_dict()
        return {"packages": [raw]}

    def test_roundtrips_pkgid(self):
        payload = self._build_payload("pypi")
        result = packages_from_rpc(payload)
        assert len(result) == 1
        assert result[0].pkgid == "pypi"

    def test_roundtrips_status(self):
        payload = self._build_payload("pypi")
        result = packages_from_rpc(payload)
        assert result[0].status == "ACTIVE"

    def test_roundtrips_runninglog(self):
        payload = self._build_payload("debian", runninglog="/var/log/mirror/packages/debian.log")
        result = packages_from_rpc(payload)
        assert result[0].statusinfo.runninglog == "/var/log/mirror/packages/debian.log"

    def test_empty_packages(self):
        result = packages_from_rpc({"packages": []})
        assert result == []

    def test_unknown_synctype_falls_back_instead_of_dropping(self):
        # A package whose synctype is not in mirror.sync.methods should still
        # appear in the result (via _fallback_package_from_dict) rather than
        # being silently dropped.
        pkg = _make_package(pkgid="custom", status="ACTIVE")
        raw = pkg.to_dict()
        raw["synctype"] = "plugin_that_does_not_exist"
        payload = {"packages": [raw]}

        result = packages_from_rpc(payload)
        assert len(result) == 1
        assert result[0].pkgid == "custom"
        assert result[0].status == "ACTIVE"

    def test_fallback_preserves_runninglog(self):
        pkg = _make_package(pkgid="debian", status="SYNC", runninglog="/var/log/debian.log")
        raw = pkg.to_dict()
        raw["synctype"] = "unknown_plugin"
        payload = {"packages": [raw]}

        result = packages_from_rpc(payload)
        assert len(result) == 1
        assert result[0].statusinfo.runninglog == "/var/log/debian.log"


# ---------------------------------------------------------------------------
# 8. Click registration
# ---------------------------------------------------------------------------


class TestClickRegistration:
    def test_tui_subcommand_registered(self):
        from click.testing import CliRunner
        from mirror.__main__ import main

        runner = CliRunner()
        result = runner.invoke(main, ["tui", "--help"])
        assert result.exit_code == 0
        assert "real-time mirror status TUI" in result.output
        assert "--socket" in result.output
        assert "--config" not in result.output


# ---------------------------------------------------------------------------
# 9. safe_open_log_for_read
# ---------------------------------------------------------------------------


class TestSafeOpenLogForRead:
    def test_regular_file_inside_base(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        log_file = base / "test.log"
        log_file.write_text("hello")

        fd = safe_open_log_for_read(log_file, base)
        assert fd is not None
        assert isinstance(fd, int)
        os.close(fd)

    def test_symlink_rejected(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        real_file = base / "real.log"
        real_file.write_text("content")
        link_file = base / "link.log"
        os.symlink(real_file, link_file)

        fd = safe_open_log_for_read(link_file, base)
        assert fd is None

    def test_path_outside_base_rejected(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        outside = tmp_path / "outside.log"
        outside.write_text("evil")

        fd = safe_open_log_for_read(outside, base)
        assert fd is None

    def test_fifo_rejected(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        fifo = base / "test.fifo"
        os.mkfifo(str(fifo))

        fd = safe_open_log_for_read(fifo, base)
        assert fd is None

    def test_base_none_regular_file_accepted(self, tmp_path):
        # base=None skips the containment check; regular files must be accepted.
        log_file = tmp_path / "test.log"
        log_file.write_text("data")

        fd = safe_open_log_for_read(log_file, None)
        assert fd is not None
        assert isinstance(fd, int)
        os.close(fd)

    def test_base_none_symlink_rejected(self, tmp_path):
        real_file = tmp_path / "real.log"
        real_file.write_text("content")
        link_file = tmp_path / "link.log"
        os.symlink(real_file, link_file)

        fd = safe_open_log_for_read(link_file, None)
        assert fd is None

    def test_base_none_fifo_rejected(self, tmp_path):
        fifo = tmp_path / "test.fifo"
        os.mkfifo(str(fifo))

        fd = safe_open_log_for_read(fifo, None)
        assert fd is None


# ---------------------------------------------------------------------------
# 10. State transitions (dialog open/confirm/failure)
# ---------------------------------------------------------------------------


class TestStateTransitions:
    def test_open_dialog_sets_dialog(self):
        state = TUIState()
        state.open_dialog("start", "debian")
        assert state.dialog is not None
        assert state.dialog.action == "start"
        assert state.dialog.pkgid == "debian"

    def test_confirm_dialog_success_sets_toast(self):
        state = TUIState()
        state.open_dialog("start", "debian")

        client = MagicMock()
        client.start_sync.return_value = {"status": "started"}

        state.confirm_dialog(client)

        client.start_sync.assert_called_once_with("debian")
        assert state.dialog is None
        assert state.toast is not None
        _, cls, text = state.toast
        assert cls == "class:success"
        assert "started" in text

    def test_confirm_dialog_failure_sets_error_toast(self):
        state = TUIState()
        state.open_dialog("start", "debian")

        client = MagicMock()
        client.start_sync.side_effect = RuntimeError("socket broken")

        state.confirm_dialog(client)

        assert state.dialog is None
        assert state.toast is not None
        _, cls, text = state.toast
        assert cls == "class:error"
        assert "failed" in text or "socket broken" in text


# ---------------------------------------------------------------------------
# 11. Log tail offset reset on truncation
# ---------------------------------------------------------------------------


class TestLogTailOffsetReset:
    def test_truncation_detected(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        log_file = base / "running.log"
        log_file.write_bytes(b"x" * 100)

        # Open the file and simulate a known offset
        fd = safe_open_log_for_read(log_file, base)
        assert fd is not None

        offset = 100

        # Truncate the file below the offset
        with open(log_file, "wb") as f:
            f.write(b"y" * 10)

        st = os.fstat(fd)
        truncated = st.st_size < offset

        os.close(fd)
        assert truncated, "File should be smaller than recorded offset after truncation"


# ---------------------------------------------------------------------------
# 12. show_log toggle
# ---------------------------------------------------------------------------


class TestShowLogToggle:
    def test_toggle_off_sets_false(self):
        state = TUIState(show_log=True)
        state.toggle_log()
        assert state.show_log is False

    def test_toggle_off_toast_contains_off(self):
        state = TUIState(show_log=True)
        state.toggle_log()
        assert state.toast is not None
        _, _, text = state.toast
        assert "off" in text.lower()

    def test_toggle_on_restores_true(self):
        state = TUIState(show_log=True)
        state.toggle_log()
        state.toggle_log()
        assert state.show_log is True

    def test_toggle_on_toast_contains_on(self):
        state = TUIState(show_log=True)
        state.toggle_log()
        state.toggle_log()
        _, _, text = state.toast
        assert "on" in text.lower()

    def test_toggle_on_resets_offset_to_eof(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        log_file = base / "running.log"
        log_file.write_bytes(b"z" * 50)

        state = TUIState(show_log=True, log_tail_path=log_file, log_tail_offset=0)
        state.toggle_log()  # off
        state.toggle_log()  # on — should reset offset to EOF (50)
        assert state.log_tail_offset == 50


# ---------------------------------------------------------------------------
# _trim_log_text
# ---------------------------------------------------------------------------


class TestTrimLogText:
    def test_log_buffer_caps_at_max_lines(self):
        short = "\n".join(f"l{i}" for i in range(10)) + "\n"
        assert _trim_log_text(short) == short

        long = "\n".join(f"l{i}" for i in range(LOG_BUFFER_MAX_LINES + 50)) + "\n"
        trimmed = _trim_log_text(long)
        assert trimmed.count("\n") == LOG_BUFFER_MAX_LINES
        assert trimmed.endswith(f"l{LOG_BUFFER_MAX_LINES + 49}\n")


# ---------------------------------------------------------------------------
# _is_rotated
# ---------------------------------------------------------------------------


def test_log_tailer_detects_rotation_by_inode(tmp_path):
    base = tmp_path / "logs"
    base.mkdir()
    log_file = base / "running.log"
    log_file.write_bytes(b"x" * 20)

    fd_a = safe_open_log_for_read(log_file, base)
    assert fd_a is not None

    # No rotation yet: same inode
    assert _is_rotated(fd_a, log_file) is False

    # Simulate log rotation: unlink and recreate (new inode)
    os.unlink(log_file)
    log_file.write_text("new content after rotation")

    assert _is_rotated(fd_a, log_file) is True

    os.close(fd_a)


# ---------------------------------------------------------------------------
# Addendum: get_runtime_info RPC tests
# ---------------------------------------------------------------------------


_RUNTIME_INFO = {
    "mirrorname": "testmirror",
    "hostname": "h.example",
    "localtimezone": "UTC",
    "logfolder": "/var/log/mirror",
    "webroot": "/var/www/mirror",
    "log_base": "/var/log/mirror/packages",
    "max_runtime_seconds": 43200,
    "errorcontinuetime": 60,
    "sync_methods": ["rsync", "ftpsync"],
    "daemon_started_at": time.time(),
}


class TestTuiForwardsGetRuntimeInfo:
    """(i) tui() calls get_runtime_info and passes mirrorname/log_base to MirrorTUI."""

    def test_forwards_runtime_info(self, monkeypatch):
        import mirror.socket.master
        import sys
        tui_module = sys.modules["mirror.command.tui"]

        monkeypatch.setattr(
            mirror.socket.master, "get_runtime_info", lambda socket_path=None: _RUNTIME_INFO
        )

        captured = {}

        class CaptureMirrorTUI:
            def __init__(self, socket_path, mirrorname="", log_base=None):
                captured["mirrorname"] = mirrorname
                captured["log_base"] = log_base

            def run(self):
                pass

        monkeypatch.setattr(tui_module, "MirrorTUI", CaptureMirrorTUI)

        from mirror.command.tui import tui
        tui(socket_path=None)

        assert captured["mirrorname"] == "testmirror"
        assert captured["log_base"] == Path("/var/log/mirror/packages")


class TestTuiStartupRpcRaises:
    """(ii) When get_runtime_info raises at startup, TUI opens with empty values;
    _apply_runtime_info setter then populates them."""

    def test_startup_rpc_raises_then_setter_populates(self, monkeypatch):
        import mirror.socket.master
        import sys
        tui_module = sys.modules["mirror.command.tui"]

        def _raise(socket_path=None):
            raise RuntimeError("daemon offline")

        monkeypatch.setattr(mirror.socket.master, "get_runtime_info", _raise)

        captured_tui = {}

        class CaptureMirrorTUI:
            def __init__(self, socket_path, mirrorname="", log_base=None):
                self._mirrorname = mirrorname
                self._log_base = log_base
                captured_tui["instance"] = self

            def run(self):
                pass

            # Delegate to real implementation via composition
            _apply_runtime_info = MirrorTUI._apply_runtime_info

        monkeypatch.setattr(tui_module, "MirrorTUI", CaptureMirrorTUI)

        from mirror.command.tui import tui
        tui(socket_path=None)

        instance = captured_tui["instance"]
        assert instance._mirrorname == ""
        assert instance._log_base is None

        # Now apply runtime info via the setter (call unbound to avoid double-self)
        MirrorTUI._apply_runtime_info(instance, {"mirrorname": "m", "log_base": "/x"})
        assert instance._mirrorname == "m"
        assert instance._log_base == Path("/x")


class TestStatusPollerFetchesRuntimeInfoAfterStartupFailure:
    """(iii) Poller fetches runtime info on first successful tick when not yet populated."""

    def test_fetches_on_first_success(self):
        tui_instance = MirrorTUI(socket_path="/tmp/fake.sock")
        # Precondition: unpopulated
        assert tui_instance._mirrorname == ""
        assert tui_instance._log_base is None

        client = MagicMock()
        client.list_packages.return_value = {"packages": []}
        client.get_runtime_info.return_value = {"mirrorname": "new", "log_base": "/logs"}
        tui_instance._client = client

        mock_app = MagicMock()
        result = asyncio.run(tui_instance._poll_once(mock_app, was_connected=False))

        assert result is True
        assert tui_instance._mirrorname == "new"
        assert tui_instance._log_base == Path("/logs")
        client.list_packages.assert_called_once()
        client.get_runtime_info.assert_called_once()


class TestStatusPollerDoesNotRefetchInSteadyState:
    """(iv) Steady-state: populated + was_connected=True → get_runtime_info not called."""

    def test_no_refetch_in_steady_state(self):
        tui_instance = MirrorTUI(
            socket_path="/tmp/fake.sock",
            mirrorname="populated",
            log_base=Path("/logs"),
        )

        client = MagicMock()
        client.list_packages.return_value = {"packages": []}
        client.get_runtime_info.side_effect = AssertionError("get_runtime_info must not be called")
        tui_instance._client = client

        mock_app = MagicMock()
        result = asyncio.run(tui_instance._poll_once(mock_app, was_connected=True))

        assert result is True
        client.list_packages.assert_called_once()
        client.get_runtime_info.assert_not_called()


class TestStatusPollerRefetchesOnReconnect:
    """(v) Reconnect transition: populated + was_connected=False → refetch and update."""

    def test_refetches_on_reconnect_with_updated_values(self):
        tui_instance = MirrorTUI(
            socket_path="/tmp/fake.sock",
            mirrorname="old",
            log_base=Path("/old"),
        )

        client = MagicMock()
        client.list_packages.return_value = {"packages": []}
        client.get_runtime_info.return_value = {"mirrorname": "new", "log_base": "/new"}
        tui_instance._client = client

        mock_app = MagicMock()
        result = asyncio.run(tui_instance._poll_once(mock_app, was_connected=False))

        assert result is True
        assert tui_instance._mirrorname == "new"
        assert tui_instance._log_base == Path("/new")
        client.list_packages.assert_called_once()
        client.get_runtime_info.assert_called_once()


