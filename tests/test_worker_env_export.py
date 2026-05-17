import json
import os
import sys
import time
import uuid

import pytest

from mirror.socket.worker import WorkerServer
import mirror.socket.worker as worker_module
import mirror.worker.process as process_module


@pytest.fixture()
def worker_server(tmp_path):
    """Spin up a WorkerServer on a temp socket; yield (server, socket_path)."""
    socket_path = tmp_path / "worker.sock"
    server = WorkerServer(socket_path=socket_path)
    server.set_version("test")
    try:
        server.start()
    except OSError as exc:
        pytest.skip(f"Cannot bind Unix socket: {exc}")
    time.sleep(0.1)
    yield server, socket_path
    server.stop()
    process_module._jobs.clear()


def _probe_command() -> list[str]:
    """Return a Python one-liner that dumps selected env keys as JSON."""
    keys = ["MIRROR_PUSH_TEST", "SSH_ORIGINAL_COMMAND", "SSH_CONNECTION"]
    script = (
        "import os, json, sys; "
        f"keys = {keys!r}; "
        "sys.stdout.write(json.dumps({k: os.environ.get(k) for k in keys}))"
    )
    return [sys.executable, "-c", script]


def _wait_for_job(job_id: str, timeout: float = 10.0) -> None:
    """Poll until the job is no longer running or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = process_module.get(job_id)
        if job is not None and not job.is_running:
            return
        time.sleep(0.1)
    raise TimeoutError(f"Job {job_id!r} did not finish within {timeout}s")


def test_env_export_explicit_keys(worker_server, tmp_path):
    """Env vars passed to execute_command must appear in the spawned subprocess."""
    server, socket_path = worker_server

    marker = f"hello-world-{uuid.uuid4().hex}"
    log_path = tmp_path / "probe.log"
    job_id = f"env-export-test-{uuid.uuid4().hex[:8]}"

    worker_module.execute_command(
        socket_path=socket_path,
        job_id=job_id,
        commandline=_probe_command(),
        env={
            "MIRROR_PUSH_TEST": marker,
            "SSH_ORIGINAL_COMMAND": "ftpsync sync:archive:debian",
            "SSH_CONNECTION": "203.0.113.10 54321 198.51.100.5 22",
        },
        uid=os.getuid(),
        gid=os.getgid(),
        log_path=str(log_path),
    )

    _wait_for_job(job_id)

    assert log_path.exists(), "log file was not created"
    raw = log_path.read_text().strip()
    assert raw, "log file is empty"
    parsed = json.loads(raw)

    assert parsed["MIRROR_PUSH_TEST"] == marker
    assert parsed["SSH_ORIGINAL_COMMAND"] == "ftpsync sync:archive:debian"
    assert parsed["SSH_CONNECTION"] == "203.0.113.10 54321 198.51.100.5 22"


def test_env_export_keys_absent_when_not_passed(monkeypatch, worker_server, tmp_path):
    """Keys not included in env= must not be visible in the subprocess."""
    server, socket_path = worker_server

    monkeypatch.delenv("MIRROR_PUSH_TEST", raising=False)
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)

    log_path = tmp_path / "probe_absent.log"
    job_id = f"env-absent-test-{uuid.uuid4().hex[:8]}"

    worker_module.execute_command(
        socket_path=socket_path,
        job_id=job_id,
        commandline=_probe_command(),
        env={},
        uid=os.getuid(),
        gid=os.getgid(),
        log_path=str(log_path),
    )

    _wait_for_job(job_id)

    assert log_path.exists(), "log file was not created"
    raw = log_path.read_text().strip()
    assert raw, "log file is empty"
    parsed = json.loads(raw)

    assert parsed["MIRROR_PUSH_TEST"] is None
    assert parsed["SSH_ORIGINAL_COMMAND"] is None
    assert parsed["SSH_CONNECTION"] is None
