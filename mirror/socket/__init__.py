"""
Mirror.py Socket Communication Module

Provides base server and client classes for IPC communication
with automatic handshake protocol for version and role exchange.
"""

import os
import socket
import threading
import json
import struct
import traceback
import array
from pathlib import Path
from typing import Optional, Callable, Any
from dataclasses import dataclass, asdict

# Protocol constants
PROTOCOL_VERSION = 1
APP_NAME = "mirror.py"
HANDSHAKE_TIMEOUT = 5.0


@dataclass
class HandshakeInfo:
    """Information exchanged during connection handshake"""
    app_name: str
    app_version: str
    protocol_version: int
    is_server: bool
    role: str  # "master", "worker", "cli", etc.

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "HandshakeInfo":
        return HandshakeInfo(**data)


def _send_message(sock: socket.socket, data: dict) -> None:
    """Send a length-prefixed JSON message"""
    body = json.dumps(data).encode('utf-8')
    header = struct.pack('>I', len(body))
    sock.sendall(header + body)


def _recv_message(sock: socket.socket, timeout: Optional[float] = None) -> dict:
    """Receive a length-prefixed JSON message"""
    if timeout:
        sock.settimeout(timeout)

    header = sock.recv(4)
    if not header or len(header) < 4:
        raise ConnectionError("Failed to receive message header")

    length = struct.unpack('>I', header)[0]
    data = b''
    while len(data) < length:
        packet = sock.recv(length - len(data))
        if not packet:
            raise ConnectionError("Connection closed while receiving message")
        data += packet

    if timeout:
        sock.settimeout(None)

    return json.loads(data.decode('utf-8'))



def expose(cmd_name: Optional[str] = None):
    """
    Decorator to mark a method as an exposed socket command handler.
    If cmd_name is not provided, the method name is used.
    """
    def decorator(func):
        func._is_rpc_handler = True
        func._rpc_command = cmd_name
        return func
    return decorator


