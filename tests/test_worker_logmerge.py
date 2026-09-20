import os
import subprocess
import sys
import time


def _wait_for(dest, needle, timeout=3):
    """Block until `needle` appears in dest, returning its content; raise on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if dest.exists():
            data = dest.read_bytes()
            if needle in data:
                return data
        time.sleep(0.05)
    raise AssertionError(f"{needle!r} did not appear in {dest}")


def test_logmerge_follows_late_created_files(tmp_path):
    dest = tmp_path / "package.log"
    source = tmp_path / "source.log"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mirror.worker.logmerge",
            "--dest",
            str(dest),
            "--source",
            f"ftpsync={source}",
            "--poll",
            "0.05",
        ]
    )
    try:
        time.sleep(0.1)
        source.write_text("first\n")
        deadline = time.time() + 3
        while time.time() < deadline:
            if dest.exists() and b"[ftpsync] first" in dest.read_bytes():
                break
            time.sleep(0.05)
        else:
            raise AssertionError("logmerge did not copy late-created source")
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_logmerge_final_drains_partial_line(tmp_path):
    dest = tmp_path / "package.log"
    source = tmp_path / "source.log"
    source.write_text("partial")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mirror.worker.logmerge",
            "--dest",
            str(dest),
            "--source",
            f"rsync:error={source}",
            "--poll",
            "0.05",
        ]
    )
    try:
        deadline = time.time() + 3
        while time.time() < deadline and not dest.exists():
            time.sleep(0.05)
        assert dest.exists()
        proc.terminate()
        proc.wait(timeout=3)
        assert b"[rsync:error] partial" in dest.read_bytes()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)


def test_logmerge_no_duplicate_across_rotation(tmp_path):
    # Reproduces the ftpsync double-logging: lines tailed live before rotation
    # must not be re-emitted when the source is rotated to `<path>.0`.
    dest = tmp_path / "package.log"
    source = tmp_path / "source.log"
    source.write_text("l1\nl2\nl3\n")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mirror.worker.logmerge",
            "--dest",
            str(dest),
            "--source",
            f"ftpsync={source}",
            "--poll",
            "0.05",
        ]
    )
    try:
        # Ensure the first lines were tailed live (emitted once) before rotating.
        _wait_for(dest, b"[ftpsync] l3\n")

        # Rotate the file out from under logmerge (as savelog does), then append a
        # trailing line to the rotated inode still held open by logmerge.
        rotated = tmp_path / "source.log.0"
        os.rename(source, rotated)
        with open(rotated, "a") as fh:
            fh.write("l4\n")

        data = _wait_for(dest, b"[ftpsync] l4\n")
    finally:
        proc.terminate()
        proc.wait(timeout=3)

    data = dest.read_bytes()
    for line in (b"[ftpsync] l1\n", b"[ftpsync] l2\n", b"[ftpsync] l3\n", b"[ftpsync] l4\n"):
        assert data.count(line) == 1, (line, data)


def test_logmerge_follows_rotation_to_new_file(tmp_path):
    # After rotation creates a fresh file at the same path, logmerge must switch
    # to the new inode and emit every line exactly once.
    dest = tmp_path / "package.log"
    source = tmp_path / "source.log"
    source.write_text("a1\na2\n")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mirror.worker.logmerge",
            "--dest",
            str(dest),
            "--source",
            f"ftpsync={source}",
            "--poll",
            "0.05",
        ]
    )
    try:
        _wait_for(dest, b"[ftpsync] a2\n")

        os.rename(source, tmp_path / "source.log.0")
        source.write_text("b1\nb2\n")

        _wait_for(dest, b"[ftpsync] b2\n")
    finally:
        proc.terminate()
        proc.wait(timeout=3)

    data = dest.read_bytes()
    for line in (b"[ftpsync] a1\n", b"[ftpsync] a2\n", b"[ftpsync] b1\n", b"[ftpsync] b2\n"):
        assert data.count(line) == 1, (line, data)
