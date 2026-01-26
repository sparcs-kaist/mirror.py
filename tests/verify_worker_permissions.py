import sys
import os
import argparse
import time
import shutil
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mirror.worker import process

def verify_permissions(target_uid, target_gid):
    print(f"Current PID: {os.getpid()}, UID: {os.getuid()}, GID: {os.getgid()}")
    print(f"Target UID: {target_uid}, Target GID: {target_gid}")

    test_file = Path(f"/tmp/test_perm_{int(time.time())}.tmp").absolute()
    if test_file.exists():
        test_file.unlink()

    print(f"Creating worker to touch file: {test_file}")
    
    # Create the worker
    # worker_id, commandline, env, uid, gid, nice
    try:
        worker = process.create(
            worker_id="test_worker_1",
            commandline=["touch", str(test_file)],
            env={},
            uid=target_uid,
            gid=target_gid,
            nice=0
        )
    except Exception as e:
        print(f"Failed to create/start worker: {e}")
        return False

    print(f"Worker started with PID: {worker.pid}")
    
    # Wait for worker to finish
    max_retries = 10
    while worker.is_running and max_retries > 0:
        time.sleep(0.1)
        max_retries -= 1
    
    worker.stop()
    
    if worker.returncode != 0 and worker.returncode is not None:
        print(f"Worker failed with return code: {worker.returncode}")
        # If the worker failed, it might be because it couldn't setuid/setgid inside the child process.
        # However, subprocess preexec_fn exceptions are often printed to stderr but might not propagate cleanly as a return code 
        # unless caught and re-raised or causing an exit.
        # In the current implementation of process.py:
        # exceptions in preexec_fn usually cause the child to crash before exec.
        return False

    if not test_file.exists():
        print("Error: Test file was not created.")
        return False

    # Check file stats
    stat = os.stat(test_file)
    print(f"File created. Owner UID: {stat.st_uid}, Owner GID: {stat.st_gid}")

    success = True
    if stat.st_uid != target_uid:
        print(f"FAIL: File UID ({stat.st_uid}) does not match target ({target_uid})")
        success = False
    
    if stat.st_gid != target_gid:
        print(f"FAIL: File GID ({stat.st_gid}) does not match target ({target_gid})")
        success = False
        
    # Clean up
    if test_file.exists():
        try:
            test_file.unlink()
        except PermissionError:
            print(f"Warning: Could not delete {test_file} (likely due to permission differences). Please delete manually.")

    return success

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify worker process permission switching")
    parser.add_argument("--uid", type=int, required=True, help="Target UID")
    parser.add_argument("--gid", type=int, required=True, help="Target GID")
    
    args = parser.parse_args()
    
    if verify_permissions(args.uid, args.gid):
        print("SUCCESS: Permissions verified.")
        sys.exit(0)
    else:
        print("FAILURE: Verification failed.")
        sys.exit(1)
