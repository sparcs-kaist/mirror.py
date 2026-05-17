import subprocess
import sys
import time


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
