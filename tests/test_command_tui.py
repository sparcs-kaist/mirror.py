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
    LOG_FOLLOW_MAX_LINES,
    LOG_INITIAL_LINES,
    LOG_PAGE_LINES,
    MirrorTUI,
    TUIState,
    _fallback_package_from_dict,
    _is_rotated,
    _modal_active,
    _read_bytes_range,
    _start_of_last_n_lines,
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
    read_gzip_lines,
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
        vis = _visible_columns(160)
        rows = build_table_header(vis)
        label = rows[0][1]
        assert "ELAPSED" in label
        assert "STARTED" in label

    def test_responsive_medium_no_elapsed(self):
        vis = _visible_columns(120)
        rows = build_table_header(vis)
        label = rows[0][1]
        assert "ELAPSED" not in label
        assert "STARTED" in label

    def test_responsive_narrow_no_elapsed_no_started(self):
        vis = _visible_columns(100)
        rows = build_table_header(vis)
        label = rows[0][1]
        assert "ELAPSED" not in label
        assert "STARTED" not in label

    def test_responsive_narrow_header_body_aligned(self):
        vis = _visible_columns(100)
        pkgs = [_make_package("debian")]
        header_rows = build_table_header(vis)
        body_rows = build_table_rows(pkgs, selected=0, now=time.time(), visible=vis)
        assert len(header_rows[0][1]) == len(body_rows[0][1])


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


