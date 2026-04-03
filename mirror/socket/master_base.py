"""
Mirror.py Master Socket Base Module

Provides Master server/client implementations.
"""

from pathlib import Path
from typing import Optional

import mirror

from .worker_base import BaseServer, BaseClient, expose

MASTER_SOCKET_PATH = mirror.RUN_PATH / "master.sock"


class MasterServer(BaseServer):
    """
    Master daemon server.
    Automatically registers default handlers and manages mirror operations.
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
        # TODO: Implement actual package listing
        return {"packages": []}

    @expose("start_sync")
    def _handle_start_sync(self, package_id: str) -> dict:
        """Start sync for a package"""
        # TODO: Implement actual sync start
        return {"package_id": package_id, "status": "started"}

    @expose("stop_sync")
    def _handle_stop_sync(self, package_id: str) -> dict:
        """Stop sync for a package"""
        # TODO: Implement actual sync stop
        return {"package_id": package_id, "status": "stopped"}

    @expose("get_package")
    def _handle_get_package(self, package_id: str) -> dict:
        """Get package details"""
        # TODO: Implement actual package retrieval
        return {"package_id": package_id}


class MasterClient(BaseClient):
    """
    Client for connecting to Master daemon.
    Used by CLI tools and other processes.
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


__all__ = [
    "MasterServer",
    "MasterClient",
    "MASTER_SOCKET_PATH",
]
