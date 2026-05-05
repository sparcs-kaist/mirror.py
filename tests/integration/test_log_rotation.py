"""Log rotation test: per-package log is created and compressed after sync."""

import gzip

import pytest


@pytest.mark.integration
def test_per_package_log_created_and_compressed(mirror_stack):
    """After a completed rsync-test sync, a per-package log file exists and is gzipped.

    mirror/logger/core.py compresses logs on close_logger. After the sync finishes
    the log for that session should be a .gz file with valid gzip magic bytes.
    """
    mirror_stack.trigger_sync("rsync-test")
    mirror_stack.wait_for_status("rsync-test", "ACTIVE", timeout=30)

    log_files = mirror_stack.read_package_log_dir("rsync-test")
    assert log_files, (
        f"No log files found under {mirror_stack.log_dir}/packages/ for rsync-test"
    )

    gz_files = [p for p in log_files if p.suffix == ".gz"]
    assert gz_files, (
        f"No .gz log files found for rsync-test; files present: {[str(p) for p in log_files]}"
    )

    latest_gz = sorted(gz_files)[-1]
    raw_bytes = latest_gz.read_bytes()

    assert raw_bytes[:2] == b"\x1f\x8b", (
        f"Expected gzip magic bytes at start of {latest_gz}, got {raw_bytes[:2]!r}"
    )

    # Also verify the file is readable as valid gzip.
    with gzip.open(latest_gz, "rt") as fh:
        content = fh.read()

    assert content, f"Gzip log file {latest_gz} decompressed to empty content"