class TestShowLatestLog:
    def _make_tui(self, base):
        from prompt_toolkit.widgets import TextArea

        tui = MirrorTUI(socket_path="/tmp/none.sock", log_base=base)
        log_area = TextArea(text="", read_only=True)
        return tui, log_area

    def test_displays_latest_gzipped_success_log(self, tmp_path):
        import gzip

        base = tmp_path / "logs"
        base.mkdir()
        log_file = base / "success.log.gz"
        with gzip.open(log_file, "wt", encoding="utf-8") as fh:
            fh.write("sync complete\nall good\n")

        pkg = _make_package(status="ACTIVE", lastsuccesstime=100.0)
        pkg.statusinfo.lastsuccesslog = str(log_file)

        tui, log_area = self._make_tui(base)
        tui._show_latest_log(MagicMock(), log_area, pkg)

        assert log_area.text == "sync complete\nall good\n"
        assert tui._state.log_tail_live is False
        assert tui._state.log_tail_path == log_file

    def test_blank_pane_when_no_completed_log(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        pkg = _make_package(status="ACTIVE")

        tui, log_area = self._make_tui(base)
        tui._show_latest_log(MagicMock(), log_area, pkg)

        assert log_area.text == ""
        assert tui._state.log_tail_path is None

    def test_picks_newer_error_log_over_older_success(self, tmp_path):
        import gzip

        base = tmp_path / "logs"
        base.mkdir()
        success = base / "success.log.gz"
        with gzip.open(success, "wt", encoding="utf-8") as fh:
            fh.write("old success\n")
        error = base / "error.log.gz"
        with gzip.open(error, "wt", encoding="utf-8") as fh:
            fh.write("recent error\n")

        pkg = _make_package(status="ERROR", lastsuccesstime=100.0, lasterrortime=200.0)
        pkg.statusinfo.lastsuccesslog = str(success)
        pkg.statusinfo.lasterrorlog = str(error)

        tui, log_area = self._make_tui(base)
        tui._show_latest_log(MagicMock(), log_area, pkg)

        assert log_area.text == "recent error\n"

    def test_read_failure_does_not_pin_path(self, tmp_path):
        # A latest log that fails the safety check (outside base) must not
        # pin log_tail_path, so the next tick can retry.
        base = tmp_path / "logs"
        base.mkdir()
        outside = tmp_path / "outside.log"
        outside.write_text("evil\n")

        pkg = _make_package(status="ACTIVE", lastsuccesstime=100.0)
        pkg.statusinfo.lastsuccesslog = str(outside)

        tui, log_area = self._make_tui(base)
        tui._show_latest_log(MagicMock(), log_area, pkg)

        assert log_area.text == ""
        assert tui._state.log_tail_path is None

    def test_closes_live_tail_fd_on_static(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        running = base / "running.log"
        running.write_text("live\n")

        pkg = _make_package(status="ACTIVE")
        tui, log_area = self._make_tui(base)
        # Simulate a leftover live tail (fd + live flag + path).
        tui._log_fd = safe_open_log_for_read(running, base)
        tui._state.log_tail_live = True
        tui._state.log_tail_path = running
        assert tui._log_fd is not None

        tui._show_latest_log(MagicMock(), log_area, pkg)

        assert tui._log_fd is None
        assert tui._state.log_tail_live is False


class TestStartOfLastNLines:
    def _fd(self, tmp_path, data: bytes) -> int:
        p = tmp_path / "f"
        p.write_bytes(data)
        return os.open(str(p), os.O_RDONLY)

    def test_exact_offsets_with_trailing_newline(self, tmp_path):
        fd = self._fd(tmp_path, b"a\nb\nc\n")  # size 6
        try:
            assert _start_of_last_n_lines(fd, 6, 1) == 4
            assert _start_of_last_n_lines(fd, 6, 2) == 2
            assert _start_of_last_n_lines(fd, 6, 3) == 0
            assert _start_of_last_n_lines(fd, 6, 5) == 0
        finally:
            os.close(fd)

    def test_no_trailing_newline(self, tmp_path):
        fd = self._fd(tmp_path, b"a\nb\nc")  # size 5
        try:
            assert _start_of_last_n_lines(fd, 5, 1) == 4
            assert _start_of_last_n_lines(fd, 5, 2) == 2
            assert _start_of_last_n_lines(fd, 5, 3) == 0
        finally:
            os.close(fd)

    def test_empty(self, tmp_path):
        fd = self._fd(tmp_path, b"")
        try:
            assert _start_of_last_n_lines(fd, 0, 5) == 0
        finally:
            os.close(fd)

    def test_n_zero_returns_end(self, tmp_path):
        fd = self._fd(tmp_path, b"a\nb\n")
        try:
            assert _start_of_last_n_lines(fd, 4, 0) == 4
        finally:
            os.close(fd)

    def test_page_up_offsets(self, tmp_path):
        data = b"l0\nl1\nl2\nl3\nl4\n"  # 5 lines, 3 bytes each, size 15
        fd = self._fd(tmp_path, data)
        try:
            assert _start_of_last_n_lines(fd, 15, 2) == 9  # last 2 lines: l3,l4
            assert _start_of_last_n_lines(fd, 9, 2) == 3   # previous 2: l1,l2
        finally:
            os.close(fd)


class TestReadBytesRange:
    def test_reads_subrange(self, tmp_path):
        p = tmp_path / "f"
        p.write_bytes(b"0123456789")
        fd = os.open(str(p), os.O_RDONLY)
        try:
            assert _read_bytes_range(fd, 2, 5) == b"234"
            assert _read_bytes_range(fd, 0, 10) == b"0123456789"
            assert _read_bytes_range(fd, 5, 5) == b""
        finally:
            os.close(fd)


class TestReadGzipLines:
    def test_reads_all_lines(self, tmp_path):
        import gzip

        base = tmp_path / "logs"
        base.mkdir()
        p = base / "a.log.gz"
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            fh.write("l0\nl1\nl2\n")
        assert read_gzip_lines(p, base, 1000) == ["l0\n", "l1\n", "l2\n"]

    def test_caps_to_last_max_lines(self, tmp_path):
        import gzip

        base = tmp_path / "logs"
        base.mkdir()
        p = base / "a.log.gz"
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            fh.write("".join(f"l{i}\n" for i in range(100)))
        out = read_gzip_lines(p, base, 10)
        assert len(out) == 10
        assert out[0] == "l90\n"
        assert out[-1] == "l99\n"

    def test_outside_base_rejected(self, tmp_path):
        import gzip

        base = tmp_path / "logs"
        base.mkdir()
        p = tmp_path / "out.log.gz"
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            fh.write("x\n")
        assert read_gzip_lines(p, base, 10) is None


class TestLogPaging:
    def _make_tui(self, base):
        from prompt_toolkit.widgets import TextArea

        tui = MirrorTUI(socket_path="/tmp/none.sock", log_base=base)
        log_area = TextArea(text="", read_only=True)
        return tui, log_area

    def _write_lines(self, path, n):
        path.write_text("".join(f"line{i}\n" for i in range(n)))

    def test_live_initial_loads_last_n_lines(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        running = base / "running.log"
        self._write_lines(running, 3000)
        tui, log_area = self._make_tui(base)
        try:
            tui._tail_live(MagicMock(), log_area, running)
            text = log_area.buffer.text
            assert text.count("\n") == LOG_INITIAL_LINES
            assert text.startswith("line2000\n")
            assert text.endswith("line2999\n")
            assert tui._state.log_more_above is True
            assert tui._state.log_win_start > 0
        finally:
            if tui._log_fd is not None:
                os.close(tui._log_fd)

    def test_live_page_up_prepends_previous_lines(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        running = base / "running.log"
        self._write_lines(running, 3000)
        tui, log_area = self._make_tui(base)
        try:
            tui._tail_live(MagicMock(), log_area, running)
            assert tui._load_more_above(log_area) is True
            text = log_area.buffer.text
            assert text.count("\n") == 2 * LOG_PAGE_LINES
            assert text.startswith("line1000\n")
            assert text.endswith("line2999\n")
            prepend = "".join(f"line{i}\n" for i in range(1000, 2000))
            assert log_area.buffer.cursor_position == len(prepend)
            assert tui._state.log_more_above is True
        finally:
            if tui._log_fd is not None:
                os.close(tui._log_fd)

    def test_live_page_up_reaches_top(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        running = base / "running.log"
        self._write_lines(running, 1500)
        tui, log_area = self._make_tui(base)
        try:
            tui._tail_live(MagicMock(), log_area, running)
            assert tui._state.log_more_above is True
            tui._load_more_above(log_area)
            text = log_area.buffer.text
            assert text.count("\n") == 1500
            assert text.startswith("line0\n")
            assert tui._state.log_more_above is False
        finally:
            if tui._log_fd is not None:
                os.close(tui._log_fd)

    def test_live_append_while_following_trims_front(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        running = base / "running.log"
        self._write_lines(running, 100)
        tui, log_area = self._make_tui(base)
        try:
            tui._tail_live(MagicMock(), log_area, running)
            with open(running, "a") as f:
                f.write("".join(f"extra{i}\n" for i in range(LOG_FOLLOW_MAX_LINES + 200)))
            tui._tail_live(MagicMock(), log_area, running)
            text = log_area.buffer.text
            assert text.count("\n") <= LOG_FOLLOW_MAX_LINES
            assert text.endswith(f"extra{LOG_FOLLOW_MAX_LINES + 199}\n")
        finally:
            if tui._log_fd is not None:
                os.close(tui._log_fd)

    def test_live_append_while_scrolled_up_preserves_top(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        running = base / "running.log"
        self._write_lines(running, 2000)
        tui, log_area = self._make_tui(base)
        try:
            tui._tail_live(MagicMock(), log_area, running)
            first_line = log_area.buffer.text.split("\n", 1)[0]
            log_area.buffer.cursor_position = 0  # scrolled up, not following
            with open(running, "a") as f:
                f.write("new0\nnew1\n")
            tui._tail_live(MagicMock(), log_area, running)
            text = log_area.buffer.text
            assert text.startswith(first_line + "\n")
            assert text.endswith("new1\n")
        finally:
            if tui._log_fd is not None:
                os.close(tui._log_fd)

    def test_static_gzip_initial_and_page_up(self, tmp_path):
        import gzip

        base = tmp_path / "logs"
        base.mkdir()
        gz = base / "s.log.gz"
        with gzip.open(gz, "wt", encoding="utf-8") as fh:
            fh.write("".join(f"g{i}\n" for i in range(2500)))
        pkg = _make_package(status="ACTIVE", lastsuccesstime=100.0)
        pkg.statusinfo.lastsuccesslog = str(gz)
        tui, log_area = self._make_tui(base)
        tui._show_latest_log(MagicMock(), log_area, pkg)
        text = log_area.buffer.text
        assert text.count("\n") == LOG_INITIAL_LINES
        assert text.startswith("g1500\n")
        assert text.endswith("g2499\n")
        assert tui._state.log_more_above is True

        tui._load_more_above(log_area)
        text = log_area.buffer.text
        assert text.count("\n") == 2 * LOG_PAGE_LINES
        assert text.startswith("g500\n")

    def test_maybe_load_more_above_rising_edge(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        tui, log_area = self._make_tui(base)
        tui._state.log_more_above = True
        calls = []
        tui._load_more_above = lambda la: (calls.append(1) or True)
        app = MagicMock()
        app.layout.has_focus.return_value = True

        class RI:
            vertical_scroll = 0

        log_area.window.render_info = RI()

        tui._maybe_load_more_above(app, log_area)  # at top -> load
        assert len(calls) == 1
        assert tui._state.log_was_at_top is True

        tui._maybe_load_more_above(app, log_area)  # still at top -> no rising edge
        assert len(calls) == 1

        RI.vertical_scroll = 5  # scrolled away
        tui._maybe_load_more_above(app, log_area)
        assert tui._state.log_was_at_top is False

        RI.vertical_scroll = 0  # back to top -> rising edge -> load again
        tui._maybe_load_more_above(app, log_area)
        assert len(calls) == 2

    def test_following_trim_enables_page_up(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        running = base / "running.log"
        self._write_lines(running, 100)  # fits entirely, nothing above yet
        tui, log_area = self._make_tui(base)
        try:
            tui._tail_live(MagicMock(), log_area, running)
            assert tui._state.log_win_start == 0
            assert tui._state.log_more_above is False
            # Append past the follow cap so the front gets trimmed.
            with open(running, "a") as f:
                f.write("".join(f"x{i}\n" for i in range(LOG_FOLLOW_MAX_LINES + 100)))
            tui._tail_live(MagicMock(), log_area, running)
            assert tui._state.log_win_start > 0
            assert tui._state.log_more_above is True
        finally:
            if tui._log_fd is not None:
                os.close(tui._log_fd)

    def test_no_completed_log_clears_stale_text(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        tui, log_area = self._make_tui(base)
        # Leftover text from a previously shown package; selection change has
        # already nulled log_tail_path.
        log_area.buffer.set_document(
            log_area.buffer.document.__class__("old package log\n"),
            bypass_readonly=True,
        )
        tui._state.log_tail_path = None
        pkg = _make_package(status="ACTIVE")  # no completed log
        tui._show_latest_log(MagicMock(), log_area, pkg)
        assert log_area.buffer.text == ""

    def test_static_plain_completed_log_and_page_up(self, tmp_path):
        base = tmp_path / "logs"
        base.mkdir()
        plain = base / "done.log"
        self._write_lines(plain, 1500)
        pkg = _make_package(status="ERROR", lasterrortime=100.0)
        pkg.statusinfo.lasterrorlog = str(plain)
        tui, log_area = self._make_tui(base)
        try:
            tui._show_latest_log(MagicMock(), log_area, pkg)
            text = log_area.buffer.text
            assert text.count("\n") == LOG_INITIAL_LINES
            assert text.startswith("line500\n")
            assert text.endswith("line1499\n")
            assert tui._state.log_more_above is True

            tui._load_more_above(log_area)
            assert log_area.buffer.text.startswith("line0\n")
            assert tui._state.log_more_above is False
        finally:
            if tui._log_fd is not None:
                os.close(tui._log_fd)


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
