import os
import subprocess
import threading
import time
import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Global registry of jobs and its lock
_jobs: dict[str, 'Job'] = {}
_jobs_lock = threading.Lock()

# Maximum notification attempts before force-pruning a finished job.
# The manage() loop retries once per second, so this is roughly a 40s window
# for the master to re-establish its notification connection and accept the
# completion. Beyond that the job is dropped; the master-side reconciliation
# then marks the package ERROR and it re-syncs on the next cycle.
NOTIFY_ATTEMPT_BUDGET = 40
HELPER_DRAIN_TIMEOUT = 5.0


class Job:
    """Represents a worker process."""

    def __init__(
        self,
        job_id: str,
        commandline: list[str],
        env: dict[str, str],
        uid: Optional[int],
        gid: Optional[int],
        nice: int,
        log_path: Optional[Path] = None,
        log_helper_command: Optional[list[str]] = None,
    ):
        self.id = job_id
        self.commandline = commandline
        self.env = env
        self.uid = uid
        self.gid = gid
        self.nice = nice
        self.log_path = log_path
        self.log_helper_command = log_helper_command
        self.process: Optional[subprocess.Popen] = None
        self.log_helper_process: Optional[subprocess.Popen] = None
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self._notify_attempts: int = 0
        self._main_finished_at: Optional[float] = None
        self._helper_stop_requested_at: Optional[float] = None
        self._lock = threading.Lock()

        if nice < 0 and os.geteuid() != 0:
            raise PermissionError(
                f"Job {job_id}: negative nice ({nice}) requires root EUID"
            )

    def start(self) -> None:
        """Spawn the subprocess with the configured command, uid, gid, and niceness."""
        if self.uid is None or self.gid is None:
            raise ValueError(f"Job {self.id}: explicit uid and gid are required")

        run_env = os.environ.copy()
        if self.env:
            run_env.update(self.env)

        def preexec():
            # Apply niceness before changing identity so the setuid
            # call cannot lose the privilege needed to renice.
            if self.nice is not None:
                try:
                    os.nice(self.nice)
                except OSError as e:
                    logger.error(f"Failed to set niceness to {self.nice}: {e}")
                    raise e

            if self.gid is not None:
                try:
                    os.setgid(self.gid)
                except OSError as e:
                    logger.error(f"Failed to set GID to {self.gid}: {e}")
                    raise e

            if self.uid is not None:
                try:
                    os.setuid(self.uid)
                except OSError as e:
                    logger.error(f"Failed to set UID to {self.uid}: {e}")
                    raise e

        self.start_time = time.time()

        stdout_dest = subprocess.DEVNULL
        stderr_dest = subprocess.DEVNULL
        log_file_handle = None

        if self.log_path:
            missing_parents = self._missing_directories(self.log_path.parent)
            file_existed = self.log_path.exists()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            for directory in missing_parents:
                self._apply_job_owner(directory)
            log_file_handle = open(self.log_path, "ab")
            if not file_existed:
                self._apply_job_owner(self.log_path)
            stdout_dest = log_file_handle
            stderr_dest = subprocess.STDOUT

        try:
            with self._lock:
                if self.log_helper_command:
                    self.log_helper_process = subprocess.Popen(
                        self.log_helper_command,
                        **self._popen_kwargs(
                            os.environ.copy(),
                            subprocess.DEVNULL,
                            subprocess.DEVNULL,
                            preexec,
                        ),
                    )

                popen_kwargs = self._popen_kwargs(run_env, stdout_dest, stderr_dest, preexec)
                self.process = subprocess.Popen(self.commandline, **popen_kwargs)

            # Close our handle to the log file now that subprocess has it
            if log_file_handle:
                log_file_handle.close()

            logger.info(f"Started worker {self.id} (PID {self.process.pid})")
        except Exception as e:
            if log_file_handle:
                log_file_handle.close()
            self._stop_log_helper(timeout=1)
            logger.error(f"Failed to start worker: {e}")
            self.end_time = time.time()
            raise e

    def _popen_kwargs(self, env, stdout_dest, stderr_dest, preexec) -> dict:
        popen_kwargs = {
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": stdout_dest,
            "stderr": stderr_dest,
            "bufsize": 0,
        }
        # Popen has user/group support but no nice kwarg; only use
        # preexec_fn when a niceness adjustment is actually needed.
        if self.nice == 0:
            popen_kwargs["group"] = self.gid
            popen_kwargs["user"] = self.uid
        else:
            popen_kwargs["preexec_fn"] = preexec
        return popen_kwargs

    def _missing_directories(self, directory: Path) -> list[Path]:
        missing = []
        current = directory
        while not current.exists():
            missing.append(current)
            parent = current.parent
            if parent == current:
                break
            current = parent
        return list(reversed(missing))

    def _apply_job_owner(self, path: Path) -> None:
        if os.geteuid() != 0 or self.uid is None or self.gid is None:
            return
        try:
            os.chown(path, self.uid, self.gid, follow_symlinks=False)
        except OSError as e:
            logger.warning(f"Failed to chown {path}: {e}")

    def get_pipe(self, stream: str) -> Optional[int]:
        """Return the file descriptor for the specified stream.

        Args:
            stream(str): One of 'stdin', 'stdout', 'stderr'

        Return:
            fd(int | None): File descriptor, or None if unavailable
        """
        if self.process is None:
            return None

        if stream == "stdin" and self.process.stdin:
            return self.process.stdin.fileno()
        elif stream == "stdout" and self.process.stdout:
            return self.process.stdout.fileno()
        elif stream == "stderr" and self.process.stderr:
            return self.process.stderr.fileno()
        return None

    @property
    def pid(self) -> Optional[int]:
        return self.process.pid if self.process else None

    @property
    def is_running(self) -> bool:
        with self._lock:
            main_running = self.process is not None and self.process.poll() is None
            helper_running = (
                self.log_helper_process is not None
                and self.log_helper_process.poll() is None
            )
            return main_running or helper_running

    @property
    def returncode(self) -> Optional[int]:
        if self.process:
            return self.process.returncode
        return None

    def stop(self, timeout: int = 5) -> None:
        """Terminate the worker process, killing it if it does not stop in time.

        Args:
            timeout(int, optional): Seconds to wait before sending SIGKILL. Defaults to 5.
        """
        with self._lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()

            self._stop_log_helper_locked(timeout=timeout)

            if self.process:
                self.end_time = time.time()

    def reap(self) -> None:
        """Advance helper lifecycle after the main process exits."""
        with self._lock:
            main_done = self.process is not None and self.process.poll() is not None
            main_failed_to_start = self.process is None and self.end_time is not None
            main_done = main_done or main_failed_to_start
            if main_done and self._main_finished_at is None:
                self._main_finished_at = time.time()

            helper = self.log_helper_process
            if helper is None:
                if main_done and self.end_time is None:
                    self.end_time = time.time()
                return

            if not main_done:
                return

            if helper.poll() is None and self._helper_stop_requested_at is None:
                helper.terminate()
                self._helper_stop_requested_at = time.time()

            if helper.poll() is None and self._helper_stop_requested_at is not None:
                if time.time() - self._helper_stop_requested_at >= HELPER_DRAIN_TIMEOUT:
                    helper.kill()
                    helper.wait()

            if helper.poll() is not None and self.end_time is None:
                self.end_time = time.time()

    def _stop_log_helper(self, timeout: int = 5) -> None:
        with self._lock:
            self._stop_log_helper_locked(timeout)

    def _stop_log_helper_locked(self, timeout: int = 5) -> None:
        helper = self.log_helper_process
        if helper is None or helper.poll() is not None:
            return
        helper.terminate()
        try:
            helper.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            helper.kill()
            helper.wait()

    def info(self) -> dict:
        """Return a snapshot dict of the job's current state.

        Return:
            data(dict): Job metadata including id, pid, running status, and uptime.
        """
        with self._lock:
            main_running = self.process is not None and self.process.poll() is None
            helper = self.log_helper_process
            helper_pid = helper.pid if helper else None
            helper_running = helper is not None and helper.poll() is None
            helper_returncode = helper.returncode if helper else None

        return {
            "id": self.id,
            "pid": self.pid,
            "commandline": self.commandline,
            "uid": self.uid,
            "gid": self.gid,
            "nice": self.nice,
            "running": main_running or helper_running,
            "main_running": main_running,
            "helper_pid": helper_pid,
            "helper_running": helper_running,
            "helper_returncode": helper_returncode,
            "start_time": self.start_time,
            "uptime": (
                (time.time() - self.start_time)
                if self.is_running and self.start_time
                else 0
            ),
        }


