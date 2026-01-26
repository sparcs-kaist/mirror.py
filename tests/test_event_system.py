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

if __name__ == "__main__":
    test_event_system()
