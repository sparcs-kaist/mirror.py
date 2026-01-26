import pytest
from mirror.worker import process

def test_worker_id_uniqueness():
    worker_id = "unique_test_worker"
    command = ["sleep", "1"]
    
    # 첫 번째 워커 생성
    worker1 = process.create(worker_id, command, {}, None, None, 0)
    assert worker1.id == worker_id
    
    # 동일한 ID로 두 번째 워커 생성 시도 (ValueError 예상)
    with pytest.raises(ValueError) as excinfo:
        process.create(worker_id, command, {}, None, None, 0)
    
    assert f"Worker with ID '{worker_id}' already exists." in str(excinfo.value)
    
    # 워커 중지 및 정리
    worker1.stop()
    process.prune_finished()
    
    # 정리 후에는 동일한 ID로 생성 가능해야 함
    worker2 = process.create(worker_id, command, {}, None, None, 0)
    assert worker2.id == worker_id
    worker2.stop()
    process.prune_finished()
