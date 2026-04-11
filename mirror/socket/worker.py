"""
Worker socket server and client for mirror.py

Provides WorkerServer/WorkerClient classes,
module-level instance management, and convenience functions.
"""

import os
import logging
from pathlib import Path
from typing import Optional

import mirror

from .protocol import expose
from .base import BaseServer, BaseClient

logger = logging.getLogger(__name__)

WORKER_SOCKET_PATH = mirror.RUN_PATH / "worker.sock"

# Module-level instance (initialized via init_instance)
_instance: Optional["WorkerServer | WorkerClient"] = None


# --- Classes ---


class WorkerServer(BaseServer):
    """Worker process server that manages sync job execution

    Args:
        socket_path(Path | str, optional): Socket file path
    """

    def __init__(self, socket_path: Optional[Path | str] = None):
        if socket_path is None:
            socket_path = WORKER_SOCKET_PATH
        super().__init__(socket_path, role="worker")

    def send_finished_notification(self, job_id: str, success: bool, returncode: Optional[int]) -> None:
        """Broadcast a job-finished notification to all connected clients

        Args:
            job_id(str): Job identifier
            success(bool): Whether the job succeeded
            returncode(int, optional): Process return code
        """
        if self.client_count == 0:
            raise ConnectionError("No clients connected to receive notification")

        self.broadcast({
            "type": "notification",
            "event": "job_finished",
            "job_id": job_id,
            "success": success,
            "returncode": returncode,
        })

    @expose("ping")
    def _handle_ping(self) -> dict:
        """Health check"""
        return {"message": "pong"}

    @expose("status")
    def _handle_status(self) -> dict:
        """Get worker status and list of active jobs"""
        import mirror.worker.process as process

        process.prune_finished()
        jobs = process.get_all()
        return {
            "running": self.running,
            "role": self.role,
            "version": mirror.__version__,
            "socket": str(self.socket_path),
            "active_jobs": [j.id for j in jobs if j.is_running],
        }

    @expose("execute_command")
    def _handle_execute_command(
        self,
        job_id: str,
        commandline: list[str],
        env: dict,
        sync_method: str = "execute",
        uid: Optional[int] = None,
        gid: Optional[int] = None,
        nice: int = 0,
        log_path: Optional[str] = None,
    ) -> dict:
        """Execute a shell command for a job"""
        import mirror.worker.process as process

        process.prune_finished()

        if not uid:
            uid = os.getuid()
        if not gid:
            gid = os.getgid()

        job = process.create(
            job_id=job_id,
            commandline=commandline,
            env=env,
            uid=uid,
            gid=gid,
            nice=nice,
            log_path=Path(log_path) if log_path else None,
        )

        return {
            "job_id": job_id,
            "sync_method": sync_method,
            "status": "started",
            "job_pid": job.pid,
            "has_fds": False,
        }

    @expose("stop_command")
    def _handle_stop_command(self, job_id: Optional[str] = None) -> dict:
        """Stop a specific job or all jobs"""
        import mirror.worker.process as process

        if job_id:
            job = process.get(job_id)
            if job:
                job.stop()
                return {"job_id": job_id, "status": "stopped"}
            return {"job_id": job_id, "status": "not_found"}

        jobs = process.get_all()
        stopped = []
        for job in jobs:
            if job.is_running:
                job.stop()
                stopped.append(job.id)
        return {"status": "all_stopped", "stopped_jobs": stopped}

    @expose("get_progress")
    def _handle_get_progress(self, job_id: Optional[str] = None) -> dict:
        """Get progress for a specific job or all active jobs"""
        import mirror.worker.process as process

        process.prune_finished()

        if job_id:
            job = process.get(job_id)
            if job:
                return {
                    "job_id": job_id,
                    "syncing": job.is_running,
                    "progress": 0,
                    "info": job.info(),
                }
            return {"job_id": job_id, "syncing": False, "status": "not_found"}

        jobs = process.get_all()
        return {
            "syncing": any(j.is_running for j in jobs),
            "jobs": {j.id: {"running": j.is_running, "info": j.info()} for j in jobs},
        }


