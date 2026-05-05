import pytest

from mirror.worker import process


@pytest.fixture(autouse=True)
def stub_worker_notification(monkeypatch):
    """Stub send_finished_notification so prune_finished succeeds without a WorkerServer.

    In production the worker process owns a WorkerServer which is the broadcast
    target for job_finished notifications. In-process tests like this one do
    not stand up a server, so the real notification call would raise and
    `prune_finished` would never delete the job entry. Stubbing keeps the test
    focused on the create/stop/prune contract.
    """
    monkeypatch.setattr(
        "mirror.socket.worker.send_finished_notification",
        lambda *args, **kwargs: None,
    )
    yield
    # Belt-and-suspenders: drop any leftover entries this test created so
    # sibling tests start with a clean _jobs map.
    with process._jobs_lock:
        process._jobs.clear()


def test_worker_id_uniqueness():
    worker_id = "unique_test_worker"
    command = ["sleep", "1"]

    worker1 = process.create(worker_id, command, {}, None, None, 0)
    assert worker1.id == worker_id

    with pytest.raises(ValueError) as excinfo:
        process.create(worker_id, command, {}, None, None, 0)

    assert f"Worker with ID '{worker_id}' already exists." in str(excinfo.value)

    worker1.stop()
    process.prune_finished()

    worker2 = process.create(worker_id, command, {}, None, None, 0)
    assert worker2.id == worker_id
    worker2.stop()
    process.prune_finished()
