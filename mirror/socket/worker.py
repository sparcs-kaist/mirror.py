"""
Worker socket server and client for mirror.py

Provides WorkerServer/WorkerClient classes,
module-level instance management, and convenience functions.
"""

import os
import socket
import logging
import threading
import time
from pathlib import Path
from typing import Optional

import mirror

from .protocol import expose
from .base import BaseServer, BaseClient

logger = logging.getLogger(__name__)

def _default_worker_socket_path() -> Path:
    """Resolve the worker socket path, preferring config.SOCKET_PATH when set."""
    try:
        import mirror.config
        configured = getattr(mirror.config, "SOCKET_PATH", None)
    except Exception:
        configured = None
    if configured:
        p = Path(str(configured))
        if p.suffix == ".sock":
            return p
        return p / "worker.sock"
    return mirror.RUN_PATH / "worker.sock"


WORKER_SOCKET_PATH = mirror.RUN_PATH / "worker.sock"  # legacy default constant

# Module-level instance (initialized via init_instance)
_instance: Optional["WorkerServer | WorkerClient"] = None

# Module-level supervisor (set when role == "client")
_supervisor: Optional["WorkerClientSupervisor"] = None


# --- Classes ---


class WorkerServer(BaseServer):
    """Worker process server that manages sync job execution

    Args:
        socket_path(Path | str, optional): Socket file path
    """

    def __init__(self, socket_path: Optional[Path | str] = None):
        if socket_path is None:
            socket_path = _default_worker_socket_path()
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

    def stop(self) -> None:
        """Stop the server and close all existing connections."""
        with self._connections_lock:
            connections = list(self._connections)
        for conn in connections:
            try:
                conn.shutdown(socket.SHUT_RDWR)
                conn.close()
            except Exception:
                pass
        super().stop()

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

        if uid is None:
            uid = os.getuid()
        if gid is None:
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
            socket_path = _default_worker_socket_path()
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


class WorkerClientSupervisor:
    """Daemon thread that keeps a WorkerClient connected to the worker server.

    Polls `_instance.is_connected` once per second. If the client has
    disconnected (the listener loop will have flipped `_connected = False`
    on its own), a fresh `WorkerClient` is constructed and connected.
    Reconnect attempts use exponential backoff (1, 2, 4, 8, 16, 30 seconds,
    capped at 30). On a *subsequent* successful connect (i.e. not the very
    first one), the supervisor fires the `MASTER.WORKER_RECONNECTED` event.

    Args:
        socket_path(Path | str, optional): Worker socket path.
    """

    _BACKOFF = [1, 2, 4, 8, 16, 30]

    def __init__(self, socket_path: Optional[Path | str] = None):
        if socket_path is None:
            socket_path = _default_worker_socket_path()
        self._socket_path = socket_path
        self._stop_event = threading.Event()
        self._has_connected_once = False
        self._thread: Optional[threading.Thread] = None
        self._app_version = "unknown"

    def set_version(self, version: str) -> None:
        """Set the app version used for handshake on each connect attempt."""
        self._app_version = version

    def start(self) -> None:
        """Spawn the supervisor thread (daemon)."""
        self._thread = threading.Thread(
            target=self._run, name="WorkerClientSupervisor", daemon=True
        )
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        """Signal the supervisor to stop and wait for the thread to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)

    @property
    def is_connected(self) -> bool:
        """Return whether the managed instance currently reports connected."""
        global _instance
        return isinstance(_instance, WorkerClient) and _instance.is_connected

    def _try_connect(self) -> bool:
        """Try to connect a fresh client; replace `_instance` on success."""
        global _instance
        client = WorkerClient(self._socket_path)
        client.set_version(self._app_version)
        try:
            client.connect()
        except Exception as exc:
            logger.debug("WorkerClientSupervisor connect failed: %s", exc)
            return False
        _instance = client
        return True

    def _run(self) -> None:
        attempt = 0
        while not self._stop_event.is_set():
            if self.is_connected:
                attempt = 0
                if self._stop_event.wait(1.0):
                    break
                continue

            if self._try_connect():
                if self._has_connected_once:
                    try:
                        import mirror.event
                        mirror.event.post_event("MASTER.WORKER_RECONNECTED")
                    except Exception:
                        logger.debug("Failed to post MASTER.WORKER_RECONNECTED", exc_info=True)
                self._has_connected_once = True
                attempt = 0
                continue

            delay = self._BACKOFF[min(attempt, len(self._BACKOFF) - 1)]
            attempt += 1
            if self._stop_event.wait(delay):
                break


# --- Instance management ---


def init_instance(role: str, **kwargs) -> "WorkerServer | WorkerClient | WorkerClientSupervisor":
    """Initialize and store the module-level worker instance.

    Args:
        role(str): "server" for WorkerServer, "client" for a supervised WorkerClient.
        **kwargs: Arguments passed to the constructor (socket_path).

    Return:
        instance: WorkerServer for "server" role, or the WorkerClientSupervisor
            for "client" role (the supervisor manages the actual WorkerClient
            stored in module global `_instance`).
    """
    global _instance, _supervisor

    if role == "server":
        _instance = WorkerServer(**kwargs)
        if hasattr(mirror, "__version__"):
            _instance.set_version(mirror.__version__)
        _instance.start()
        return _instance

    if role == "client":
        _supervisor = WorkerClientSupervisor(socket_path=kwargs.get("socket_path"))
        if hasattr(mirror, "__version__"):
            _supervisor.set_version(mirror.__version__)
        _supervisor.start()
        return _supervisor

    raise ValueError(f"Invalid worker role: {role}")


def stop_instance() -> None:
    """Stop the supervisor and/or server, then drop module-level state."""
    global _instance, _supervisor

    if _supervisor is not None:
        _supervisor.stop()
        _supervisor = None

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
        with WorkerClient(_default_worker_socket_path()) as client:
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
    "WorkerClientSupervisor",
    "WORKER_SOCKET_PATH",
    "_default_worker_socket_path",
    "init_instance",
    "stop_instance",
    "send_finished_notification",
    "is_worker_running",
    "stop_command",
    "get_progress",
    "execute_command",
]
