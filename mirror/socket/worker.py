"""
Worker socket module for mirror.py

Provides instance management and convenience functions.
Classes are defined in worker_base.
"""

import mirror

from pathlib import Path
from typing import Optional

from .worker_base import (
    BaseServer,
    BaseClient,
    WorkerServer,
    WorkerClient,
    WORKER_SOCKET_PATH,
)

# Module-level instance (initialized via init_instance)
_instance: Optional[WorkerServer | WorkerClient] = None


# --- Instance management ---

def init_instance(role: str, **kwargs) -> WorkerServer | WorkerClient:
    """
    Initialize and store the module-level worker instance.

    Args:
        role: "server" for WorkerServer, "client" for WorkerClient.
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
    """Stop the module-level worker instance."""
    global _instance
    if _instance is None:
        return

    if isinstance(_instance, BaseServer):
        _instance.stop()
    elif isinstance(_instance, BaseClient):
        _instance.disconnect()

    _instance = None


# --- Module-level convenience functions ---

def send_finished_notification(job_id: str, success: bool, returncode: Optional[int]) -> None:
    """Send finished notification via the worker server instance."""
    if _instance is None:
        raise ConnectionError("Worker instance not initialized")
    if not isinstance(_instance, WorkerServer):
        raise TypeError("send_finished_notification requires a WorkerServer instance")
    _instance.send_finished_notification(job_id, success, returncode)


def ping(socket_path: Optional[Path | str] = None) -> dict:
    """Health check"""
    with WorkerClient(socket_path) as client:
        return client.ping()

def status(socket_path: Optional[Path | str] = None) -> dict:
    """Get worker status"""
    with WorkerClient(socket_path) as client:
        return client.status()

def stop_command(job_id: Optional[str] = None, socket_path: Optional[Path | str] = None) -> dict:
    """Stop current command"""
    with WorkerClient(socket_path) as client:
        return client.stop_command(job_id)

def get_progress(job_id: Optional[str] = None, socket_path: Optional[Path | str] = None) -> dict:
    """Get current sync progress"""
    with WorkerClient(socket_path) as client:
        return client.get_progress(job_id)

def execute_command(job_id: str, commandline: list[str], env: dict, sync_method: str = "execute", uid: Optional[int] = None, gid: Optional[int] = None, nice: int = 0, log_path: Optional[Path | str] = None, socket_path: Optional[Path | str] = None) -> dict:
    """Execute a shell command"""
    with WorkerClient(socket_path) as client:
        return client.execute_command(job_id, commandline, env, sync_method=sync_method, uid=uid, gid=gid, nice=nice, log_path=str(log_path) if log_path else None)

def is_worker_running(job_id: Optional[str] = None) -> bool:
    """
    Check if the local worker server is alive.
    If job_id is provided, check if that specific job is currently running.
    """
    try:
        with WorkerClient(WORKER_SOCKET_PATH) as client:
            if job_id:
                progress = client.get_progress(job_id)
                return progress.get("syncing", False)
            else:
                client.ping()
                return True
    except (ConnectionError, Exception):
        return False


__all__ = [
    "WorkerServer",
    "WorkerClient",
    "init_instance",
    "stop_instance",
    "send_finished_notification",
    "is_worker_running",
    "stop_command",
    "get_progress",
    "execute_command",
    "WORKER_SOCKET_PATH",
]
