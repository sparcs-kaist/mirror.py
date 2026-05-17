from types import SimpleNamespace
from unittest.mock import patch

import mirror.worker.process as process


def test_worker_chowns_only_log_paths_it_creates(tmp_path):
    log_path = tmp_path / "new" / "nested" / "job.log"
    job = process.Job(
        job_id="j",
        commandline=["true"],
        env={},
        uid=123,
        gid=456,
        nice=0,
        log_path=log_path,
    )

    fake_process = SimpleNamespace(pid=999)
    with patch("mirror.worker.process.os.geteuid", return_value=0), \
         patch("mirror.worker.process.os.chown") as chown_mock, \
         patch("mirror.worker.process.subprocess.Popen", return_value=fake_process):
        job.start()

    assert log_path.exists()
    chowned_paths = [call.args[0] for call in chown_mock.call_args_list]
    assert tmp_path / "new" in chowned_paths
    assert log_path.parent in chowned_paths
    assert log_path in chowned_paths


def test_worker_does_not_chown_existing_log_paths(tmp_path):
    log_path = tmp_path / "existing" / "job.log"
    log_path.parent.mkdir()
    log_path.write_text("old")
    job = process.Job(
        job_id="j",
        commandline=["true"],
        env={},
        uid=123,
        gid=456,
        nice=0,
        log_path=log_path,
    )

    fake_process = SimpleNamespace(pid=999)
    with patch("mirror.worker.process.os.geteuid", return_value=0), \
         patch("mirror.worker.process.os.chown") as chown_mock, \
         patch("mirror.worker.process.subprocess.Popen", return_value=fake_process):
        job.start()

    chowned_paths = [call.args[0] for call in chown_mock.call_args_list]
    assert log_path.parent not in chowned_paths
    assert log_path not in chowned_paths
