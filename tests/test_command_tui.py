"""
Tests for TUI helpers, terminal interaction, and background log reading.
"""

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from prompt_toolkit import Application
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

import mirror.structure
from mirror.command.tui import (
    LOG_FOLLOW_MAX_LINES,
    LOG_INITIAL_LINES,
    LOG_PAGE_LINES,
    MirrorTUI,
    TUIState,
    _fit_field,
    _fallback_package_from_dict,
    _modal_active,
    _visible_columns,
    apply_filter,
    apply_sort,
    build_help_text,
    build_table_header,
    build_table_rows,
    compute_status_counts,
    format_ago,
    format_datetime,
    format_duration,
    format_elapsed,
    format_last_success,
    format_started,
    latest_completed_log,
    packages_from_rpc,
    safe_open_log_for_read,
    status_style,
    visible_packages,
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
    errorcount: int = 0,
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
    pkg.statusinfo.errorcount = errorcount
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
# 2. format_datetime
# ---------------------------------------------------------------------------


class TestFormatDatetime:
    def test_zero_returns_dash(self):
        assert format_datetime(0) == "-"

    def test_negative_returns_dash(self):
        assert format_datetime(-1) == "-"

    def test_positive_returns_iso_like_string(self):
        # Pick a fixed epoch and check the format shape only (locale-dependent).
        result = format_datetime(1_700_000_000)
        assert len(result) == 19
        assert result[4] == "-" and result[7] == "-" and result[10] == " "
        assert result[13] == ":" and result[16] == ":"


# ---------------------------------------------------------------------------
# 3. format_started
# ---------------------------------------------------------------------------


class TestFormatStarted:
    def test_non_sync_returns_dash(self):
        pkg = _make_package(status="ACTIVE", timestamp=1_700_000_000 * 1000)
        assert format_started(pkg, time.time()) == "-"

    def test_sync_without_timestamp(self):
        pkg = _make_package(status="SYNC", timestamp=0.0)
        assert format_started(pkg, time.time()) == "(unknown)"

    def test_sync_normalizes_ms_timestamp(self):
        # Package.timestamp is ms; format_started must divide by 1000.
        pkg = _make_package(status="SYNC", timestamp=1_700_000_000_000)
        result = format_started(pkg, time.time())
        # Same format as format_datetime(1_700_000_000)
        assert result == format_datetime(1_700_000_000)


# ---------------------------------------------------------------------------
# 4. format_elapsed
# ---------------------------------------------------------------------------


class TestFormatElapsed:
    def test_non_sync_returns_dash(self):
        pkg = _make_package(status="ACTIVE", timestamp=(time.time() - 120) * 1000)
        assert format_elapsed(pkg, time.time()) == "-"

    def test_sync_without_timestamp_returns_dash(self):
        pkg = _make_package(status="SYNC", timestamp=0.0)
        assert format_elapsed(pkg, time.time()) == "-"

    def test_sync_returns_running_duration(self):
        now = 1_000_000.0
        pkg = _make_package(status="SYNC", timestamp=(now - 125) * 1000)
        result = format_elapsed(pkg, now)
        assert result == "00:02:05"


# ---------------------------------------------------------------------------
# 5. format_last_success
# ---------------------------------------------------------------------------


class TestFormatLastSuccess:
    def test_never(self):
        pkg = _make_package(lastsuccesstime=0.0)
        assert format_last_success(pkg) == "(never)"

    def test_returns_datetime(self):
        pkg = _make_package(lastsuccesstime=1_700_000_000)
        assert format_last_success(pkg) == format_datetime(1_700_000_000)


# ---------------------------------------------------------------------------
# 6. format_ago
# ---------------------------------------------------------------------------


class TestFormatAgo:
    def test_zero_returns_dash(self):
        assert format_ago(0, time.time()) == "-"

    def test_future_epoch_returns_dash(self):
        now = 1_000_000.0
        assert format_ago(now + 60, now) == "-"

    def test_past_epoch_returns_duration(self):
        now = 1_000_000.0
        assert format_ago(now - 125, now) == "00:02:05"


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

    def test_selected_row_has_selected_style_via_pkgid(self):
        pkgs = [_make_package("a"), _make_package("b")]
        rows = build_table_rows(pkgs, selected=0, now=time.time(), selected_pkgid="b")
        assert rows[1][0] == "class:selected"

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

    def test_row_alignment_matches_header(self):
        pkgs = [_make_package("debian", status="SYNC")]
        header_rows = build_table_header()
        body_rows = build_table_rows(pkgs, selected=0, now=time.time())
        assert len(header_rows[0][1]) == len(body_rows[0][1])

    def test_long_pkgid_truncated_keeps_alignment(self):
        pkgs = [_make_package("a" * 80, status="SYNC")]
        header_rows = build_table_header()
        body_rows = build_table_rows(pkgs, selected=0, now=time.time())
        # Header and body must remain the same total width
        assert len(header_rows[0][1]) == len(body_rows[0][1])
        # Truncation marker is the trailing ".."
        assert ".." in body_rows[0][1]

    def test_disabled_package_has_disabled_style(self):
        pkg = _make_package("mypkg", disabled=True)
        # Use a non-matching pkgid so this package is not selected
        rows = build_table_rows([pkg], selected=99, now=time.time(), selected_pkgid="other")
        assert rows[0][0] == "class:status.disabled"

    def test_disabled_package_pkgid_has_off_suffix(self):
        pkg = _make_package("mypkg", disabled=True)
        rows = build_table_rows([pkg], selected=99, now=time.time(), selected_pkgid="other")
        assert "(off)" in rows[0][1]

    def test_error_with_count_shows_error_xn(self):
        pkg = _make_package("mypkg", status="ERROR", errorcount=3)
        rows = build_table_rows([pkg], selected=0, now=time.time())
        assert "ERROR x3" in rows[0][1]

    def test_error_with_zero_count_shows_plain_error(self):
        pkg = _make_package("mypkg", status="ERROR", errorcount=0)
        rows = build_table_rows([pkg], selected=0, now=time.time())
        assert "ERROR" in rows[0][1]
        assert "ERROR x" not in rows[0][1]


class TestBuildTableHeader:
    def test_returns_two_rows(self):
        rows = build_table_header()
        assert len(rows) == 2

    def test_label_row_contains_column_names(self):
        rows = build_table_header()
        label = rows[0][1]
        for col in ("PACKAGE", "STATUS", "STARTED", "ELAPSED", "LAST SUCCESS", "AGO"):
            assert col in label

    def test_divider_row_is_dashes(self):
        rows = build_table_header()
        divider = rows[1][1]
        assert "-" in divider
        assert "PACKAGE" not in divider

    def test_styles(self):
        rows = build_table_header()
        assert rows[0][0] == "class:tableheader"
        assert rows[1][0] == "class:tableheader.divider"

    def test_responsive_wide_all_columns(self):
        vis = _visible_columns(101)
        rows = build_table_header(vis)
        label = rows[0][1]
        assert "ELAPSED" in label
        assert "STARTED" in label

    def test_responsive_drops_columns_in_priority_order(self):
        vis = _visible_columns(100)
        rows = build_table_header(vis)
        label = rows[0][1]
        assert "AGO" not in label
        assert "ELAPSED" in label
        assert "STARTED" in label

        vis = _visible_columns(80)
        rows = build_table_header(vis)
        label = rows[0][1]
        assert "ELAPSED" not in label
        assert "STARTED" in label

        vis = _visible_columns(70)
        label = build_table_header(vis)[0][1]
        assert "STARTED" not in label
        assert "LAST SUCCESS" in label

        vis = _visible_columns(50)
        label = build_table_header(vis)[0][1]
        assert "LAST SUCCESS" not in label

    def test_responsive_narrow_header_body_aligned(self):
        vis = _visible_columns(50)
        pkgs = [_make_package("debian")]
        header_rows = build_table_header(vis, available_width=50)
        body_rows = build_table_rows(
            pkgs, selected=0, now=time.time(), visible=vis, available_width=50
        )
        assert len(header_rows[0][1]) == len(body_rows[0][1])

    def test_narrow_columns_fit_available_width(self):
        vis = _visible_columns(24)
        header = build_table_header(vis, available_width=24)[0][1]
        assert get_cwidth(header.rstrip("\n")) == 24

    def test_fit_field_uses_terminal_cell_width(self):
        field = _fit_field("한글-package", 10)
        assert get_cwidth(field) == 10


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


class TestLatestCompletedLog:
    def test_none_when_no_logs(self):
        pkg = _make_package()
        assert latest_completed_log(pkg) is None

    def test_returns_success_log_when_only_success(self):
        pkg = _make_package(lastsuccesstime=100.0)
        pkg.statusinfo.lastsuccesslog = "/var/log/mirror/packages/success.log.gz"
        assert latest_completed_log(pkg) == Path(pkg.statusinfo.lastsuccesslog)

    def test_returns_error_log_when_only_error(self):
        pkg = _make_package(lasterrortime=100.0)
        pkg.statusinfo.lasterrorlog = "/var/log/mirror/packages/error.log.gz"
        assert latest_completed_log(pkg) == Path(pkg.statusinfo.lasterrorlog)

    def test_picks_newer_by_timestamp(self):
        pkg = _make_package(lastsuccesstime=100.0, lasterrortime=200.0)
        pkg.statusinfo.lastsuccesslog = "/var/log/mirror/packages/success.log.gz"
        pkg.statusinfo.lasterrorlog = "/var/log/mirror/packages/error.log.gz"
        assert latest_completed_log(pkg) == Path(pkg.statusinfo.lasterrorlog)

        pkg.statusinfo.lastsuccesstime = 300.0
        assert latest_completed_log(pkg) == Path(pkg.statusinfo.lastsuccesslog)


class TestTuiLogReading:
    def _make_tui(self, base, pkg):
        from prompt_toolkit.widgets import TextArea

        tui = MirrorTUI('/tmp/no-master.sock', log_base=base)
        tui._state.packages = [pkg]
        tui._state.selected_pkgid = pkg.pkgid
        log = TextArea(read_only=True)
        tui._log_area = log
        return tui, log

    def test_latest_gzip_and_no_log_clear(self, tmp_path):
        import gzip

        path = tmp_path / 'done.gz'
        with gzip.open(path, 'wb') as output:
            output.write(b'completed\n')
        pkg = _make_package(status='ACTIVE', lastsuccesstime=10)
        pkg.statusinfo.lastsuccesslog = str(path)
        tui, log = self._make_tui(tmp_path, pkg)

        async def scenario():
            try:
                await tui._poll_log_once(MagicMock(), log)
                assert log.text == 'completed\n'
                assert not tui._state.log_tail_live
                pkg.statusinfo.lastsuccesslog = ''
                await tui._poll_log_once(MagicMock(), log)
                assert log.text == ''
                assert tui._state.log_tail_path is None
            finally:
                await tui._close_log_reader()
        asyncio.run(scenario())

    def test_missing_other_package_never_keeps_old_content_and_recovers(self, tmp_path):
        path = tmp_path / 'a.log'
        path.write_text('package A\n')
        pkg = _make_package(pkgid='a', runninglog=str(path))
        tui, log = self._make_tui(tmp_path, pkg)
        other_path = tmp_path / 'b.log'
        other = _make_package(pkgid='b', runninglog=str(other_path))

        async def scenario():
            try:
                await tui._poll_log_once(MagicMock(), log)
                tui._state.packages.append(other)
                tui._state.selected_pkgid = 'b'
                tui._on_selection_change()
                assert log.text == ''
                await tui._poll_log_once(MagicMock(), log)
                assert log.text == ''
                assert 'unable to read' in tui._state.log_message
                other_path.write_text('package B\n')
                await tui._poll_log_once(MagicMock(), log)
                assert log.text == 'package B\n'
                tui._state.filter_text = 'no match'
                tui._state.fix_selection()
                await tui._poll_log_once(MagicMock(), log)
                assert log.text == ''
            finally:
                await tui._close_log_reader()
        asyncio.run(scenario())

    def test_slow_obsolete_read_is_discarded(self, tmp_path):
        import threading

        path = tmp_path / 'a.log'
        path.write_text('old package\n')
        pkg = _make_package(pkgid='a', runninglog=str(path))
        tui, log = self._make_tui(tmp_path, pkg)
        entered, release = threading.Event(), threading.Event()
        original = tui._log_reader.read

        def delayed(*args):
            result = original(*args)
            entered.set()
            assert release.wait(5)
            return result

        tui._log_reader.read = delayed

        async def scenario():
            pending = asyncio.create_task(tui._poll_log_once(MagicMock(), log))
            try:
                for _ in range(200):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.005)
                assert entered.is_set()
                # This executes while the worker is still blocked.
                tui._state.packages = []
                tui._on_selection_change()
                assert not pending.done()
                release.set()
                await asyncio.wait_for(pending, 5)
                assert log.text == ''
                assert tui._log_snapshot is None
            finally:
                release.set()
                await pending
                await tui._close_log_reader()
        asyncio.run(scenario())

    def test_io_failure_does_not_prevent_next_read(self, tmp_path):
        path = tmp_path / 'live.log'
        path.write_text('recovered\n')
        pkg = _make_package(runninglog=str(path))
        tui, log = self._make_tui(tmp_path, pkg)

        async def scenario():
            try:
                with patch.object(tui._log_reader, 'read', side_effect=OSError('disk error')):
                    await tui._poll_log_once(MagicMock(), log)
                assert 'disk error' in tui._state.log_message
                await tui._poll_log_once(MagicMock(), log)
                assert log.text == 'recovered\n'
            finally:
                await tui._close_log_reader()
        asyncio.run(scenario())

    def test_history_preserves_anchor_and_end_returns_to_physical_eof(self, tmp_path):
        path = tmp_path / 'history.log'
        path.write_text(''.join(f'line{i}\n' for i in range(6000)))
        pkg = _make_package(runninglog=str(path))
        tui, log = self._make_tui(tmp_path, pkg)

        async def scenario():
            try:
                app = MagicMock()
                await tui._poll_log_once(app, log)
                tui._request_log_jump('start')
                await tui._poll_log_once(app, log)
                assert log.text.startswith('line0\n')
                assert tui._state.log_more_below
                assert not tui._state.log_following
                log.buffer.cursor_position = 30
                before = log.buffer.document.current_line
                with path.open('a') as output:
                    output.write('new ending\n')
                await tui._poll_log_once(app, log)
                assert log.buffer.document.current_line == before
                assert 'new ending' not in log.text
                tui._request_log_jump('end')
                await tui._poll_log_once(app, log)
                assert log.text.endswith('new ending\n')
                assert tui._state.log_following
            finally:
                await tui._close_log_reader()
        asyncio.run(scenario())

    def test_cancelled_gzip_preparation_cleans_worker(self, tmp_path):
        import threading
        from mirror.command.tui import _LogCancelled

        pkg = _make_package(runninglog=str(tmp_path / 'log'))
        tui, log = self._make_tui(tmp_path, pkg)
        entered = threading.Event()

        def pending_read(path, base, live, action, cancel, following):
            entered.set()
            assert cancel.wait(5)
            raise _LogCancelled()

        tui._log_reader.read = pending_read

        async def scenario():
            pending = asyncio.create_task(tui._poll_log_once(MagicMock(), log))
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(.005)
            assert entered.is_set()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await asyncio.wait_for(tui._close_log_reader(), 5)
        asyncio.run(scenario())


