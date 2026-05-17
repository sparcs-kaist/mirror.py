import os
import time
from unittest.mock import patch

from mirror.worker import process


def _cleanup(job_id: str) -> None:
    with process._jobs_lock:
        process._jobs.pop(job_id, None)


def test_log_helper_delays_finished_notification_until_drained(monkeypatch):
    sent = []

    def _send(job_id, success, returncode):
        sent.append((job_id, success, returncode))

    monkeypatch.setattr("mirror.socket.worker.send_finished_notification", _send)

    job_id = "helper_prune"
    process.create(
        job_id,
        ["/bin/sh", "-c", "exit 0"],
        {},
        os.getuid(),
        os.getgid(),
        0,
        log_helper_command=[
            "/bin/sh",
            "-c",
            "trap 'exit 0' TERM; while true; do sleep 1; done",
        ],
    )

    try:
        for _ in range(20):
            process.prune_finished()
            if sent:
                break
            time.sleep(0.1)
        assert sent == [(job_id, True, 0)]
        assert process.get(job_id) is None
    finally:
        job = process.get(job_id)
        if job is not None:
            job.stop()
        _cleanup(job_id)


def test_log_helper_start_failure_removes_job():
    job_id = "helper_fail"

    try:
        with patch("mirror.worker.process.subprocess.Popen", side_effect=FileNotFoundError("missing")):
            try:
                process.create(
                    job_id,
                    ["true"],
                    {},
                    os.getuid(),
                    os.getgid(),
                    0,
                    log_helper_command=["missing-helper"],
                )
            except FileNotFoundError:
                pass
            else:
                raise AssertionError("expected helper start failure")

        assert process.get(job_id) is None
    finally:
        _cleanup(job_id)


def test_info_includes_helper_state():
    job_id = "helper_info"
    job = process.create(
        job_id,
        ["/bin/sh", "-c", "sleep 0.2"],
        {},
        os.getuid(),
        os.getgid(),
        0,
        log_helper_command=[
            "/bin/sh",
            "-c",
            "trap 'exit 0' TERM; while true; do sleep 1; done",
        ],
    )
    try:
        info = job.info()
        assert info["helper_pid"] is not None
        assert info["helper_running"] is True
        assert info["helper_returncode"] is None
    finally:
        job.stop()
        _cleanup(job_id)
