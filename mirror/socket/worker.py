"""
Worker Server and Client for mirror.py

Worker processes handle actual sync operations.
Master communicates with Workers via WorkerClient.
"""

import mirror

from pathlib import Path
from typing import Optional

from . import BaseServer, BaseClient, HandshakeInfo, expose

# Default socket path pattern for workers
DEFAULT_WORKER_SOCKET_DIR = Path("/run/mirror/workers")


def get_worker_socket_path(job_id: str) -> Path:
    """Get socket path for a specific job (package)"""
    return DEFAULT_WORKER_SOCKET_DIR / f"{job_id}.sock"


class WorkerServer(BaseServer):
    """
    Worker process server.
    Handles sync operations for assigned packages (jobs).
    """

    def __init__(self, socket_path: Optional[Path | str] = None):
        if socket_path is None:
            socket_path = Path("/run/mirror/worker.sock")
        super().__init__(socket_path, role="worker")
        self._current_sync: Optional[str] = None

    @expose("ping")
    def _handle_ping(self) -> dict:
        """Health check"""
        return {"message": "pong"}

    @expose("status")
    def _handle_status(self) -> dict:
        """Get job status"""
        return {
            "running": self.running,
            "role": self.role,
            "version": mirror.__version__,
            "socket": str(self.socket_path),
        }

    @expose("start_sync")
    def _handle_start_sync(self, job_id: str, sync_method: str, commandline: list[str], env: dict, uid: int, gid: int, nice: int = 0) -> tuple[dict, list[int]]:
        """Start sync for a package (job)"""
        if self._current_sync is not None:
            raise RuntimeError(f"Worker busy with {self._current_sync}")

        import mirror.worker.process as process
        
        job = process.create(
            job_id=job_id, 
            commandline=commandline,
            env=env,
            uid=uid,
            gid=gid,
            nice=nice
        )
        
        self._current_sync = job_id
        
        # Collect FDs to send (stdout, stderr)
        fds = []
        stdout_fd = job.get_pipe('stdout')
        stderr_fd = job.get_pipe('stderr')
        
        if stdout_fd is not None:
            fds.append(stdout_fd)
        if stderr_fd is not None:
            fds.append(stderr_fd)

        return {
            "job_id": job_id,
            "sync_method": sync_method,
            "status": "started",
            "job_pid": job.pid,
            "has_fds": len(fds) > 0
        }, fds

    @expose("stop_sync")
    def _handle_stop_sync(self) -> dict:
        """Stop current sync"""
        if self._current_sync is None:
            raise RuntimeError("No sync in progress")

        job_id = self._current_sync
        self._current_sync = None
        # TODO: Implement actual sync stop
        return {
            "job_id": job_id,
            "status": "stopped",
        }

    @expose("get_progress")
    def _handle_get_progress(self) -> dict:
        """Get current sync progress"""
        if self._current_sync is None:
            return {"syncing": False}

        # TODO: Implement actual progress tracking
        return {
            "syncing": True,
            "job_id": self._current_sync,
            "progress": 0,
            "speed": "0 B/s",
        }
    


class WorkerClient(BaseClient):
    """
    Client for connecting to Worker processes.
    Used by Master to manage workers.
    """

    def __init__(self, socket_path: Optional[Path | str] = None):
        if socket_path is None:
            socket_path = Path("/run/mirror/worker.sock")
        super().__init__(socket_path, role="master")

    def ping(self) -> dict:
        """Health check"""
        return self.send_command("ping")

    def status(self) -> dict:
        """Get worker status"""
        return self.send_command("status")

    def start_sync(self, job_id: str, sync_method: str, commandline: list[str], env: dict, uid: int, gid: int, nice: int = 0) -> tuple[dict, list[int]]:
        """Start sync for a package"""
        return self.send_command("start_sync", expect_fds=True, job_id=job_id, sync_method=sync_method, commandline=commandline, env=env, uid=uid, gid=gid, nice=nice)

    def stop_sync(self) -> dict:
        """Stop current sync"""
        return self.send_command("stop_sync")

    def get_progress(self) -> dict:
        """Get current sync progress"""
        return self.send_command("get_progress")



def get_worker_client(job_id: str) -> WorkerClient:
    """
    Get a connected WorkerClient instance.
    Convenience function for Master usage.
    """
    client = WorkerClient(job_id)
    client.connect()
    return client


def is_worker_running(job_id: str) -> bool:
    """Check if a worker is running"""
    try:
        with WorkerClient(job_id) as client:
            client.ping()
            return True
    except (ConnectionError, Exception):
        return False


__all__ = [
    "WorkerServer",
    "WorkerClient",
    "get_worker_client",
    "is_worker_running",
    "get_worker_socket_path",
    "DEFAULT_WORKER_SOCKET_DIR",
]