class WorkerClient(BaseClient):
    """Client for connecting to Worker processes

    Args:
        socket_path(Path | str, optional): Socket file path
    """

    def __init__(self, socket_path: Optional[Path | str] = None):
        if socket_path is None:
            socket_path = WORKER_SOCKET_PATH
        super().__init__(socket_path, role="master")

    def handle_notification(self, data: dict) -> None:
        """Handle job-finished notifications from worker server"""
        if data.get("event") == "job_finished":
            import mirror.sync

            job_id = data.get("job_id")
            success = data.get("success", False)
            returncode = data.get("returncode")
            mirror.sync.on_sync_done(job_id, success, returncode)

    def ping(self) -> dict:
        """Health check"""
        return self.send_command("ping")

    def status(self) -> dict:
        """Get worker status"""
        return self.send_command("status")

    def execute_command(
        self,
        job_id: str,
        commandline: list[str],
        env: dict,
        sync_method: str = "execute",
        uid: Optional[int] = None,
        gid: Optional[int] = None,
        nice: int = 0,
        log_path: Optional[str] = None,
    ) -> dict:
        """Execute a shell command on the worker"""
        return self.send_command(
            "execute_command",
            job_id=job_id,
            commandline=commandline,
            env=env,
            sync_method=sync_method,
            uid=uid,
            gid=gid,
            nice=nice,
            log_path=log_path,
        )

    def stop_command(self, job_id: Optional[str] = None) -> dict:
        """Stop a job on the worker"""
        return self.send_command("stop_command", job_id=job_id)

    def get_progress(self, job_id: Optional[str] = None) -> dict:
        """Get current sync progress"""
        return self.send_command("get_progress", job_id=job_id)


# --- Instance management ---


def init_instance(role: str, **kwargs) -> WorkerServer | WorkerClient:
    """Initialize and store the module-level worker instance

    Args:
        role(str): "server" for WorkerServer, "client" for WorkerClient
        **kwargs: Arguments passed to the constructor

    Return:
        instance(WorkerServer | WorkerClient): Initialized instance
    """
    global _instance

    if role == "server":
        _instance = WorkerServer(**kwargs)
        if hasattr(mirror, "__version__"):
            _instance.set_version(mirror.__version__)
        _instance.start()
    elif role == "client":
        _instance = WorkerClient(**kwargs)
        if hasattr(mirror, "__version__"):
            _instance.set_version(mirror.__version__)
        _instance.connect()
    else:
        raise ValueError(f"Invalid worker role: {role}")

    return _instance


def stop_instance() -> None:
    """Stop the module-level worker instance"""
    global _instance
    if _instance is None:
        return

    if isinstance(_instance, BaseServer):
        _instance.stop()
    elif isinstance(_instance, BaseClient):
        _instance.disconnect()

    _instance = None


# --- Convenience functions ---


def send_finished_notification(job_id: str, success: bool, returncode: Optional[int]) -> None:
    """Send finished notification via the worker server instance

    Args:
        job_id(str): Job identifier
        success(bool): Whether the job succeeded
        returncode(int, optional): Process return code
    """
    if _instance is None:
        raise ConnectionError("Worker instance not initialized")
    if not isinstance(_instance, WorkerServer):
        raise TypeError("send_finished_notification requires a WorkerServer instance")
    _instance.send_finished_notification(job_id, success, returncode)


def ping(socket_path: Optional[Path | str] = None) -> dict:
    """Health check via a temporary client"""
    with WorkerClient(socket_path) as client:
        return client.ping()


def status(socket_path: Optional[Path | str] = None) -> dict:
    """Get worker status via a temporary client"""
    with WorkerClient(socket_path) as client:
        return client.status()


def stop_command(job_id: Optional[str] = None, socket_path: Optional[Path | str] = None) -> dict:
    """Stop a job via a temporary client"""
    with WorkerClient(socket_path) as client:
        return client.stop_command(job_id)


def get_progress(job_id: Optional[str] = None, socket_path: Optional[Path | str] = None) -> dict:
    """Get current sync progress via a temporary client"""
    with WorkerClient(socket_path) as client:
        return client.get_progress(job_id)


def execute_command(
    job_id: str,
    commandline: list[str],
    env: dict,
    sync_method: str = "execute",
    uid: Optional[int] = None,
    gid: Optional[int] = None,
    nice: int = 0,
    log_path: Optional[Path | str] = None,
    socket_path: Optional[Path | str] = None,
) -> dict:
    """Execute a shell command via a temporary client"""
    with WorkerClient(socket_path) as client:
        return client.execute_command(
            job_id,
            commandline,
            env,
            sync_method=sync_method,
            uid=uid,
            gid=gid,
            nice=nice,
            log_path=str(log_path) if log_path else None,
        )


def is_worker_running(job_id: Optional[str] = None) -> bool:
    """Check if the worker server is alive, optionally checking a specific job

    Args:
        job_id(str, optional): If provided, check if this job is running

    Return:
        alive(bool): True if worker responds (and job is running, if specified)
    """
    try:
        with WorkerClient(WORKER_SOCKET_PATH) as client:
            if job_id:
                progress = client.get_progress(job_id)
                return progress.get("syncing", False)
            client.ping()
            return True
    except Exception:
        return False


__all__ = [
    "WorkerServer",
    "WorkerClient",
    "WORKER_SOCKET_PATH",
    "init_instance",
    "stop_instance",
    "send_finished_notification",
    "is_worker_running",
    "stop_command",
    "get_progress",
    "execute_command",
]
