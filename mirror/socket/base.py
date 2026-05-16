"""
Base server and client classes for Unix socket IPC.

Provides the connection lifecycle, handshake protocol,
command dispatch, and message listener infrastructure.
"""

import json
import queue
import socket
import threading
import traceback
import logging
import uuid
from pathlib import Path
from typing import Optional, Callable, Any

from .protocol import (
    PROTOCOL_VERSION,
    APP_NAME,
    HANDSHAKE_TIMEOUT,
    HandshakeInfo,
    send_message,
    recv_message,
)

logger = logging.getLogger(__name__)

_CONNECTION_ERROR_SENTINEL = object()


class BaseServer:
    """Base server for Unix socket IPC with handshake and command dispatch

    Args:
        socket_path(Path | str): Path to the Unix domain socket file
        role(str): Server role identifier for handshake
    """

    def __init__(self, socket_path: Path | str, role: str):
        self.socket_path = Path(socket_path)
        self.role = role
        self.running = False
        self.server: Optional[socket.socket] = None
        self._handlers: dict[str, Callable] = {}
        self._version = "unknown"
        self._connections: list[socket.socket] = []
        self._connections_lock = threading.Lock()
        self._register_exposed_handlers()

    def _register_exposed_handlers(self) -> None:
        """Scan for methods decorated with @expose and register them"""
        for attr_name in dir(self):
            try:
                attr = getattr(self, attr_name)
                if getattr(attr, "_is_rpc_handler", False):
                    cmd_name = attr._rpc_command or attr_name
                    self._handlers[cmd_name] = attr
            except Exception:
                pass

    def set_version(self, version: str) -> None:
        """Set application version for handshake

        Args:
            version(str): Version string
        """
        self._version = version

    def register_handler(self, command: str, handler: Callable) -> None:
        """Register a command handler

        Args:
            command(str): Command name
            handler(Callable): Handler function
        """
        self._handlers[command] = handler

    def _build_handshake_info(self) -> HandshakeInfo:
        """Build handshake info for this server"""
        return HandshakeInfo(
            app_name=APP_NAME,
            app_version=self._version,
            protocol_version=PROTOCOL_VERSION,
            is_server=True,
            role=self.role,
        )

    def _perform_handshake(self, conn: socket.socket) -> Optional[HandshakeInfo]:
        """Execute handshake with a connecting client"""
        try:
            send_message(conn, {
                "status": 200,
                "message": "Handshake",
                "data": {"info": self._build_handshake_info().to_dict()},
            })

            response = recv_message(conn, timeout=HANDSHAKE_TIMEOUT)
            info_dict = response.get("data", {}).get("info") or response.get("info")
            if not info_dict:
                raise ValueError("Expected handshake message")

            client_info = HandshakeInfo.from_dict(info_dict)

            if client_info.app_name != APP_NAME:
                send_message(conn, {
                    "status": 403,
                    "message": "Invalid application",
                    "data": None,
                })
                return None

            if client_info.protocol_version != PROTOCOL_VERSION:
                send_message(conn, {
                    "status": 400,
                    "message": "Protocol version mismatch",
                    "data": None,
                })
                return None

            send_message(conn, {"status": 200, "message": "OK", "data": None})
            return client_info

        except Exception as exc:
            logger.warning("Handshake failed: %s", exc)
            return None

    def _handle_connection(self, conn: socket.socket, client_info: HandshakeInfo) -> None:
        """Read commands from a connected client until disconnect"""
        with self._connections_lock:
            self._connections.append(conn)
        try:
            while self.running:
                try:
                    request = recv_message(conn)
                except (ConnectionError, json.JSONDecodeError):
                    break

                command = request.get("command")
                kwargs = request.get("kwargs", {})
                request_id = request.get("request_id")

                response = self._dispatch_command(command, kwargs)
                if isinstance(request_id, str):
                    response["request_id"] = request_id
                send_message(conn, response)
        finally:
            with self._connections_lock:
                if conn in self._connections:
                    self._connections.remove(conn)
            try:
                conn.close()
            except OSError as exc:
                logger.debug("Failed to close connection: %s", exc)

    def _dispatch_command(self, command: str, kwargs: Optional[dict]) -> dict:
        """Route a command to its handler and build the response"""
        handler = self._handlers.get(command)
        if handler is None:
            return {
                "status": 404,
                "message": f"Command '{command}' not found",
                "data": None,
            }

        try:
            result = handler(**kwargs) if kwargs else handler()
            return {"status": 200, "message": "OK", "data": result}
        except Exception as exc:
            logger.exception("Command '%s' failed", command)
            return {
                "status": 500,
                "message": str(exc),
                "data": {"traceback": traceback.format_exc()},
            }

    def broadcast(self, data: dict) -> None:
        """Send a message to all connected clients

        Args:
            data(dict): Payload to broadcast
        """
        with self._connections_lock:
            connections = list(self._connections)

        for conn in connections:
            try:
                send_message(conn, data)
            except OSError as exc:
                logger.debug("broadcast send failed: %s", exc)

    @property
    def client_count(self) -> int:
        """Number of currently connected clients"""
        with self._connections_lock:
            return len(self._connections)

    def start(self) -> None:
        """Bind the socket and begin accepting connections"""
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass

        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.socket_path))
        self.server.listen()
        self.running = True
        self.socket_path.chmod(0o600)

        thread = threading.Thread(target=self._accept_loop, daemon=True)
        thread.start()

    def _accept_loop(self) -> None:
        """Accept incoming connections and spawn handler threads"""
        while self.running:
            if self.server is None:
                break
            try:
                conn, _ = self.server.accept()

                client_info = self._perform_handshake(conn)
                if client_info is None:
                    conn.close()
                    continue

                handler_thread = threading.Thread(
                    target=self._handle_connection,
                    args=(conn, client_info),
                    daemon=True,
                )
                handler_thread.start()

            except OSError:
                if self.running:
                    logger.warning("Socket accept error")
                break

    def stop(self) -> None:
        """Close the server socket and remove the socket file"""
        self.running = False
        if self.server:
            try:
                self.server.close()
            except OSError as exc:
                logger.debug("Failed to close server socket: %s", exc)
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError as exc:
                logger.debug("Failed to unlink socket path %s: %s", self.socket_path, exc)