def create(
    job_id: str,
    commandline: list[str],
    env: dict[str, str],
    uid: Optional[int],
    gid: Optional[int],
    nice: int,
    log_path: Optional[Path] = None,
    log_helper_command: Optional[list[str]] = None,
) -> Job:
    """Create and start a new worker.

    Args:
        job_id(str): Unique identifier for the job
        commandline(list[str]): Command to execute
        env(dict[str, str]): Extra environment variables
        uid(int | None): User ID for the subprocess
        gid(int | None): Group ID for the subprocess
        nice(int): Niceness value
        log_path(Path, optional): File to redirect stdout/stderr into

    Return:
        job(Job): The started Job instance

    Raises:
        ValueError: If a job with the given ID already exists
    """
    with _jobs_lock:
        if job_id in _jobs:
            raise ValueError(f"Worker with ID '{job_id}' already exists.")
        job = Job(job_id, commandline, env, uid, gid, nice, log_path, log_helper_command)
        _jobs[job_id] = job

    try:
        job.start()
    except Exception:
        with _jobs_lock:
            _jobs.pop(job_id, None)
        raise

    return job


def get(job_id: str) -> Optional[Job]:
    """Retrieve a worker by ID.

    Args:
        job_id(str): Job identifier

    Return:
        job(Job | None): The Job, or None if not found
    """
    with _jobs_lock:
        return _jobs.get(job_id)


