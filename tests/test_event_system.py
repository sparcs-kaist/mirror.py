import time
import pytest
import mirror.event as event

def test_event_system():
    # 검증을 위한 상태 변수 (리스트를 써서 참조 전달)
    results = []
    
    # 1. 리스너 정의
    def on_sync_finished(package_id, status):
        print(f"Sync finished for {package_id} with status {status}")
        results.append((package_id, status))
        
    # 2. 리스너 등록 (데코레이터 방식 테스트)
    @event.listener("test_event")
    def on_test_event(msg):
        results.append(msg)
        
    # 3. 일반 등록 방식 테스트
    event.on("sync_finished", on_sync_finished)
    
    # 4. 이벤트 발생
    event.post_event("sync_finished", "pkg_123", status="success")
    event.post_event("test_event", "hello world")
    
    # 5. 비동기 실행 대기
    time.sleep(0.5)
    
    # 6. 검증
    assert ("pkg_123", "success") in results
    assert "hello world" in results
    
    # 7. 리스너 해제 테스트
    event.off("sync_finished", on_sync_finished)
    event.post_event("sync_finished", "pkg_456", status="fail")
    
    time.sleep(0.5)
    assert ("pkg_456", "fail") not in results

def test_post_event_passes_payload_positional_args():
    """Listeners must receive positional payload args from post_event."""
    from mirror.event import on, off, post_event

    received = []
    def listener(*args, **kwargs):
        received.append((args, kwargs))

    on("test.payload", listener)
    try:
        post_event("test.payload", "pkg", "ACTIVE", wait=True)
    finally:
        off("test.payload", listener)

    assert received == [(("pkg", "ACTIVE"), {})]


def test_pre_listener_observes_pre_mutation_status():
    """Package.set_status must fire PRE before mutating .status (wait=True)."""
    from unittest.mock import MagicMock
    from mirror.structure import Package
    from mirror.event import on, off

    pkg = Package.__new__(Package)
    pkg.pkgid = "evt_test"
    pkg.name = "EvtTest"
    pkg.status = "UNKNOWN"
    pkg.timestamp = 0.0
    pkg.statusinfo = Package.StatusInfo()
    pkg.disabled = False

    pre_observed = []
    post_observed = []

    def pre_listener(p, new_status, **kwargs):
        # PRE must run BEFORE .status was mutated to new_status
        pre_observed.append((p.status, new_status))

    def post_listener(p, new_status, **kwargs):
        post_observed.append((p.status, new_status))

    on("MASTER.PACKAGE_STATUS_UPDATE.PRE", pre_listener)
    on("MASTER.PACKAGE_STATUS_UPDATE.POST", post_listener)
    try:
        pkg.set_status("ACTIVE")
    finally:
        off("MASTER.PACKAGE_STATUS_UPDATE.PRE", pre_listener)
        off("MASTER.PACKAGE_STATUS_UPDATE.POST", post_listener)

    assert pre_observed == [("UNKNOWN", "ACTIVE")], (
        f"PRE should observe pre-mutation status; got {pre_observed}"
    )
    # POST runs async by default; give it a moment then check
    import time as _t
    for _ in range(20):
        if post_observed:
            break
        _t.sleep(0.05)
    # POST observes the new status
    assert post_observed and post_observed[0][1] == "ACTIVE"


if __name__ == "__main__":
    test_event_system()