class BaseClient:
    """Base client for Unix socket IPC with handshake and async listener

    Args:
        socket_path(Path | str): Path to the server's Unix domain socket
        role(str): Client role identifier for handshake
    """

    def __init__(self, socket_path: Path | str, role: str):
        self.socket_path = Path(socket_path)
        self.role = role
        self._version = "unknown"
        self._sock: Optional[socket.socket] = None
        self._server_info: Optional[HandshakeInfo] = None
        self._connected = False
        self._pending_responses: dict[str, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._listener_thread: Optional[threading.Thread] = None
        self._send_lock = threading.Lock()

    def set_version(self, version: str) -> None:
        """Set application version for handshake

        Args:
            version(str): Version string
        """
        self._version = version

    def _build_handshake_info(self) -> HandshakeInfo:
        """Build handshake info for this client"""
        return HandshakeInfo(
            app_name=APP_NAME,
            app_version=self._version,
            protocol_version=PROTOCOL_VERSION,
            is_server=False,
            role=self.role,
        )

    def _listen_loop(self) -> None:
        """Background loop that receives server messages"""
        while self._connected and self._sock:
            try:
                message = recv_message(self._sock)
                self._handle_incoming_message(message)
            except (ConnectionError, json.JSONDecodeError, OSError):
                break
            except Exception:
                logger.exception("Listener error")
        self._connected = False
        self._wake_pending_connection_error()

    def _handle_incoming_message(self, message: Any) -> None:
        """Dispatch an incoming socket message to notification or RPC routing."""
        if not isinstance(message, dict):
            logger.warning("Dropping invalid socket message: %s", message)
            return
        if message.get("type") == "notification":
            self.handle_notification(message)
            return
        self._route_response(message)

    def _route_response(self, message: dict) -> bool:
        """Route a correlated response to its pending waiter."""
        request_id = message.get("request_id")
        if not isinstance(request_id, str):
            logger.debug("Dropping uncorrelated RPC response: %s", message)
            return False
        with self._pending_lock:
            response_queue = self._pending_responses.get(request_id)
        if response_queue is None:
            logger.debug("Dropping stale RPC response for request_id=%s", request_id)
            return False
        try:
            response_queue.put_nowait(message)
        except queue.Full:
            logger.debug("Dropping duplicate RPC response for request_id=%s", request_id)
            return False
        return True

    def _wake_pending_connection_error(self) -> None:
        """Wake all pending RPC calls after the listener detects disconnect."""
        with self._pending_lock:
            pending = list(self._pending_responses.values())
        for response_queue in pending:
            try:
                response_queue.put_nowait(_CONNECTION_ERROR_SENTINEL)
            except queue.Full:
                pass

    def handle_notification(self, data: dict) -> None:
        """Handle server notification. Override in subclasses.

        Args:
            data(dict): Notification payload
        """

    def connect(self) -> HandshakeInfo:
        """Connect to server and perform handshake

        Return:
            server_info(HandshakeInfo): Server's handshake information
        """
        if not self.socket_path.exists():
            raise ConnectionError(f"Socket file not found at {self.socket_path}")

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._sock.connect(str(self.socket_path))

            response = recv_message(self._sock, timeout=HANDSHAKE_TIMEOUT)
            if response.get("status") != 200:
                raise ConnectionError(f"Handshake failed: {response.get('message')}")

            info_dict = response.get("data", {}).get("info")
            if not info_dict:
                raise ConnectionError("Invalid handshake data from server")

            self._server_info = HandshakeInfo.from_dict(info_dict)

            if self._server_info.app_name != APP_NAME:
                raise ConnectionError("Invalid application")

            if self._server_info.protocol_version != PROTOCOL_VERSION:
                raise ConnectionError(
                    f"Protocol version mismatch: "
                    f"server={self._server_info.protocol_version}, "
                    f"client={PROTOCOL_VERSION}"
                )

            send_message(self._sock, {
                "status": 200,
                "message": "Handshake",
                "data": {"info": self._build_handshake_info().to_dict()},
            })

            confirm = recv_message(self._sock, timeout=HANDSHAKE_TIMEOUT)
            if confirm.get("status") != 200:
                raise ConnectionError(f"Handshake rejected: {confirm.get('message')}")

            self._connected = True

            self._listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
            self._listener_thread.start()

            return self._server_info

        except Exception:
            if self._sock:
                self._sock.close()
                self._sock = None
            raise

    def disconnect(self) -> None:
        """Disconnect from server"""
        self._connected = False
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
                self._sock.close()
            except OSError as exc:
                logger.debug("Failed to shut down client socket: %s", exc)
            self._sock = None
        self._server_info = None
        self._wake_pending_connection_error()

    def send_command(self, command: str, recv_timeout: float | None = None, **kwargs: Any) -> Any:
        """Send a command to server and wait for the response.

        Args:
            command(str): Command name.
            recv_timeout(float, optional): How many seconds to wait for the
                server's response before raising TimeoutError. Defaults to 30s.
                NOTE: this parameter is consumed client-side and is NOT forwarded
                to the server. Any server-side kwarg named ``recv_timeout`` would
                be silently shadowed — callers must rename such kwargs before
                calling this method.
            **kwargs: Command arguments forwarded to the server.

        Return:
            data(Any): Response payload from the server.
        """
        if not self._connected or not self._sock:
            raise ConnectionError("Not connected to server")

        request_id = uuid.uuid4().hex
        response_queue: queue.Queue = queue.Queue(maxsize=1)

        with self._pending_lock:
            if not self._connected or not self._sock:
                raise ConnectionError("Not connected to server")
            sock = self._sock
            self._pending_responses[request_id] = response_queue

        try:
            with self._send_lock:
                try:
                    send_message(sock, {
                        "command": command,
                        "kwargs": kwargs if kwargs else None,
                        "request_id": request_id,
                    })
                except OSError as exc:
                    raise ConnectionError(f"Failed to send command '{command}': {exc}") from exc

            try:
                response = response_queue.get(timeout=recv_timeout if recv_timeout is not None else 30)
            except queue.Empty:
                raise TimeoutError(f"Command '{command}' timed out")
        finally:
            with self._pending_lock:
                self._pending_responses.pop(request_id, None)

        if response is _CONNECTION_ERROR_SENTINEL:
            raise ConnectionError("Connection closed")

        if response.get("status") == 200:
            return response.get("data")

        error_msg = response.get("message", "Unknown error")
        raise Exception(f"RPC Error ({response.get('status')}): {error_msg}")

    def __getattr__(self, name: str) -> Callable:
        """Forward unknown method calls as RPC commands"""
        if name.startswith("_"):
            raise AttributeError(name)

        def wrapper(**kwargs: Any) -> Any:
            return self.send_command(name, **kwargs)
        return wrapper

    @property
    def server_info(self) -> Optional[HandshakeInfo]:
        """Server's handshake info (available after connect)"""
        return self._server_info

    @property
    def is_connected(self) -> bool:
        """Whether the client is currently connected"""
        return self._connected

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()


__all__ = [
    "BaseServer",
    "BaseClient",
]
