"""
Mirror.py Socket Communication Module

Provides Unix socket IPC between master daemon and worker processes
with automatic handshake protocol for version and role exchange.
"""

from typing import Any

from .protocol import HandshakeInfo, PROTOCOL_VERSION, APP_NAME, HANDSHAKE_TIMEOUT, expose
from .base import BaseServer, BaseClient


def init(role: str, **kwargs: Any) -> Any:
    """Initialize a socket server or client by role

    Args:
        role(str): "master" or "worker" for servers,
                   "client"/"master_client" for MasterClient,
                   "worker_client" for WorkerClient
        **kwargs: Arguments passed to the constructor

    Return:
        instance(Any): Initialized server or connected client
    """
    from . import master, worker

    if role == "master":
        server = master.init_instance("server", **kwargs)

        try:
            worker.init_instance("client")
        except Exception:
            pass

        return server

    elif role == "worker":
        return worker.init_instance("server", **kwargs)

    elif role in ("client", "master_client"):
        return master.init_instance("client", **kwargs)

    elif role == "worker_client":
        return worker.init_instance("client", **kwargs)

    else:
        raise ValueError(f"Invalid role: {role}")


def stop() -> None:
    """Stop all running servers and disconnect clients"""
    from . import master, worker

    master.stop_instance()
    worker.stop_instance()


__all__ = [
    "BaseServer",
    "BaseClient",
    "HandshakeInfo",
    "PROTOCOL_VERSION",
    "APP_NAME",
    "HANDSHAKE_TIMEOUT",
    "expose",
    "init",
    "stop",
]