class BaseServer:
    """
    Base server class for Unix socket IPC.
    Handles connection acceptance and handshake protocol.
    """

    def __init__(self, socket_path: Path | str, role: str):
        self.socket_path = Path(socket_path)
        self.role = role
        self.running = False
        self.server: Optional[socket.socket] = None
        self._handlers: dict[str, Callable] = {}
        self._version = "unknown"
        self._auto_register_handlers()

    def _auto_register_handlers(self):
        """Automatically register methods decorated with @expose"""
        # Inspect all members of the instance
        for attr_name in dir(self):
            try:
                attr = getattr(self, attr_name)
                # Check if it's a method and has the marker
                if hasattr(attr, "_is_rpc_handler") and attr._is_rpc_handler:
                    # Use the provided command name or the method name
                    cmd_name = attr._rpc_command or attr_name
                    self.register_handler(cmd_name, attr)
            except Exception:
                pass

    def set_version(self, version: str) -> None:
        """Set application version for handshake"""
        self._version = version

    def register_handler(self, command: str, handler: Callable) -> None:
        """Register a command handler"""
        self._handlers[command] = handler

    def _get_handshake_info(self) -> HandshakeInfo:
        """Get handshake info for this server"""
        return HandshakeInfo(
            app_name=APP_NAME,
            app_version=self._version,
            protocol_version=PROTOCOL_VERSION,
            is_server=True,
            role=self.role
        )

    def _perform_handshake(self, conn: socket.socket) -> Optional[HandshakeInfo]:
        """Perform handshake with client, returns client info or None on failure"""
        try:
            # Send server info first
            _send_message(conn, {
                "status": 200,
                "message": "Handshake",
                "data": {"info": self._get_handshake_info().to_dict()}
            })

            # Receive client info
            response = _recv_message(conn, timeout=HANDSHAKE_TIMEOUT)
            # Support both old and new format during transition if needed
            info_dict = response.get("data", {}).get("info") or response.get("info")
            if not info_dict:
                raise ValueError("Expected handshake message")

            client_info = HandshakeInfo.from_dict(info_dict)

            # Validate handshake
            if client_info.app_name != APP_NAME:
                _send_message(conn, {
                    "status": 403,
                    "message": "Invalid application",
                    "data": None
                })
                return None

            if client_info.protocol_version != PROTOCOL_VERSION:
                _send_message(conn, {
                    "status": 400,
                    "message": "Protocol version mismatch",
                    "data": None
                })
                return None

            # Send handshake success
            _send_message(conn, {
                "status": 200,
                "message": "OK",
                "data": None
            })
            return client_info

        except Exception as e:
            print(f"Handshake failed: {e}")
            return None

    def _handle_connection(self, conn: socket.socket, client_info: HandshakeInfo) -> None:
        """Handle client connection after successful handshake"""
        try:
            while self.running:
                try:
                    request = _recv_message(conn)
                except (ConnectionError, json.JSONDecodeError):
                    break

                command = request.get("command")
                kwargs = request.get("kwargs", {})

                if command in self._handlers:
                    try:
                        result = self._handlers[command](**kwargs) if kwargs else self._handlers[command]()
                        response = {
                            "status": 200,
                            "message": "OK",
                            "data": result
                        }
                    except Exception as e:
                        # Log error internally if needed
                        traceback.print_exc()
                        response = {
                            "status": 500,
                            "message": str(e),
                            "data": {"traceback": traceback.format_exc()}
                        }
                else:
                    response = {
                        "status": 404,
                        "message": f"Command '{command}' not found",
                        "data": None
                    }

                _send_message(conn, response)
        finally:
            conn.close()

    def start(self) -> None:
        """Start the server"""
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
        """Accept incoming connections"""
        while self.running:
            if self.server is None:
                break
            try:
                conn, _ = self.server.accept()

                # Perform handshake
                client_info = self._perform_handshake(conn)
                if client_info is None:
                    conn.close()
                    continue

                # Handle connection in separate thread
                handler_thread = threading.Thread(
                    target=self._handle_connection,
                    args=(conn, client_info),
                    daemon=True
                )
                handler_thread.start()

            except OSError:
                if self.running:
                    print("Socket accept error")
                break

    def stop(self) -> None:
        """Stop the server"""
        self.running = False
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except Exception:
                pass


class BaseClient:
    """
    Base client class for Unix socket IPC.
    Handles connection and handshake protocol.
    """

    def __init__(self, socket_path: Path | str, role: str):
        self.socket_path = Path(socket_path)
        self.role = role
        self._version = "unknown"
        self._sock: Optional[socket.socket] = None
        self._server_info: Optional[HandshakeInfo] = None
        self._connected = False

    def set_version(self, version: str) -> None:
        """Set application version for handshake"""
        self._version = version

    def _get_handshake_info(self) -> HandshakeInfo:
        """Get handshake info for this client"""
        return HandshakeInfo(
            app_name=APP_NAME,
            app_version=self._version,
            protocol_version=PROTOCOL_VERSION,
            is_server=False,
            role=self.role
        )

    def connect(self) -> HandshakeInfo:
        """
        Connect to server and perform handshake.
        Returns server's handshake info.
        """
        if not self.socket_path.exists():
            raise ConnectionError(f"Socket file not found at {self.socket_path}")

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._sock.connect(str(self.socket_path))

            # Receive server handshake
            response = _recv_message(self._sock, timeout=HANDSHAKE_TIMEOUT)
            if response.get("status") != 200:
                raise ConnectionError(f"Handshake failed: {response.get('message')}")

            info_dict = response.get("data", {}).get("info")
            if not info_dict:
                raise ConnectionError("Invalid handshake data from server")

            self._server_info = HandshakeInfo.from_dict(info_dict)

            # Validate server
            if self._server_info.app_name != APP_NAME:
                raise ConnectionError("Invalid application")

            if self._server_info.protocol_version != PROTOCOL_VERSION:
                raise ConnectionError(f"Protocol version mismatch: server={self._server_info.protocol_version}, client={PROTOCOL_VERSION}")

            # Send client handshake
            _send_message(self._sock, {
                "status": 200,
                "message": "Handshake",
                "data": {"info": self._get_handshake_info().to_dict()}
            })

            # Wait for confirmation
            confirm = _recv_message(self._sock, timeout=HANDSHAKE_TIMEOUT)
            if confirm.get("status") != 200:
                raise ConnectionError(f"Handshake rejected: {confirm.get('message')}")

            self._connected = True
            return self._server_info

        except Exception:
            if self._sock:
                self._sock.close()
                self._sock = None
            raise

    def disconnect(self) -> None:
        """Disconnect from server"""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._connected = False
        self._server_info = None

    def send_command(self, command: str, **kwargs) -> Any:
        """Send a command to server and return result"""
        if not self._connected or not self._sock:
            raise ConnectionError("Not connected to server")

        _send_message(self._sock, {"command": command, "kwargs": kwargs if kwargs else None})
        
        response = _recv_message(self._sock)

        if response.get("status") == 200:
            return response.get("data")
        else:
            error_msg = response.get("message", "Unknown error")
            data = response.get("data")
            if data and isinstance(data, dict) and "traceback" in data:
                 # Optionally include traceback in the exception message for debugging
                 pass
            raise Exception(f"RPC Error ({response.get('status')}): {error_msg}")

    def __getattr__(self, name: str) -> Callable:
        """Allow calling commands as methods"""
        if name.startswith('_'):
            raise AttributeError(name)
        def wrapper(**kwargs):
            return self.send_command(name, **kwargs)
        return wrapper

    @property
    def server_info(self) -> Optional[HandshakeInfo]:
        """Get server's handshake info after connection"""
        return self._server_info

    @property
    def is_connected(self) -> bool:
        """Check if connected to server"""
        return self._connected

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()