class TestBoundedLogReader:
    """Backend coverage for disk-backed plain and gzip log paging."""

    @staticmethod
    def _reader_api():
        import importlib

        tui_module = importlib.import_module("mirror.command.tui")
        return tui_module, tui_module._LogReader()

    def test_tail_and_bidirectional_paging_stay_bounded(self, tmp_path):
        import threading

        tui_module, reader = self._reader_api()
        path = tmp_path / "large.log"
        lines = [f"line-{index:06d}\n".encode() for index in range(205_000)]
        expected = b"".join(lines)
        path.write_bytes(expected)
        cancel = threading.Event()

        snapshot = reader.read(path, tmp_path, False, "tail", cancel)
        assert snapshot.reset is True
        assert snapshot.data == b"".join(lines[-tui_module.LOG_INITIAL_LINES :])

        while snapshot.more_above:
            previous_start = snapshot.start
            snapshot = reader.read(path, tmp_path, False, "up", cancel)
            assert snapshot.start < previous_start
            assert snapshot.data.count(b"\n") <= tui_module.LOG_MAX_LOADED_LINES
            assert len(snapshot.data) <= tui_module.LOG_MAX_LOADED_BYTES
            assert snapshot.data == expected[snapshot.start : snapshot.end]
        assert snapshot.start == 0

        while snapshot.more_below:
            previous_end = snapshot.end
            snapshot = reader.read(path, tmp_path, False, "down", cancel)
            assert snapshot.end > previous_end
            assert snapshot.data.count(b"\n") <= tui_module.LOG_MAX_LOADED_LINES
            assert len(snapshot.data) <= tui_module.LOG_MAX_LOADED_BYTES
        assert snapshot.end == snapshot.size == path.stat().st_size
        reader.close()

    def test_poll_only_appends_while_live_following(self, tmp_path):
        import threading

        tui_module, reader = self._reader_api()
        path = tmp_path / "live.log"
        path.write_bytes(b"initial\n")
        cancel = threading.Event()
        initial = reader.read(path, tmp_path, True, "tail", cancel)

        with path.open("ab") as stream:
            for index in range(tui_module.LOG_FOLLOW_MAX_LINES + 100):
                stream.write(f"new-{index}\n".encode())

        historical = reader.read(
            path, tmp_path, True, "poll", cancel, following=False
        )
        assert historical.data == initial.data
        assert historical.end == initial.end
        assert historical.size == path.stat().st_size
        assert historical.more_below is True

        following = reader.read(path, tmp_path, True, "poll", cancel)
        assert following.end == following.size
        assert following.data.count(b"\n") <= tui_module.LOG_FOLLOW_MAX_LINES
        assert following.data.endswith(
            f"new-{tui_module.LOG_FOLLOW_MAX_LINES + 99}\n".encode()
        )
        reader.close()

    def test_rotation_and_truncation_reset_source(self, tmp_path):
        import threading

        _, reader = self._reader_api()
        path = tmp_path / "live.log"
        path.write_bytes(b"old\n")
        cancel = threading.Event()
        reader.read(path, tmp_path, True, "tail", cancel)

        rotated = tmp_path / "rotated.log"
        path.rename(rotated)
        path.write_bytes(b"replacement\n")
        snapshot = reader.read(path, tmp_path, True, "poll", cancel)
        assert snapshot.reset is True
        assert snapshot.data == b"replacement\n"

        path.write_bytes(b"x\n")
        snapshot = reader.read(path, tmp_path, True, "poll", cancel)
        assert snapshot.reset is True
        assert snapshot.data == b"x\n"
        reader.close()

    def test_gzip_pages_without_historical_line_cutoff(self, tmp_path):
        import gzip
        import threading

        _, reader = self._reader_api()
        path = tmp_path / "archive.log.gz"
        expected = b"".join(f"line-{index}\n".encode() for index in range(200_100))
        with gzip.open(path, "wb") as stream:
            stream.write(expected)

        cancel = threading.Event()
        snapshot = reader.read(path, tmp_path, False, "tail", cancel)
        temporary = reader._temporary
        assert snapshot.size == len(expected)
        assert snapshot.data.endswith(b"line-200099\n")

        snapshot = reader.read(path, tmp_path, False, "start", cancel)
        assert snapshot.start == 0
        assert snapshot.data.startswith(b"line-0\n")
        assert snapshot.more_below is True
        reader.close()
        assert temporary.closed is True

    def test_cancellation_closes_plain_and_partial_gzip_backing(self, tmp_path):
        import gzip
        import threading

        tui_module, reader = self._reader_api()
        plain = tmp_path / "plain.log"
        plain.write_bytes(b"data\n")
        cancel = threading.Event()
        reader.read(plain, tmp_path, False, "tail", cancel)
        cancel.set()
        with pytest.raises(tui_module._LogCancelled):
            reader.read(plain, tmp_path, False, "poll", cancel)
        assert reader._fd is None

        compressed = tmp_path / "large.log.gz"
        with gzip.open(compressed, "wb") as stream:
            stream.write(b"x" * (4 * tui_module.LOG_READ_BLOCK_BYTES))

        class CancelAfterChunks:
            def __init__(self):
                self.calls = 0

            def is_set(self):
                self.calls += 1
                return self.calls >= 3

        with pytest.raises(tui_module._LogCancelled):
            reader.read(
                compressed, tmp_path, False, "tail", CancelAfterChunks()
            )
        assert reader._fd is None
        assert reader._temporary is None

    def test_corrupt_gzip_and_enospc_close_partial_backing(self, tmp_path):
        import errno
        import gzip
        import threading

        tui_module, reader = self._reader_api()
        corrupt = tmp_path / "corrupt.log.gz"
        corrupt.write_bytes(b"not a gzip stream")
        with pytest.raises(OSError):
            reader.read(corrupt, tmp_path, False, "tail", threading.Event())
        assert reader._fd is None
        assert reader._temporary is None

        compressed = tmp_path / "valid.log.gz"
        with gzip.open(compressed, "wb") as stream:
            stream.write(b"content\n")
        temporary = MagicMock()
        temporary.write.side_effect = OSError(errno.ENOSPC, "no space")
        with patch("tempfile.TemporaryFile", return_value=temporary):
            with pytest.raises(OSError) as error:
                reader.read(
                    compressed, tmp_path, False, "tail", threading.Event()
                )
        assert error.value.errno == errno.ENOSPC
        temporary.close.assert_called_once()
        assert reader._fd is None
        assert reader._temporary is None

    def test_fstat_failure_closes_new_source_fd(self, tmp_path):
        import threading

        tui_module, reader = self._reader_api()
        path = tmp_path / "plain.log"
        path.write_bytes(b"content\n")
        source_fd = os.open(path, os.O_RDONLY)

        with patch.object(
            tui_module, "safe_open_log_for_read", return_value=source_fd
        ), patch.object(tui_module.os, "fstat", side_effect=OSError("stat failed")):
            with pytest.raises(OSError, match="stat failed"):
                reader.read(path, tmp_path, False, "tail", threading.Event())

        with pytest.raises(OSError):
            os.fstat(source_fd)
        assert reader._fd is None

    def test_gzip_bidirectional_paging_evicts_opposite_edge(self, tmp_path):
        import gzip
        import threading

        tui_module, reader = self._reader_api()
        path = tmp_path / "archive.log.gz"
        expected = b"".join(f"line-{index}\n".encode() for index in range(12))
        with gzip.open(path, "wb") as stream:
            stream.write(expected)

        cancel = threading.Event()
        with patch.object(tui_module, "LOG_INITIAL_LINES", 2), patch.object(
            tui_module, "LOG_PAGE_LINES", 2
        ), patch.object(tui_module, "LOG_MAX_LOADED_LINES", 3):
            snapshot = reader.read(path, tmp_path, False, "tail", cancel)
            while snapshot.more_above:
                old_start = snapshot.start
                snapshot = reader.read(path, tmp_path, False, "up", cancel)
                assert snapshot.start < old_start
                assert tui_module._LogReader._line_count(snapshot.data) <= 3
                assert snapshot.data == expected[snapshot.start : snapshot.end]
            assert snapshot.start == 0

            while snapshot.more_below:
                old_end = snapshot.end
                snapshot = reader.read(path, tmp_path, False, "down", cancel)
                assert snapshot.end > old_end
                assert tui_module._LogReader._line_count(snapshot.data) <= 3
                assert snapshot.data == expected[snapshot.start : snapshot.end]
            assert snapshot.end == len(expected)
        reader.close()

    def test_long_utf8_line_segments_preserve_all_bytes(self, tmp_path):
        import importlib
        import threading

        tui_module = importlib.import_module("mirror.command.tui")

        path = tmp_path / "utf8.log"
        expected = ("€" * 31).encode()
        path.write_bytes(expected)
        reader = tui_module._LogReader()
        cancel = threading.Event()

        with patch.object(tui_module, "LOG_MAX_LOADED_BYTES", 17):
            snapshot = reader.read(path, tmp_path, False, "start", cancel)
            assert snapshot.segmented is True
            assert snapshot.data == expected[snapshot.start : snapshot.end]
            assert snapshot.data.decode("utf-8")

            ranges = [(snapshot.start, snapshot.end)]
            while snapshot.more_below:
                snapshot = reader.read(path, tmp_path, False, "down", cancel)
                assert snapshot.data == expected[snapshot.start : snapshot.end]
                assert snapshot.data.decode("utf-8")
                ranges.append((snapshot.start, snapshot.end))
            assert ranges[0][0] == 0
            assert ranges[-1][1] == len(expected)
            assert all(left[1] >= right[0] for left, right in zip(ranges, ranges[1:]))

            snapshot = reader.read(path, tmp_path, False, "tail", cancel)
            assert snapshot.data.decode("utf-8")
            ranges = [(snapshot.start, snapshot.end)]
            while snapshot.more_above:
                snapshot = reader.read(path, tmp_path, False, "up", cancel)
                assert snapshot.data == expected[snapshot.start : snapshot.end]
                assert snapshot.data.decode("utf-8")
                ranges.append((snapshot.start, snapshot.end))
            assert ranges[0][1] == len(expected)
            assert ranges[-1][0] == 0
            assert all(left[0] <= right[1] for left, right in zip(ranges, ranges[1:]))
        reader.close()

    def test_line_cap_counts_unterminated_final_line(self):
        tui_module, _ = self._reader_api()
        data = b"zero\none\ntwo\nthree\npartial"

        front, _, _ = tui_module._LogReader._trim_front(data, 0, 4)
        back, _, _ = tui_module._LogReader._trim_back(data, 0, 4)

        assert front == b"one\ntwo\nthree\npartial"
        assert back == b"zero\none\ntwo\nthree\n"
        assert tui_module._LogReader._line_count(front) == 4
        assert tui_module._LogReader._line_count(back) == 4

    def test_short_down_read_uses_actual_end_without_skipping(self, tmp_path):
        import threading

        tui_module, reader = self._reader_api()
        path = tmp_path / "history.log"
        expected = b"".join(f"line-{index}\n".encode() for index in range(8))
        path.write_bytes(expected)
        cancel = threading.Event()

        with patch.object(tui_module, "LOG_INITIAL_LINES", 2), patch.object(
            tui_module, "LOG_PAGE_LINES", 2
        ):
            snapshot = reader.read(path, tmp_path, False, "start", cancel)
            original_read = reader._read_range
            shortened = False

            def short_once(fd, start, end, event):
                nonlocal shortened
                data = original_read(fd, start, end, event)
                if not shortened:
                    shortened = True
                    return data[: max(1, len(data) // 2)]
                return data

            with patch.object(reader, "_read_range", side_effect=short_once):
                snapshot = reader.read(path, tmp_path, False, "down", cancel)
                first_short_end = snapshot.end
                assert snapshot.data == expected[snapshot.start : snapshot.end]
                assert snapshot.end < path.stat().st_size

                snapshot = reader.read(path, tmp_path, False, "down", cancel)
                assert snapshot.end > first_short_end
                assert snapshot.data == expected[snapshot.start : snapshot.end]
        reader.close()

    def test_short_poll_read_retries_from_actual_end(self, tmp_path):
        import threading

        _, reader = self._reader_api()
        path = tmp_path / "live.log"
        expected = b"initial\n"
        path.write_bytes(expected)
        cancel = threading.Event()
        snapshot = reader.read(path, tmp_path, True, "tail", cancel)

        addition = b"one\ntwo\nthree\n"
        expected += addition
        with path.open("ab") as stream:
            stream.write(addition)
        original_read = reader._read_range
        shortened = False

        def short_once(fd, start, end, event):
            nonlocal shortened
            data = original_read(fd, start, end, event)
            if not shortened:
                shortened = True
                return data[: max(1, len(data) // 2)]
            return data

        with patch.object(reader, "_read_range", side_effect=short_once):
            snapshot = reader.read(path, tmp_path, True, "poll", cancel)
            short_end = snapshot.end
            assert snapshot.data == expected[snapshot.start : snapshot.end]
            assert snapshot.more_below is True

            snapshot = reader.read(path, tmp_path, True, "poll", cancel)
            assert snapshot.end > short_end
            assert snapshot.end == snapshot.size == len(expected)
            assert snapshot.data == expected[snapshot.start : snapshot.end]
        reader.close()

    def test_short_up_read_invalidates_reader_then_recovers(self, tmp_path):
        import threading

        tui_module, reader = self._reader_api()
        path = tmp_path / "history.log"
        expected = b"".join(f"line-{index}\n".encode() for index in range(8))
        path.write_bytes(expected)
        cancel = threading.Event()

        with patch.object(tui_module, "LOG_INITIAL_LINES", 2), patch.object(
            tui_module, "LOG_PAGE_LINES", 2
        ):
            reader.read(path, tmp_path, False, "tail", cancel)
            original_read = reader._read_range

            def short_prefix(fd, start, end, event):
                data = original_read(fd, start, end, event)
                return data[:-1]

            with patch.object(reader, "_read_range", side_effect=short_prefix):
                with pytest.raises(OSError, match="changed during backward"):
                    reader.read(path, tmp_path, False, "up", cancel)
            assert reader._fd is None

            recovered = reader.read(path, tmp_path, False, "tail", cancel)
            assert recovered.reset is True
            assert recovered.data == expected[recovered.start : recovered.end]
        reader.close()


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

    def test_toggle_on_forces_reload(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        log_file = base / "running.log"
        log_file.write_bytes(b"z" * 50)

        state = TUIState(
            show_log=True, log_tail_path=log_file, log_tail_live=True, log_more_above=True
        )
        state.toggle_log()  # off
        state.toggle_log()  # on — should force a fresh tail reload
        assert state.log_tail_path is None
        assert state.log_tail_live is False
        assert state.log_more_above is False


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


class TestTuiStartup:
    def test_opens_without_blocking_runtime_rpc(self, monkeypatch):
        import importlib
        module = importlib.import_module("mirror.command.tui")
        constructor = MagicMock()
        monkeypatch.setattr(module, "MirrorTUI", constructor)
        with patch.object(mirror.socket.master, "get_runtime_info") as rpc:
            module.tui("/tmp/explicit.sock")
        constructor.assert_called_once_with(socket_path="/tmp/explicit.sock")
        constructor.return_value.run.assert_called_once()
        rpc.assert_not_called()


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


# ---------------------------------------------------------------------------
# New tests: compute_status_counts
# ---------------------------------------------------------------------------


class TestComputeStatusCounts:
    def test_empty_list(self):
        counts = compute_status_counts([])
        assert counts == {"total": 0, "SYNC": 0, "ACTIVE": 0, "ERROR": 0, "UNKNOWN": 0, "disabled": 0}

    def test_mixed_statuses(self):
        pkgs = [
            _make_package("a", status="SYNC"),
            _make_package("b", status="ACTIVE"),
            _make_package("c", status="ERROR"),
            _make_package("d", status="UNKNOWN"),
            _make_package("e", status="ACTIVE"),
        ]
        counts = compute_status_counts(pkgs)
        assert counts["total"] == 5
        assert counts["SYNC"] == 1
        assert counts["ACTIVE"] == 2
        assert counts["ERROR"] == 1
        assert counts["UNKNOWN"] == 1
        assert counts["disabled"] == 0

    def test_all_disabled(self):
        pkgs = [
            _make_package("a", disabled=True, status="UNKNOWN"),
            _make_package("b", disabled=True, status="UNKNOWN"),
        ]
        counts = compute_status_counts(pkgs)
        assert counts["disabled"] == 2
        assert counts["total"] == 2


# ---------------------------------------------------------------------------
# New tests: apply_sort
# ---------------------------------------------------------------------------


class TestApplySort:
    def test_default_preserves_order(self):
        pkgs = [_make_package("c"), _make_package("a"), _make_package("b")]
        result = apply_sort(pkgs, "default")
        assert [p.pkgid for p in result] == ["c", "a", "b"]

    def test_status_priority_order(self):
        pkgs = [
            _make_package("u", status="UNKNOWN"),
            _make_package("a", status="ACTIVE"),
            _make_package("e", status="ERROR"),
            _make_package("s", status="SYNC"),
        ]
        result = apply_sort(pkgs, "status")
        statuses = [p.status for p in result]
        assert statuses == ["ERROR", "SYNC", "ACTIVE", "UNKNOWN"]

    def test_ago_oldest_first(self):
        pkgs = [
            _make_package("recent", lastsuccesstime=1_000_000.0),
            _make_package("never"),  # lastsuccesstime=0
            _make_package("old", lastsuccesstime=500_000.0),
        ]
        result = apply_sort(pkgs, "ago")
        assert result[0].pkgid == "never"
        assert result[1].pkgid == "old"
        assert result[2].pkgid == "recent"

    def test_pkgid_alpha(self):
        pkgs = [_make_package("Zebra"), _make_package("apple"), _make_package("Mango")]
        result = apply_sort(pkgs, "pkgid")
        assert [p.pkgid for p in result] == ["apple", "Mango", "Zebra"]


# ---------------------------------------------------------------------------
# New tests: apply_filter
# ---------------------------------------------------------------------------


class TestApplyFilter:
    def test_case_insensitive_match(self):
        pkgs = [_make_package("Debian"), _make_package("ubuntu"), _make_package("PyPI")]
        result = apply_filter(pkgs, "deb")
        assert len(result) == 1
        assert result[0].pkgid == "Debian"

    def test_no_match_returns_empty(self):
        pkgs = [_make_package("debian"), _make_package("ubuntu")]
        result = apply_filter(pkgs, "arch")
        assert result == []

    def test_empty_needle_returns_all(self):
        pkgs = [_make_package("a"), _make_package("b")]
        result = apply_filter(pkgs, "")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# New tests: visible_packages (sort + filter pipeline)
# ---------------------------------------------------------------------------


class TestVisiblePackages:
    def test_sort_then_filter_pipeline(self):
        state = TUIState()
        state.packages = [
            _make_package("debian", status="ERROR"),
            _make_package("ubuntu", status="ACTIVE"),
            _make_package("archlinux", status="ERROR"),
        ]
        state.sort_mode = "status"
        state.filter_text = "arch"
        result = visible_packages(state)
        # After sort by status (ERROR first), filter keeps only "archlinux"
        assert len(result) == 1
        assert result[0].pkgid == "archlinux"


# ---------------------------------------------------------------------------
# New tests: selection by pkgid preservation
# ---------------------------------------------------------------------------


class TestSelectionByPkgid:
    def test_sort_change_preserves_pkgid(self):
        state = TUIState()
        state.packages = [_make_package("a"), _make_package("z"), _make_package("m")]
        state.selected_pkgid = "m"
        state.sort_mode = "pkgid"
        # After sort alphabetically: a, m, z — selected_pkgid "m" still present
        vis = visible_packages(state)
        ids = [p.pkgid for p in vis]
        assert "m" in ids
        assert state.selected_pkgid == "m"

    def test_filter_change_falls_back_to_first_when_filtered_out(self):
        state = TUIState()
        state.packages = [_make_package("debian"), _make_package("ubuntu")]
        state.selected_pkgid = "ubuntu"
        state.filter_text = "deb"
        state.fix_selection()
        assert state.selected_pkgid == "debian"

    def test_empty_visible_list_clears_selection(self):
        state = TUIState()
        state.packages = [_make_package("debian")]
        state.selected_pkgid = "debian"
        state.filter_text = "xyz"
        state.fix_selection()
        assert state.selected_pkgid == ""

    def test_insertion_preserves_pkgid(self):
        state = TUIState()
        state.packages = [_make_package("a"), _make_package("b")]
        state.selected_pkgid = "b"
        # Add a new package at the front
        state.packages = [_make_package("new"), _make_package("a"), _make_package("b")]
        vis = visible_packages(state)
        ids = [p.pkgid for p in vis]
        assert "b" in ids
        assert state.selected_pkgid == "b"


# ---------------------------------------------------------------------------
# New tests: pause short-circuits _poll_once
# ---------------------------------------------------------------------------


class TestPauseShortCircuit:
    def test_paused_does_not_call_list_packages(self):
        tui_instance = MirrorTUI(socket_path="/tmp/fake.sock")
        tui_instance._state.paused = True

        client = MagicMock()
        tui_instance._client = client

        mock_app = MagicMock()
        result = asyncio.run(tui_instance._poll_once(mock_app, was_connected=True))

        assert result is True
        client.list_packages.assert_not_called()


# ---------------------------------------------------------------------------
# New tests: modal-key precedence
# ---------------------------------------------------------------------------


class TestModalKeyPrecedence:
    """
    For each modal state (dialog, show_help, filter_input_active) and each
    guarded key (j, x, r, Tab, s, /, p), assert that _modal_active returns True.
    The keybinding handlers call _modal_active and early-return when True.
    """

    @pytest.mark.parametrize("modal_flag,modal_value", [
        ("dialog", "set"),
        ("show_help", True),
        ("filter_input_active", True),
    ])
    @pytest.mark.parametrize("key_name", ["j", "x", "r", "tab", "s", "slash", "p"])
    def test_modal_active_blocks_key(self, modal_flag, modal_value, key_name):
        state = TUIState()
        if modal_flag == "dialog":
            state.open_dialog("start", "debian")
        else:
            setattr(state, modal_flag, modal_value)

        assert _modal_active(state) is True

    def test_tab_blocked_by_show_help(self):
        # Regression: Tab was only guarded against dialog, not show_help/filter.
        state = TUIState()
        state.show_help = True
        assert _modal_active(state) is True

    def test_tab_blocked_by_filter_input_active(self):
        state = TUIState()
        state.filter_input_active = True
        assert _modal_active(state) is True

    def test_no_modal_returns_false(self):
        state = TUIState()
        assert _modal_active(state) is False


# ---------------------------------------------------------------------------
# New tests: _apply_runtime_info stores daemon_started_at and localtimezone
# ---------------------------------------------------------------------------


class TestApplyRuntimeInfo:
    def test_stores_daemon_started_at(self):
        tui_instance = MirrorTUI(socket_path="/tmp/fake.sock")
        tui_instance._apply_runtime_info({"daemon_started_at": 1234567890.0})
        assert tui_instance._daemon_started_at == 1234567890.0

    def test_stores_localtimezone(self):
        tui_instance = MirrorTUI(socket_path="/tmp/fake.sock")
        tui_instance._apply_runtime_info({"localtimezone": "Asia/Seoul"})
        assert tui_instance._localtimezone == "Asia/Seoul"

    def test_none_is_noop(self):
        tui_instance = MirrorTUI(socket_path="/tmp/fake.sock")
        tui_instance._apply_runtime_info(None)
        assert tui_instance._daemon_started_at == 0.0
        assert tui_instance._localtimezone == ""


# ---------------------------------------------------------------------------
# New tests: build_help_text import check
# ---------------------------------------------------------------------------


class TestBuildHelpText:
    def test_returns_formatted_text(self):
        from prompt_toolkit.formatted_text import FormattedText
        result = build_help_text()
        assert isinstance(result, FormattedText)

    def test_contains_key_names(self):
        result = build_help_text()
        text = "".join(t for _, t in result)
        assert "j / k" in text
        assert "?" in text
        assert "quit" in text.lower()


class _SizedDummyOutput(DummyOutput):
    def __init__(self, columns: int = 80, rows: int = 24) -> None:
        self._size = Size(rows=rows, columns=columns)

    def get_size(self) -> Size:
        return self._size


def _run_tui_keys(
    tui: MirrorTUI,
    keys: str,
    *,
    columns: int = 80,
    rows: int = 24,
) -> Application:
    """Run keys through prompt_toolkit's real input and key processor."""
    layout, log_area = tui._build_layout()
    bindings = tui._build_keybindings(log_area)

    async def run() -> Application:
        with create_pipe_input() as pipe_input:
            app = Application(
                layout=layout,
                key_bindings=bindings,
                input=pipe_input,
                output=_SizedDummyOutput(columns, rows),
                full_screen=True,
            )
            app.ttimeoutlen = 0.01
            task = asyncio.create_task(app.run_async())
            await asyncio.sleep(0.02)
            if keys.endswith("\x1b"):
                pipe_input.send_text(keys[:-1])
                await asyncio.sleep(0.03)
                app.key_processor.feed(KeyPress(Keys.Escape))
                app.key_processor.process_keys()
                await asyncio.sleep(0.03)
            else:
                pipe_input.send_text(keys)
                await asyncio.sleep(0.08)
            if not task.done():
                app.exit()
            await task
            return app

    return asyncio.run(run())


class TestTUIApplicationBehavior:
    @pytest.mark.parametrize("columns", [40, 80, 120, 160])
    @pytest.mark.parametrize("show_log", [False, True])
    def test_rendered_table_fits_actual_pane_width(self, columns, show_log):
        tui = MirrorTUI(socket_path="/tmp/fake.sock")
        tui._state.show_log = show_log
        tui._state.packages = [
            _make_package("한글-package-with-a-long-name", status="ERROR")
        ]
        tui._state.selected_pkgid = tui._state.packages[0].pkgid

        _run_tui_keys(tui, "", columns=columns)

        pane_width = tui._table_window.render_info.window_width
        header = "".join(fragment[1] for fragment in tui._table_header_control.text())
        body = "".join(fragment[1] for fragment in tui._table_control.text())
        assert all(get_cwidth(line) <= pane_width for line in header.splitlines())
        assert all(get_cwidth(line) <= pane_width for line in body.splitlines())

    def test_selected_row_scrolls_with_table_cursor(self):
        tui = MirrorTUI(socket_path="/tmp/fake.sock")
        tui._state.packages = [_make_package(f"pkg-{index:03d}") for index in range(60)]
        tui._state.selected_pkgid = "pkg-000"

        _run_tui_keys(tui, "\x1b[B" * 35, rows=12)

        assert tui._state.selected_pkgid == "pkg-035"
        render_info = tui._table_window.render_info
        assert render_info.vertical_scroll > 0
        assert render_info.vertical_scroll <= 35
        assert 35 < render_info.vertical_scroll + render_info.window_height

    def test_boundary_navigation_does_not_reload_selection(self):
        tui = MirrorTUI(socket_path="/tmp/fake.sock")
        tui._state.packages = [_make_package("only")]
        tui._state.selected_pkgid = "only"
        tui._on_selection_change = MagicMock()

        _run_tui_keys(tui, "\x1b[B\x1b[A\x1b[H\x1b[F")

        tui._on_selection_change.assert_not_called()

    def test_tab_switches_focus_and_log_keys_do_not_change_package(self):
        tui = MirrorTUI(socket_path="/tmp/fake.sock")
        tui._state.packages = [_make_package("a"), _make_package("b")]
        tui._state.selected_pkgid = "a"
        tui._request_log_jump = MagicMock()
        layout, log_area = tui._build_layout()
        log_area.text = "one\ntwo\nthree\n"
        log_area.buffer.cursor_position = 0
        bindings = tui._build_keybindings(log_area)

        async def run() -> Application:
            with create_pipe_input() as pipe_input:
                app = Application(
                    layout=layout,
                    key_bindings=bindings,
                    input=pipe_input,
                    output=_SizedDummyOutput(),
                    full_screen=True,
                )
                app.ttimeoutlen = 0.01
                task = asyncio.create_task(app.run_async())
                await asyncio.sleep(0.02)
                pipe_input.send_text("\t\x1b[C\x1b[B\x1b[DjkgG")
                await asyncio.sleep(0.08)
                app.exit()
                await task
                return app

        app = asyncio.run(run())
        assert tui._state.selected_pkgid == "a"
        assert log_area.buffer.cursor_position == 4
        assert tui._request_log_jump.call_args_list == [call("start"), call("end")]
        assert app.layout.has_focus(log_area)

    def test_log_edges_request_adjacent_disk_pages(self):
        tui = MirrorTUI(socket_path="/tmp/fake.sock")
        tui._state.packages = [_make_package("a")]
        tui._state.selected_pkgid = "a"
        tui._state.log_more_above = True
        tui._request_log_page = MagicMock()
        layout, log_area = tui._build_layout()
        log_area.text = "one\ntwo\nthree"
        log_area.buffer.cursor_position = 0
        bindings = tui._build_keybindings(log_area)

        async def run() -> None:
            with create_pipe_input() as pipe_input:
                app = Application(
                    layout=layout,
                    key_bindings=bindings,
                    input=pipe_input,
                    output=_SizedDummyOutput(),
                    full_screen=True,
                )
                task = asyncio.create_task(app.run_async())
                await asyncio.sleep(0.02)
                pipe_input.send_text("\t\x1b[A\x1b[5~")
                await asyncio.sleep(0.05)
                tui._state.log_more_above = False
                tui._state.log_more_below = True
                log_area.buffer.cursor_position = len(log_area.text)
                pipe_input.send_text("\x1b[B\x1b[6~")
                await asyncio.sleep(0.05)
                app.exit()
                await task

        asyncio.run(run())
        assert tui._request_log_page.call_args_list == [
            call("up"),
            call("up"),
            call("down"),
            call("down"),
        ]

    def test_hiding_log_returns_focus_to_table(self):
        tui = MirrorTUI(socket_path="/tmp/fake.sock")
        tui._state.packages = [_make_package("a")]
        tui._state.selected_pkgid = "a"

        app = _run_tui_keys(tui, "\tl")

        assert tui._state.show_log is False
        assert app.layout.has_focus(tui._table_control)

    def test_help_overlay_blocks_native_log_navigation(self):
        tui = MirrorTUI(socket_path="/tmp/fake.sock")
        tui._state.packages = [_make_package("a")]
        tui._state.selected_pkgid = "a"
        layout, log_area = tui._build_layout()
        log_area.text = "one\ntwo\nthree\n"
        log_area.buffer.cursor_position = 0
        bindings = tui._build_keybindings(log_area)

        async def run() -> None:
            with create_pipe_input() as pipe_input:
                app = Application(
                    layout=layout,
                    key_bindings=bindings,
                    input=pipe_input,
                    output=_SizedDummyOutput(),
                    full_screen=True,
                )
                task = asyncio.create_task(app.run_async())
                await asyncio.sleep(0.02)
                pipe_input.send_text("\t?\x1b[C\x1b[BjkG")
                await asyncio.sleep(0.08)
                app.exit()
                await task

        asyncio.run(run())
        assert tui._state.show_help is True
        assert log_area.buffer.cursor_position == 0

    def test_filter_is_prefilled_and_accepts_reserved_printable_keys(self):
        tui = MirrorTUI(socket_path="/tmp/fake.sock")
        reserved = "qjkxrlsp?Gg/[]{}!@#$%^&*()"
        pasted = "PASTE/q?"
        expected = f"debian{reserved}{pasted}"
        tui._state.packages = [_make_package(expected), _make_package("ubuntu")]
        tui._state.selected_pkgid = expected
        tui._state.filter_text = "debian"

        keys = f"/{reserved}\x1b[200~{pasted}\x1b[201~\r"
        app = _run_tui_keys(tui, keys)

        assert tui._state.filter_text == expected
        assert tui._filter_buffer.text == expected
        assert tui._state.filter_input_active is False
        assert app.layout.has_focus(tui._table_control)

    def test_filter_escape_retains_live_filter(self):
        tui = MirrorTUI(socket_path="/tmp/fake.sock")
        tui._state.packages = [_make_package("debian"), _make_package("ubuntu")]
        tui._state.selected_pkgid = "debian"

        _run_tui_keys(tui, "/deb\x1b")

        assert tui._state.filter_text == "deb"
        assert tui._state.filter_input_active is False

    def test_resize_sort_and_filter_keep_selected_row_visible(self):
        tui = MirrorTUI(socket_path="/tmp/fake.sock")
        tui._state.packages = [_make_package(f"pkg-{index:03d}") for index in range(200)]
        tui._state.selected_pkgid = "pkg-150"
        layout, log_area = tui._build_layout()
        output = _SizedDummyOutput(columns=160, rows=20)
        bindings = tui._build_keybindings(log_area)

        async def run() -> None:
            with create_pipe_input() as pipe_input:
                app = Application(
                    layout=layout,
                    key_bindings=bindings,
                    input=pipe_input,
                    output=output,
                    full_screen=True,
                )
                task = asyncio.create_task(app.run_async())
                await asyncio.sleep(0.03)
                pipe_input.send_text("s/pkg-1\r")
                await asyncio.sleep(0.08)
                output._size = Size(rows=10, columns=40)
                app.invalidate()
                await asyncio.sleep(0.08)
                app.exit()
                await task

        asyncio.run(run())
        assert tui._state.selected_pkgid == "pkg-150"
        selected_index = [
            package.pkgid for package in visible_packages(tui._state)
        ].index("pkg-150")
        render_info = tui._table_window.render_info
        assert tui._table_control._render_width == render_info.window_width
        assert tui._table_header_control._render_width == render_info.window_width
        assert render_info.vertical_scroll <= selected_index
        assert selected_index < render_info.vertical_scroll + render_info.window_height


class TestTuiConnectionLifecycle:
    def test_slow_connect_does_not_block_event_loop(self):
        import threading

        entered, release = threading.Event(), threading.Event()
        client = MagicMock()
        client.list_packages.return_value = {'packages': []}
        client.get_runtime_info.return_value = {'mirrorname': 'test'}

        def connect():
            entered.set()
            assert release.wait(5)

        client.connect.side_effect = connect
        tui = MirrorTUI('/tmp/no-master.sock')

        async def scenario():
            pending = asyncio.create_task(tui._poll_once(MagicMock(), False))
            try:
                for _ in range(200):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.005)
                assert entered.is_set()
                assert not pending.done()
                tui._state.show_help = True
                release.set()
                assert await asyncio.wait_for(pending, 5)
                assert tui._state.show_help
                assert tui._client is client
            finally:
                release.set()
                await pending
                await tui._close_log_reader()
        with patch.object(mirror.socket.master, 'MasterClient', return_value=client):
            asyncio.run(scenario())

    def test_cancelled_connect_disconnects_unclaimed_client(self):
        import threading

        entered, release = threading.Event(), threading.Event()
        client = MagicMock()

        def connect():
            entered.set()
            assert release.wait(5)

        client.connect.side_effect = connect
        tui = MirrorTUI('/tmp/no-master.sock')

        async def scenario():
            pending = asyncio.create_task(tui._connect_client())
            try:
                for _ in range(200):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.005)
                assert entered.is_set()
                pending.cancel()
                await asyncio.sleep(0)
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(pending, 5)
                assert tui._client is None
                client.disconnect.assert_called_once()
            finally:
                release.set()
                await tui._close_log_reader()
        with patch.object(mirror.socket.master, 'MasterClient', return_value=client):
            asyncio.run(scenario())


class TestTuiDiskPageIntegration:
    _make_tui = TestTuiLogReading._make_tui
    def test_prepend_preserves_visible_line(self, tmp_path):
        from types import SimpleNamespace

        path = tmp_path / 'pages.log'
        path.write_text(''.join(f'line{i}\n' for i in range(3000)))
        tui, log = self._make_tui(tmp_path, _make_package(runninglog=str(path)))
        app = MagicMock()
        app.layout.has_focus.return_value = True
        tui._app = app

        async def scenario():
            try:
                await tui._poll_log_once(app, log)
                log.buffer.cursor_position = 0
                log.window.vertical_scroll = 0
                log.window.render_info = SimpleNamespace(vertical_scroll=0, displayed_lines=[0, 1])
                before = log.buffer.document.current_line
                await tui._poll_log_once(app, log)
                assert log.buffer.document.current_line == before
                assert log.window.vertical_scroll == LOG_PAGE_LINES
                assert tui._log_snapshot.start > 0
            finally:
                await tui._close_log_reader()
        asyncio.run(scenario())

    def test_segment_page_does_not_automatically_reverse_direction(self, tmp_path):
        import importlib
        from types import SimpleNamespace

        module = importlib.import_module('mirror.command.tui')
        path = tmp_path / 'single-line.log'
        path.write_text('abcdefghijklmnopqrstuvxyz' * 5)
        tui, log = self._make_tui(tmp_path, _make_package(runninglog=str(path)))
        app = MagicMock()
        app.layout.has_focus.return_value = True
        tui._app = app

        async def scenario():
            try:
                tui._request_log_jump('start')
                await tui._poll_log_once(app, log)
                assert tui._log_snapshot.start == 0
                log.window.render_info = SimpleNamespace(vertical_scroll=0, displayed_lines=[0])
                log.buffer.cursor_position = len(log.text)
                tui._request_log_page('down')
                await tui._poll_log_once(app, log)
                start = tui._log_snapshot.start
                assert start > 0
                await tui._poll_log_once(app, log)
                assert tui._log_snapshot.start == start
                tui._request_log_page('up')
                await tui._poll_log_once(app, log)
                assert tui._log_snapshot.start < start
            finally:
                await tui._close_log_reader()
        with patch.object(module, 'LOG_MAX_LOADED_BYTES', 17):
            asyncio.run(scenario())


def test_tui_shutdown_wakes_pending_rpc_and_closes_reader():
    import importlib
    import threading
    from prompt_toolkit.application import Application as RealApplication
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    module = importlib.import_module('mirror.command.tui')
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    client = MagicMock()

    def pending_rpc():
        entered.set()
        assert release.wait(5)
        finished.set()
        return {'packages': [_make_package('late-result').to_dict()]}

    client.list_packages.side_effect = pending_rpc
    client.disconnect.side_effect = release.set
    tui = MirrorTUI('/tmp/no-master.sock')
    tui._client = client

    async def scenario():
        with create_pipe_input() as pipe:
            def make_app(**kwargs):
                return RealApplication(input=pipe, output=DummyOutput(), **kwargs)

            with patch.object(module, 'Application', side_effect=make_app):
                task = asyncio.create_task(tui._run_async())
                try:
                    for _ in range(200):
                        if entered.is_set():
                            break
                        await asyncio.sleep(.005)
                    assert entered.is_set()
                    pipe.send_text('q')
                    await asyncio.wait_for(task, 5)
                    assert finished.is_set()
                    assert tui._client is None
                    assert tui._state.packages == []
                    assert not any(thread.is_alive() for thread in tui._log_executor._threads)
                    client.disconnect.assert_called_once()
                finally:
                    release.set()
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_opposite_page_queued_during_read_keeps_reader_and_display_in_sync(tmp_path):
    import importlib
    import threading

    module = importlib.import_module('mirror.command.tui')
    path = tmp_path / 'segments.log'
    path.write_bytes(b'abcdefghijklmnopqrstuvwxyz' * 10)
    tui, log = TestTuiLogReading()._make_tui(tmp_path, _make_package(runninglog=str(path)))
    entered, release = threading.Event(), threading.Event()
    original = tui._log_reader.read

    def delayed(*args):
        snapshot = original(*args)
        entered.set()
        assert release.wait(5)
        return snapshot

    async def scenario():
        app = MagicMock()
        try:
            await tui._poll_log_once(app, log)
            original_start = tui._log_snapshot.start
            tui._log_reader.read = delayed
            tui._request_log_page('up')
            pending = asyncio.create_task(tui._poll_log_once(app, log))
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(.005)
            assert entered.is_set()
            tui._request_log_page('down')
            release.set()
            await asyncio.wait_for(pending, 5)
            assert tui._log_snapshot.start == tui._log_reader._start
            assert tui._log_snapshot.start < original_start
            assert tui._log_jump == 'down'
            tui._log_reader.read = original
            await tui._poll_log_once(app, log)
            assert tui._log_snapshot.start == original_start
            assert tui._log_snapshot.start == tui._log_reader._start
        finally:
            release.set()
            await tui._close_log_reader()
    with patch.object(module, 'LOG_MAX_LOADED_BYTES', 17):
        asyncio.run(scenario())


def test_live_follow_survives_growth_between_read_and_eof_stat(tmp_path):
    path = tmp_path / 'growing.log'
    path.write_text('first\n')
    tui, log = TestTuiLogReading()._make_tui(tmp_path, _make_package(runninglog=str(path)))
    original = tui._log_reader._read_range
    grew = False

    def read_and_append(*args):
        nonlocal grew
        result = original(*args)
        if not grew:
            grew = True
            with path.open('a') as stream:
                stream.write('appended during read\n')
        return result

    tui._log_reader._read_range = read_and_append

    async def scenario():
        try:
            await tui._poll_log_once(MagicMock(), log)
            assert tui._log_snapshot.more_below
            assert tui._state.log_following
            await tui._poll_log_once(MagicMock(), log)
            assert log.text.endswith('appended during read\n')
            assert tui._state.log_following
        finally:
            await tui._close_log_reader()
    asyncio.run(scenario())
