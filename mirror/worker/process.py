import os
import subprocess
import time
import logging
from typing import Optional, IO
from pathlib import Path

logger = logging.getLogger(__name__)

# Global registry of jobs
_jobs: dict[str, 'Job'] = {}

class Job:
    """
    Represents a worker process.
    """
    def __init__(self, job_id: str, commandline: list[str], env: dict[str, str], uid: int, gid: int, nice: int, log_path: Optional[Path] = None):
        self.id = job_id
        self.commandline = commandline
        self.env = env
        self.uid = uid
        self.gid = gid
        self.nice = nice
        self.log_path = log_path
        self.process: Optional[subprocess.Popen] = None
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None

    def start(self):
        """
        Starts the worker process.
        """
        def preexec():
            # Set group ID first
            if self.gid is not None:
                try:
                    os.setgid(self.gid)
                except OSError as e:
                    logger.error(f"Failed to set GID to {self.gid}: {e}")
                    raise e

            # Set user ID
            if self.uid is not None:
                try:
                    os.setuid(self.uid)
                except OSError as e:
                    logger.error(f"Failed to set UID to {self.uid}: {e}")
                    raise e

            # Set niceness
            if self.nice is not None:
                try:
                    os.nice(self.nice)
                except OSError as e:
                    logger.error(f"Failed to set niceness to {self.nice}: {e}")
                    raise e

        run_env = os.environ.copy()
        if self.env:
            run_env.update(self.env)
        
        self.start_time = time.time()
        
        stdout_dest = subprocess.PIPE
        stderr_dest = subprocess.PIPE
        log_file_handle = None

        if self.log_path:
            # Ensure the directory exists
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Open log file in append mode. Using binary mode 'ab' can be more 
            # efficient and avoids encoding issues for subprocess output.
            log_file_handle = open(self.log_path, "ab")
            stdout_dest = log_file_handle
            stderr_dest = subprocess.STDOUT # Merge stderr into stdout
        
        try:
            self.process = subprocess.Popen(
                self.commandline,
                env=run_env,
                preexec_fn=preexec,
                stdin=subprocess.PIPE,
                stdout=stdout_dest,
                stderr=stderr_dest,
                bufsize=0, # Unbuffered for real-time logging
            )
            # self.process.stdin
            # self.process.stdout
            # self.process.stderr

            # Close our handle to the log file now that subprocess has it
            if log_file_handle:
                log_file_handle.close()
            
            logger.info(f"Started worker {self.id} (PID {self.process.pid})")
            _jobs[self.id] = self
        except Exception as e:
            if log_file_handle:
                log_file_handle.close()
            logger.error(f"Failed to start worker: {e}")
            self.end_time = time.time()
            raise e

    def get_pipe(self, stream: str) -> Optional[int]:
        """
        Returns the file descriptor of the specified stream.
        Useful for passing FDs to other processes via sockets (SCM_RIGHTS).
        
        Args:
            stream: One of 'stdin', 'stdout', 'stderr'
        """
        if self.process is None:
            return None
            
        if stream == 'stdin' and self.process and self.process.stdin:
            return self.process.stdin.fileno()
        elif stream == 'stdout' and self.process and self.process.stdout:
            return self.process.stdout.fileno()
        elif stream == 'stderr' and self.process and self.process.stderr:
            return self.process.stderr.fileno()
        return None

    @property
    def pid(self) -> Optional[int]:
        return self.process.pid if self.process else None

    @property
    def is_running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    @property
    def returncode(self) -> Optional[int]:
        if self.process:
            return self.process.returncode
        return None

    def stop(self, timeout=5):
        """
        Stops the worker process.
        """
        if self.process and self.is_running:
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
        
        if self.process:
             self.end_time = time.time()

    def info(self) -> dict:
        return {
            "id": self.id,
            "pid": self.pid,
            "commandline": self.commandline,
            "uid": self.uid,
            "gid": self.gid,
            "nice": self.nice,
            "running": self.is_running,
            "start_time": self.start_time,
            "uptime": (time.time() - self.start_time) if self.is_running and self.start_time else 0
        }

def create(job_id: str, commandline: list[str], env: dict[str, str], uid: int, gid: int, nice: int, log_path: Optional[Path] = None) -> Job:
    """
    Creates and starts a new worker.
    Raises ValueError if job_id already exists.
    """
    if job_id in _jobs:
        raise ValueError(f"Worker with ID '{job_id}' already exists.")
    
    job = Job(job_id, commandline, env, uid, gid, nice, log_path)
    job.start()
    return job

def get(job_id: str) -> Optional[Job]:
    """
    Retrieves a worker by ID.
    """
    return _jobs.get(job_id)

def get_all() -> list[Job]:
    """
    Returns a list of all registered jobs.
    """
    return list(_jobs.values())

def prune_finished():
    """
    Removes finished jobs from the registry.
    Wait for log threads to finish if the process has ended.
    Sends notification to worker clients via mirror.socket.worker.
    Jobs are only removed if the notification is successfully sent 
    (which implies at least one client is connected).
    """
    import mirror.socket
    
    to_remove = []
    for wid, w in _jobs.items():
        if not w.is_running:
            to_remove.append(wid)

    for wid in to_remove:
        w = _jobs.get(wid)
        if not w:
            continue
            
        try:
            # Get exit status
            returncode = w.returncode
            success = (returncode == 0)
            
            mirror.socket.worker.send_finished_notification(wid, success, returncode)
            del _jobs[wid]
        except Exception:
            # mirror.socket.worker might be missing or no clients connected.
            # Keep the job in registry for next attempt.
            pass
