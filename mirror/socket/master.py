"""
Master socket module for mirror.py

Provides instance management and convenience functions.
Classes are defined in master_base.
"""

import mirror

from pathlib import Path
from typing import Optional

from .worker_base import BaseServer, BaseClient
from .master_base import (
    MasterServer,
    MasterClient,
    MASTER_SOCKET_PATH,
)

# Module-level instance (initialized via init_instance)
_instance: Optional[MasterServer | MasterClient] = None


# --- Instance management ---

def init_instance(role: str, **kwargs) -> MasterServer | MasterClient:
    """
    Initialize and store the module-level master instance.

    Args:
        role: "server" for MasterServer, "client" for MasterClient.
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
    """Stop the module-level master instance."""
    global _instance
    if _instance is None:
        return

    if isinstance(_instance, BaseServer):
        _instance.stop()
    elif isinstance(_instance, BaseClient):
        _instance.disconnect()

    _instance = None


# --- Module-level convenience functions ---

def ping(socket_path: Optional[Path | str] = None) -> dict:
    """Health check"""
    with MasterClient(socket_path) as client:
        return client.ping()

def status(socket_path: Optional[Path | str] = None) -> dict:
    """Get master daemon status"""
    with MasterClient(socket_path) as client:
        return client.status()

def list_packages(socket_path: Optional[Path | str] = None) -> dict:
    """List all packages"""
    with MasterClient(socket_path) as client:
        return client.list_packages()

def start_sync(package_id: str, socket_path: Optional[Path | str] = None) -> dict:
    """Start sync for a package"""
    with MasterClient(socket_path) as client:
        return client.start_sync(package_id)

def stop_sync(package_id: str, socket_path: Optional[Path | str] = None) -> dict:
    """Stop sync for a package"""
    with MasterClient(socket_path) as client:
        return client.stop_sync(package_id)

def get_package(package_id: str, socket_path: Optional[Path | str] = None) -> dict:
    """Get package details"""
    with MasterClient(socket_path) as client:
        return client.get_package(package_id)

def get_master_client(socket_path: Optional[Path | str] = None) -> MasterClient:
    """Get a connected MasterClient instance."""
    client = MasterClient(socket_path)
    client.connect()
    return client

def is_master_running(socket_path: Optional[Path | str] = None) -> bool:
    """Check if master daemon is running"""
    try:
        with MasterClient(socket_path) as client:
            client.ping()
            return True
    except (ConnectionError, Exception):
        return False


__all__ = [
    "MasterServer",
    "MasterClient",
    "init_instance",
    "stop_instance",
    "get_master_client",
    "is_master_running",
    "MASTER_SOCKET_PATH",
]