def get_all() -> list[Job]:
    """Return a snapshot list of all registered jobs.

    Return:
        jobs(list[Job]): All current jobs
    """
    with _jobs_lock:
        return list(_jobs.values())


def prune_finished():
    """Remove finished jobs from the registry after notifying clients.

    Notification is attempted via mirror.socket.worker.send_finished_notification.
    If notification fails, the attempt counter is incremented. After
    NOTIFY_ATTEMPT_BUDGET consecutive failures the job is force-pruned.
    """
    import mirror.socket.worker

    with _jobs_lock:
        jobs = list(_jobs.items())
    for _, job in jobs:
        job.reap()

    with _jobs_lock:
        claimed: list[tuple[str, "Job"]] = []
        for wid in list(_jobs.keys()):
            job = _jobs[wid]
            if not job.is_running:
                _jobs.pop(wid)
                claimed.append((wid, job))

    for wid, job in claimed:
        returncode = job.returncode
        success = returncode == 0
        try:
            mirror.socket.worker.send_finished_notification(wid, success, returncode)
        except Exception as exc:
            job._notify_attempts += 1
            if job._notify_attempts >= NOTIFY_ATTEMPT_BUDGET:
                logger.warning(
                    f"Force-pruning {wid} after {NOTIFY_ATTEMPT_BUDGET} "
                    f"failed notifications: {exc}"
                )
            else:
                with _jobs_lock:
                    if wid in _jobs:
                        logger.warning(
                            f"Cannot re-queue {wid} for retry: a new job "
                            f"has taken this id; dropping stale finished job ({exc})"
                        )
                    else:
                        _jobs[wid] = job