# For backwards compatibility and convenience
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

def stop() -> None:
    """
    Stops the running master server and disconnects any clients.
    This function is intended to be called for a clean shutdown.
    """
    import sys
    this_module = sys.modules[__name__]

    # Stop the master server if it's running
    if hasattr(this_module, "master"):
        server = getattr(this_module, "master")
        if server and hasattr(server, "stop"):
            server.stop()
        delattr(this_module, "master")

    # Disconnect the worker client if it's connected
    if hasattr(this_module, "worker"):
        client = getattr(this_module, "worker")
        if client and hasattr(client, "disconnect"):
            client.disconnect()
        delattr(this_module, "worker")


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
    import sys
    import mirror
    from .master import MASTER_SOCKET_PATH
    from .worker import WORKER_SOCKET_PATH
    
    # Get the current module to set attributes
    this_module = sys.modules[__name__]
    
    if role == "master":
        from .master import MasterServer
        server = MasterServer(**kwargs)
        if hasattr(mirror, "__version__"):
            server.set_version(mirror.__version__)
        server.start()
        
        # Register master server as mirror.socket.master
        setattr(this_module, "master", server)
        
        # Check and register worker if alive
        try:
            # Try to connect to default worker socket
            # We use a default client to check existence and liveness
            from .worker import WorkerClient
            worker_client = WorkerClient()
            if worker_client.socket_path.exists():
                worker_client.connect()
                if worker_client.is_connected:
                    setattr(this_module, "worker", worker_client)
        except Exception:
            # Worker not running or unreachable, ignore
            pass

        return server

    elif role == "worker":
        from .worker import WorkerServer
        server = WorkerServer(**kwargs)
        if hasattr(mirror, "__version__"):
            server.set_version(mirror.__version__)
        server.start()
        return server

    elif role in ("client", "master_client"):
        from .master import MasterClient
        client = MasterClient(**kwargs)
        if hasattr(mirror, "__version__"):
            client.set_version(mirror.__version__)
        client.connect()
        return client

    elif role == "worker_client":
        client = WorkerClient(**kwargs)
        if hasattr(mirror, "__version__"):
            client.set_version(mirror.__version__)
        client.connect()
        return client

    else:
        raise ValueError(f"Invalid role: {role}")
