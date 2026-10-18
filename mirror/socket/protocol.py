"""
Socket protocol layer for mirror.py

Defines protocol constants, handshake data structure,
length-prefixed message transport, and the expose decorator.
"""

import logging
import json
import math
import select
import socket
import struct
import time
from dataclasses import asdict, dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 2
APP_NAME = "mirror.py"
HANDSHAKE_TIMEOUT = 5.0
MESSAGE_TIMEOUT = 10.0
MAX_MESSAGE_SIZE = 8 * 1024 * 1024
_RECV_CHUNK_SIZE = 64 * 1024


class ProtocolError(ConnectionError):
    """Raised when a peer sends an invalid protocol frame."""


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


def _get_socket_timeout(sock: socket.socket) -> Optional[float]:
    """Return a finite configured socket timeout without changing it."""
    timeout = sock.gettimeout()
    if timeout is None:
        return None
    return max(timeout, 0.0)


def _build_deadline(sock: socket.socket, timeout: Optional[float]) -> Optional[float]:
    """Build an absolute deadline honoring the socket's configured timeout."""
    configured_timeout = _get_socket_timeout(sock)
    effective_timeout = timeout
    if configured_timeout is not None:
        effective_timeout = configured_timeout if effective_timeout is None else min(effective_timeout, configured_timeout)
    if effective_timeout is None:
        return None
    return time.monotonic() + max(effective_timeout, 0.0)


def _wait_for_socket(sock: socket.socket, readable: bool, deadline: Optional[float]) -> None:
    """Wait until a socket is ready or its absolute deadline expires."""
    desired_event = select.POLLIN if readable else select.POLLOUT
    poller = select.poll()
    try:
        poller.register(sock, desired_event | select.POLLERR | select.POLLHUP | select.POLLNVAL)
    except (OSError, ValueError) as exc:
        raise ConnectionError("Socket closed during message transfer") from exc

    while True:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise socket.timeout("timed out")
        try:
            events = poller.poll(None if remaining is None else max(1, math.ceil(remaining * 1000)))
        except InterruptedError:
            continue
        except (OSError, ValueError) as exc:
            raise ConnectionError("Socket closed during message transfer") from exc
        if not events:
            raise socket.timeout("timed out")
        event = events[0][1]
        if event & select.POLLNVAL:
            raise ConnectionError("Socket closed during message transfer")
        if event & desired_event or (readable and event & (select.POLLERR | select.POLLHUP)):
            return
        raise ConnectionError("Socket closed during message transfer")


def _recv_exact(sock: socket.socket, size: int, deadline: Optional[float], section: str) -> tuple[bytes, Optional[float]]:
    """Receive exactly one frame section in bounded chunks."""
    data = bytearray()
    while len(data) < size:
        _wait_for_socket(sock, readable=True, deadline=deadline)
        try:
            chunk = sock.recv(min(size - len(data), _RECV_CHUNK_SIZE), socket.MSG_DONTWAIT)
        except BlockingIOError:
            continue
        if not chunk:
            raise ConnectionError(f"Connection closed while receiving {section}")
        data.extend(chunk)
        if deadline is None:
            deadline = time.monotonic() + MESSAGE_TIMEOUT
    return bytes(data), deadline


def send_message(sock: socket.socket, data: dict, timeout: float = MESSAGE_TIMEOUT) -> None:
    """Send a length-prefixed JSON message over a socket

    Args:
        sock(socket.socket): Connected socket
        data(dict): Payload to send as JSON
        timeout(float): Total send deadline in seconds
    """
    deadline = _build_deadline(sock, timeout)
    body = json.dumps(data).encode("utf-8")
    if not body or len(body) > MAX_MESSAGE_SIZE:
        raise ProtocolError(f"Message size must be between 1 and {MAX_MESSAGE_SIZE} bytes")

    frame = memoryview(struct.pack(">I", len(body)) + body)
    sent = 0
    while sent < len(frame):
        _wait_for_socket(sock, readable=False, deadline=deadline)
        try:
            written = sock.send(frame[sent:], socket.MSG_DONTWAIT)
        except BlockingIOError:
            continue
        if written == 0:
            raise ConnectionError("Connection closed while sending message")
        sent += written


def recv_message(sock: socket.socket, timeout: Optional[float] = None) -> dict:
    """Receive a length-prefixed JSON message from a socket

    Args:
        sock(socket.socket): Connected socket
        timeout(float, optional): Total read deadline. When omitted, waiting for
            the first byte is unlimited and the frame must then finish promptly.

    Return:
        data(dict): Deserialized JSON payload
    """
    deadline = _build_deadline(sock, timeout)
    header, deadline = _recv_exact(sock, 4, deadline, "message header")
    length = struct.unpack(">I", header)[0]
    if length == 0 or length > MAX_MESSAGE_SIZE:
        raise ProtocolError(f"Message size must be between 1 and {MAX_MESSAGE_SIZE} bytes")

    body, _ = _recv_exact(sock, length, deadline, "message")
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Invalid JSON message") from exc
    if not isinstance(decoded, dict):
        raise ProtocolError("Message payload must be a JSON object")
    return decoded


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
    "MESSAGE_TIMEOUT",
    "MAX_MESSAGE_SIZE",
    "ProtocolError",
    "HandshakeInfo",
    "send_message",
    "recv_message",
    "expose",
]
