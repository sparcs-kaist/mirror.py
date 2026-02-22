import pytest
import multiprocessing
import time
import os
import signal
import sys
from pathlib import Path
from mirror.command.worker import worker
from mirror.socket.worker import WorkerClient

def run_worker_process(config_path, socket_path):
    # Wrapper to run worker in a separate process
    try:
        worker(config_path, socket_path=socket_path)
    except SystemExit:
        pass

@pytest.fixture
def worker_process(tmp_path):
    # Setup
    socket_path = tmp_path / "worker.sock"
    config_path = tmp_path / "config.json"
    config_path.touch() # Create dummy config file

    p = multiprocessing.Process(target=run_worker_process, args=(str(config_path), str(socket_path)))
    p.start()
    
    # Wait for socket to appear
    timeout = 5
    start_time = time.time()
    while not socket_path.exists():
        if time.time() - start_time > timeout:
            p.terminate()
            pytest.fail("Worker socket did not appear in time")
        time.sleep(0.1)

    yield p, socket_path

    # Teardown
    if p.is_alive():
        p.terminate()
        p.join()

def test_worker_execution_and_tasks(worker_process):
    process, socket_path = worker_process
    
    # Connect client
    client = WorkerClient(socket_path)
    client.connect()
    
    # Verify status
    status = client.status()
    assert status["running"] is True
    assert status["role"] == "worker"

    # Task 1
    job_id_1 = "job_1"
    cmd_1 = ["echo", "task1"]
    
    # Use current user/group to avoid permission errors
    uid = os.getuid()
    gid = os.getgid()
    
    response = client.start_sync(
        job_id=job_id_1,
        sync_method="rsync",
        commandline=cmd_1,
        env={},
        uid=uid,
        gid=gid
    )
    
    assert response["job_id"] == job_id_1
    assert response["status"] == "started"
    
    # Stop sync 1
    response = client.stop_sync()
    assert response["job_id"] == job_id_1
    assert response["status"] == "stopped"

    # Task 2
    job_id_2 = "job_2"
    cmd_2 = ["echo", "task2"]
    
    response = client.start_sync(
        job_id=job_id_2,
        sync_method="rsync",
        commandline=cmd_2,
        env={},
        uid=uid,
        gid=gid
    )
    
    assert response["job_id"] == job_id_2
    assert response["status"] == "started"
    
    # Stop sync 2
    response = client.stop_sync()
    assert response["job_id"] == job_id_2
    assert response["status"] == "stopped"

    # Verify SIGINT handling
    os.kill(process.pid, signal.SIGINT)
    
    # Wait for process to exit gracefully
    process.join(timeout=5)
    
    assert not process.is_alive(), "Worker process should have exited"
    assert process.exitcode == 0, "Worker process should exit with 0"