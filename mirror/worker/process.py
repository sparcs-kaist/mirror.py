import os
import subprocess
import time
import logging
import threading
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
        self.stdin: Optional[IO] = None
        self.stdout: Optional[IO] = None
        self.stderr: Optional[IO] = None
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self._log_thread: Optional[threading.Thread] = None

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
            try:
                # Ensure the directory exists
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Open log file in append mode. Using binary mode 'ab' can be more 
                # efficient and avoids encoding issues for subprocess output.
                log_file_handle = open(self.log_path, "ab")
                stdout_dest = log_file_handle
                stderr_dest = subprocess.STDOUT # Merge stderr into stdout
            except Exception as e:
                logger.error(f"Failed to open log file {self.log_path}: {e}")
                # Fallback to PIPE if file fails
        
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
            self.stdin = self.process.stdin
            
            # If we used pipes, these will be set. If we used file, they'll be None (for stdout/stderr)
            self.stdout = self.process.stdout
            self.stderr = self.process.stderr
            
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

    def set_log_path(self, log_path: Path):
        """
        Redirects stdout to the specified log file.
        Starts a background thread to append output line by line.
        """
        if not self.stdout:
            logger.warning(f"Worker {self.id} has no stdout pipe.")
            return

        # Capture stdout locally to avoid type checker issues inside the closure
        stdout_pipe = self.stdout

        def _log_writer():
            try:
                # Open in append binary mode to avoid encoding issues and for thread safety
                with open(log_path, 'ab') as f:
                    for line in stdout_pipe:
                        f.write(line)
                        f.flush()
            except Exception as e:
                logger.error(f"Error writing log for worker {self.id}: {e}")

        self._log_thread = threading.Thread(target=_log_writer, daemon=True)
        self._log_thread.start()

    def get_pipe(self, stream: str) -> Optional[int]:
        """
        Returns the file descriptor of the specified stream.
        Useful for passing FDs to other processes via sockets (SCM_RIGHTS).
        
        Args:
            stream: One of 'stdin', 'stdout', 'stderr'
        """
        if self.process is None:
            return None
            
        if stream == 'stdin' and self.stdin:
            return self.stdin.fileno()
        elif stream == 'stdout' and self.stdout:
            return self.stdout.fileno()
        elif stream == 'stderr' and self.stderr:
            return self.stderr.fileno()
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
    """
    to_remove = []
    for wid, w in _jobs.items():
        if not w.is_running:
            # If the process is dead, the log thread will soon finish 
            # as it reaches EOF on stdout.
            if w._log_thread and w._log_thread.is_alive():
                # Join with a short timeout to not block the manager too long
                w._log_thread.join(timeout=0.1)
                if not w._log_thread.is_alive():
                    to_remove.append(wid)
            else:
                to_remove.append(wid)
                
    for wid in to_remove:
        del _jobs[wid]

def set_log_path(job_id: str, log_path: Path):
    """
    Sets the log path for a specific worker.
    """
    worker = get(job_id)
    if worker:
        worker.set_log_path(log_path)
