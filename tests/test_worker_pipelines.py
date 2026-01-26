import time
from mirror.worker import process

def test_worker_pipelines():
    worker_id = "pipeline_test"
    # 입력을 그대로 출력하는 'cat' 명령어 사용
    command = ["cat"]
    
    worker = process.create(worker_id, command, {}, None, None, 0)
    
    try:
        test_message = b"hello mirror pipeline\n"
        
        # stdin에 데이터 쓰기
        worker.stdin.write(test_message)
        worker.stdin.flush()
        
        # stdout에서 데이터 읽기
        # cat은 입력받은 데이터를 즉시 출력함
        output = worker.stdout.readline()
        
        assert output == test_message
        print(f"Pipeline test success: received '{output.decode().strip()}'")
        
    finally:
        worker.stop()
        process.prune_finished()

if __name__ == "__main__":
    test_worker_pipelines()
