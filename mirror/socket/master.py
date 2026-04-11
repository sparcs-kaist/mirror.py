"""
Master socket server and client for mirror.py

Provides MasterServer/MasterClient classes,
module-level instance management, and convenience functions.
"""

import mirror

from pathlib import Path
from typing import Optional

from .protocol import expose
from .base import BaseServer, BaseClient

MASTER_SOCKET_PATH = mirror.RUN_PATH / "master.sock"

# Module-level instance (initialized via init_instance)
_instance: Optional["MasterServer | MasterClient"] = None


# --- Classes ---


class MasterServer(BaseServer):
    """Master daemon server with built-in command handlers

    Args:
        socket_path(Path | str, optional): Socket file path
    """

    def __init__(self, socket_path: Optional[Path | str] = None):
        if socket_path is None:
            socket_path = MASTER_SOCKET_PATH
        super().__init__(socket_path, role="master")

    @expose("ping")
    def _handle_ping(self) -> dict:
        """Health check"""
        return {"message": "pong"}

    @expose("status")
    def _handle_status(self) -> dict:
        """Get master daemon status"""
        return {
            "running": self.running,
            "role": self.role,
            "version": self._version,
            "socket": str(self.socket_path),
        }

    @expose("list_packages")
    def _handle_list_packages(self) -> dict:
        """List all packages"""
        return {"packages": [pkg.to_dict() for pkg in mirror.packages.values()]}

    @expose("start_sync")
    def _handle_start_sync(self, package_id: str) -> dict:
        """Start sync for a package"""
        import mirror.sync

        package = mirror.packages.get(package_id)
        if package is None:
            raise ValueError(f"Package not found: {package_id}")
        if package.is_disabled():
            raise RuntimeError(f"Package {package_id} is disabled")
        if package.is_syncing():
            raise RuntimeError(f"Package {package_id} is already syncing")

        mirror.sync.start(package, trigger="manual")
        return {"package_id": package_id, "status": "started"}

    @expose("stop_sync")
    def _handle_stop_sync(self, package_id: str) -> dict:
        """Stop sync for a package"""
        import mirror.socket.worker

        package = mirror.packages.get(package_id)
        if package is None:
            raise ValueError(f"Package not found: {package_id}")
        if not package.is_syncing():
            raise RuntimeError(f"Package {package_id} is not syncing")

        mirror.socket.worker.stop_command(job_id=package_id)
        return {"package_id": package_id, "status": "stopped"}

    @expose("get_package")
    def _handle_get_package(self, package_id: str) -> dict:
        """Get package details"""
        package = mirror.packages.get(package_id)
        if package is None:
            raise ValueError(f"Package not found: {package_id}")
        return package.to_dict()


class MasterClient(BaseClient):
    """Client for connecting to Master daemon

    Args:
        socket_path(Path | str, optional): Socket file path
    """

    def __init__(self, socket_path: Optional[Path | str] = None):
        if socket_path is None:
            socket_path = MASTER_SOCKET_PATH
        super().__init__(socket_path, role="cli")

    def ping(self) -> dict:
        """Health check"""
        return self.send_command("ping")

    def status(self) -> dict:
        """Get master daemon status"""
        return self.send_command("status")

    def list_packages(self) -> dict:
        """List all packages"""
        return self.send_command("list_packages")

    def start_sync(self, package_id: str) -> dict:
        """Start sync for a package"""
        return self.send_command("start_sync", package_id=package_id)

    def stop_sync(self, package_id: str) -> dict:
        """Stop sync for a package"""
        return self.send_command("stop_sync", package_id=package_id)

    def get_package(self, package_id: str) -> dict:
        """Get package details"""
        return self.send_command("get_package", package_id=package_id)


# --- Instance management ---


def init_instance(role: str, **kwargs) -> MasterServer | MasterClient:
    """Initialize and store the module-level master instance

    Args:
        role(str): "server" for MasterServer, "client" for MasterClient
        **kwargs: Arguments passed to the constructor

    Return:
        instance(MasterServer | MasterClient): Initialized instance
    """
    global _instance

    if role == "server":
        _instance = MasterServer(**kwargs)
        if hasattr(mirror, "__version__"):
            _instance.set_version(mirror.__version__)
        _instance.start()
    elif role == "client":
        _instance = MasterClient(**kwargs)
        if hasattr(mirror, "__version__"):
            _instance.set_version(mirror.__version__)
        _instance.connect()
    else:
        raise ValueError(f"Invalid master role: {role}")

    return _instance


def stop_instance() -> None:
    """Stop the module-level master instance"""
    global _instance
    if _instance is None:
        return

    if isinstance(_instance, BaseServer):
        _instance.stop()
    elif isinstance(_instance, BaseClient):
        _instance.disconnect()

    _instance = None


# --- Convenience functions ---


def ping(socket_path: Optional[Path | str] = None) -> dict:
    """Health check via a temporary client"""
    with MasterClient(socket_path) as client:
        return client.ping()


def status(socket_path: Optional[Path | str] = None) -> dict:
    """Get master daemon status via a temporary client"""
    with MasterClient(socket_path) as client:
        return client.status()


def list_packages(socket_path: Optional[Path | str] = None) -> dict:
    """List all packages via a temporary client"""
    with MasterClient(socket_path) as client:
        return client.list_packages()


def start_sync(package_id: str, socket_path: Optional[Path | str] = None) -> dict:
    """Start sync for a package via a temporary client"""
    with MasterClient(socket_path) as client:
        return client.start_sync(package_id)


def stop_sync(package_id: str, socket_path: Optional[Path | str] = None) -> dict:
    """Stop sync for a package via a temporary client"""
    with MasterClient(socket_path) as client:
        return client.stop_sync(package_id)


def get_package(package_id: str, socket_path: Optional[Path | str] = None) -> dict:
    """Get package details via a temporary client"""
    with MasterClient(socket_path) as client:
        return client.get_package(package_id)


def get_master_client(socket_path: Optional[Path | str] = None) -> MasterClient:
    """Create and connect a MasterClient instance

    Args:
        socket_path(Path | str, optional): Socket file path

    Return:
        client(MasterClient): Connected client instance
    """
    client = MasterClient(socket_path)
    client.connect()
    return client


def is_master_running(socket_path: Optional[Path | str] = None) -> bool:
    """Check if master daemon is running

    Args:
        socket_path(Path | str, optional): Socket file path

    Return:
        alive(bool): True if master responds to ping
    """
    try:
        with MasterClient(socket_path) as client:
            client.ping()
            return True
    except Exception:
        return False


__all__ = [
    "MasterServer",
    "MasterClient",
    "MASTER_SOCKET_PATH",
    "init_instance",
    "stop_instance",
    "get_master_client",
    "is_master_running",
]
