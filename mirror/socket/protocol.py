"""
Socket protocol layer for mirror.py

Defines protocol constants, handshake data structure,
length-prefixed message transport, and the expose decorator.
"""

import json
import socket
import struct
import logging
from typing import Optional, Callable
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
APP_NAME = "mirror.py"
HANDSHAKE_TIMEOUT = 5.0


@dataclass
class HandshakeInfo:
    """Information exchanged during connection handshake

    Args:
        app_name(str): Application identifier
        app_version(str): Application version string
        protocol_version(int): Wire protocol version
        is_server(bool): Whether sender is a server
        role(str): Role identifier (master, worker, cli, etc.)
    """
    app_name: str
    app_version: str
    protocol_version: int
    is_server: bool
    role: str

    def to_dict(self) -> dict:
        """Serialize to dictionary

        Return:
            data(dict): Dataclass fields as dictionary
        """
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "HandshakeInfo":
        """Deserialize from dictionary

        Args:
            data(dict): Dictionary with HandshakeInfo fields

        Return:
            info(HandshakeInfo): Deserialized instance
        """
        return HandshakeInfo(**data)


def send_message(sock: socket.socket, data: dict) -> None:
    """Send a length-prefixed JSON message over a socket

    Args:
        sock(socket.socket): Connected socket
        data(dict): Payload to send as JSON
    """
    body = json.dumps(data).encode("utf-8")
    header = struct.pack(">I", len(body))
    sock.sendall(header + body)


def recv_message(sock: socket.socket, timeout: Optional[float] = None) -> dict:
    """Receive a length-prefixed JSON message from a socket

    Args:
        sock(socket.socket): Connected socket
        timeout(float, optional): Read timeout in seconds

    Return:
        data(dict): Deserialized JSON payload
    """
    if timeout:
        sock.settimeout(timeout)

    header = sock.recv(4)
    if not header or len(header) < 4:
        raise ConnectionError("Failed to receive message header")

    length = struct.unpack(">I", header)[0]
    data = b""
    while len(data) < length:
        packet = sock.recv(length - len(data))
        if not packet:
            raise ConnectionError("Connection closed while receiving message")
        data += packet

    if timeout:
        sock.settimeout(None)

    return json.loads(data.decode("utf-8"))


def expose(cmd_name: Optional[str] = None) -> Callable:
    """Mark a method as an exposed socket command handler

    Args:
        cmd_name(str, optional): Command name. Defaults to method name.

    Return:
        decorator(Callable): Decorator that tags the method
    """
    def decorator(func: Callable) -> Callable:
        func._is_rpc_handler = True
        func._rpc_command = cmd_name
        return func
    return decorator


__all__ = [
    "PROTOCOL_VERSION",
    "APP_NAME",
    "HANDSHAKE_TIMEOUT",
    "HandshakeInfo",
    "send_message",
    "recv_message",
    "expose",
]
