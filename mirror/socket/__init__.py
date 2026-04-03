"""
Mirror.py Socket Communication Module

Provides base server and client classes for IPC communication
with automatic handshake protocol for version and role exchange.
"""

from typing import Any

# Re-export from worker_base for backwards compatibility
from .worker_base import (
    BaseServer,
    BaseClient,
    HandshakeInfo,
    PROTOCOL_VERSION,
    APP_NAME,
    HANDSHAKE_TIMEOUT,
    expose,
    _send_message,
    _recv_message,
)


def stop() -> None:
    """
    Stops all running servers and disconnects any clients.
    This function is intended to be called for a clean shutdown.
    """
    from . import master, worker
    master.stop_instance()
    worker.stop_instance()


def init(role: str, **kwargs) -> Any:
    """
    Initialize and start a socket server or connect a client.

    Args:
        role: "master", "worker" for servers.
              "client", "master_client" for MasterClient.
              "worker_client" for WorkerClient.
        **kwargs: Additional arguments passed to the constructor.

    Returns:
        The initialized server or connected client instance.
    """
    from . import master, worker

    if role == "master":
        server = master.init_instance("server", **kwargs)

        # Try to connect to worker if alive
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


__all__ = [
    "BaseServer",
    "BaseClient",
    "HandshakeInfo",
    "PROTOCOL_VERSION",
    "APP_NAME",
    "expose",
    "init",
    "stop",
]
