"""
Mirror.py Worker Socket Base Module

Provides base server/client classes and Worker server/client implementations.
"""

import os
import socket
import threading
import json
import struct
import traceback
import queue
from pathlib import Path
from typing import Optional, Callable, Any
from dataclasses import dataclass, asdict

import mirror

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
        self._connections: list[socket.socket] = []
        self._connections_lock = threading.Lock()
        self._auto_register_handlers()

    def _auto_register_handlers(self):
        """Automatically register methods decorated with @expose"""
        for attr_name in dir(self):
            try:
                attr = getattr(self, attr_name)
                if hasattr(attr, "_is_rpc_handler") and attr._is_rpc_handler:
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
            _send_message(conn, {
                "status": 200,
                "message": "Handshake",
                "data": {"info": self._get_handshake_info().to_dict()}
            })

            response = _recv_message(conn, timeout=HANDSHAKE_TIMEOUT)
            info_dict = response.get("data", {}).get("info") or response.get("info")
            if not info_dict:
                raise ValueError("Expected handshake message")

            client_info = HandshakeInfo.from_dict(info_dict)

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
        with self._connections_lock:
            self._connections.append(conn)
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
            with self._connections_lock:
                if conn in self._connections:
                    self._connections.remove(conn)
            conn.close()

    def broadcast(self, data: dict) -> None:
        """Send a message to all connected clients (non-RPC notification)"""
        with self._connections_lock:
            connections = list(self._connections)

        for conn in connections:
            try:
                _send_message(conn, data)
            except Exception:
                pass

    @property
    def client_count(self) -> int:
        """Number of connected clients"""
        with self._connections_lock:
            return len(self._connections)

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

                client_info = self._perform_handshake(conn)
                if client_info is None:
                    conn.close()
                    continue

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
        self._response_queue = queue.Queue()
        self._listener_thread: Optional[threading.Thread] = None

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

    def _listen_loop(self) -> None:
        """Background thread to listen for server messages"""
        while self._connected and self._sock:
            try:
                message = _recv_message(self._sock)
                if message.get("type") == "notification":
                    self.handle_notification(message)
                else:
                    self._response_queue.put(message)
            except (ConnectionError, json.JSONDecodeError, OSError):
                break
            except Exception:
                traceback.print_exc()
        self._connected = False

    def handle_notification(self, data: dict) -> None:
        """Handle notification from server. Override in subclasses."""
        pass

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

            response = _recv_message(self._sock, timeout=HANDSHAKE_TIMEOUT)
            if response.get("status") != 200:
                raise ConnectionError(f"Handshake failed: {response.get('message')}")

            info_dict = response.get("data", {}).get("info")
            if not info_dict:
                raise ConnectionError("Invalid handshake data from server")

            self._server_info = HandshakeInfo.from_dict(info_dict)

            if self._server_info.app_name != APP_NAME:
                raise ConnectionError("Invalid application")

            if self._server_info.protocol_version != PROTOCOL_VERSION:
                raise ConnectionError(f"Protocol version mismatch: server={self._server_info.protocol_version}, client={PROTOCOL_VERSION}")

            _send_message(self._sock, {
                "status": 200,
                "message": "Handshake",
                "data": {"info": self._get_handshake_info().to_dict()}
            })

            confirm = _recv_message(self._sock, timeout=HANDSHAKE_TIMEOUT)
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
            except Exception:
                pass
            self._sock = None
        self._server_info = None

    def send_command(self, command: str, **kwargs) -> Any:
        """Send a command to server and return result"""
        if not self._connected or not self._sock:
            raise ConnectionError("Not connected to server")

        _send_message(self._sock, {"command": command, "kwargs": kwargs if kwargs else None})

        try:
            response = self._response_queue.get(timeout=30)
        except queue.Empty:
            raise TimeoutError(f"Command '{command}' timed out")

        if response.get("status") == 200:
            return response.get("data")
        else:
            error_msg = response.get("message", "Unknown error")
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


WORKER_SOCKET_PATH = mirror.RUN_PATH / "worker.sock"


class WorkerServer(BaseServer):
    """
    Worker process server.
    Handles multiple sync operations/commands concurrently.
    """

    def __init__(self, socket_path: Optional[Path | str] = None):
        if socket_path is None:
            socket_path = WORKER_SOCKET_PATH
        super().__init__(socket_path, role="worker")

    def send_finished_notification(self, job_id: str, success: bool, returncode: Optional[int]):
        """Broadcast a notification that a job has finished."""
        if self.client_count == 0:
            raise ConnectionError("No clients connected to receive notification")

        self.broadcast({
            "type": "notification",
            "event": "job_finished",
            "job_id": job_id,
            "success": success,
            "returncode": returncode
        })

    @expose("ping")
    def _handle_ping(self) -> dict:
        """Health check"""
        return {"message": "pong"}

    @expose("status")
    def _handle_status(self) -> dict:
        """Get worker status and list of active jobs"""
        import mirror.worker.process as process
        process.prune_finished()
        jobs = process.get_all()
        return {
            "running": self.running,
            "role": self.role,
            "version": mirror.__version__,
            "socket": str(self.socket_path),
            "active_jobs": [j.id for j in jobs if j.is_running]
        }

    @expose("execute_command")
    def _handle_execute_command(self, job_id: str, commandline: list[str], env: dict, sync_method: str = "execute", uid: Optional[int] = None, gid: Optional[int] = None, nice: int = 0, log_path: Optional[str] = None) -> dict:
        """Execute a shell command for a job."""
        import mirror.worker.process as process

        process.prune_finished()

        if not uid:
            uid = os.getuid()
        if not gid:
            gid = os.getgid()

        job = process.create(
            job_id=job_id,
            commandline=commandline,
            env=env,
            uid=uid,
            gid=gid,
            nice=nice,
            log_path=Path(log_path) if log_path else None
        )

        return {
            "job_id": job_id,
            "sync_method": sync_method,
            "status": "started",
            "job_pid": job.pid,
            "has_fds": False
        }

    @expose("stop_command")
    def _handle_stop_command(self, job_id: Optional[str] = None) -> dict:
        """Stop a specific job or all jobs if no job_id is provided"""
        import mirror.worker.process as process

        if job_id:
            job = process.get(job_id)
            if job:
                job.stop()
                return {"job_id": job_id, "status": "stopped"}
            return {"job_id": job_id, "status": "not_found"}
        else:
            jobs = process.get_all()
            stopped = []
            for job in jobs:
                if job.is_running:
                    job.stop()
                    stopped.append(job.id)
            return {"status": "all_stopped", "stopped_jobs": stopped}

    @expose("get_progress")
    def _handle_get_progress(self, job_id: Optional[str] = None) -> dict:
        """Get progress for a specific job or all active jobs"""
        import mirror.worker.process as process
        process.prune_finished()

        if job_id:
            job = process.get(job_id)
            if job:
                return {
                    "job_id": job_id,
                    "syncing": job.is_running,
                    "progress": 0,
                    "info": job.info()
                }
            return {"job_id": job_id, "syncing": False, "status": "not_found"}
        else:
            jobs = process.get_all()
            return {
                "syncing": any(j.is_running for j in jobs),
                "jobs": {j.id: {"running": j.is_running, "info": j.info()} for j in jobs}
            }


class WorkerClient(BaseClient):
    """
    Client for connecting to Worker processes.
    Used by Master to manage workers.
    """

    def __init__(self, socket_path: Optional[Path | str] = None):
        if socket_path is None:
            socket_path = WORKER_SOCKET_PATH
        super().__init__(socket_path, role="master")

    def handle_notification(self, data: dict) -> None:
        """Handle notification from worker server"""
        if data.get("event") == "job_finished":
            import mirror.sync
            job_id = data.get("job_id")
            success = data.get("success", False)
            returncode = data.get("returncode")
            mirror.sync.on_sync_done(job_id, success, returncode)

    def ping(self) -> dict:
        """Health check"""
        return self.send_command("ping")

    def status(self) -> dict:
        """Get worker status"""
        return self.send_command("status")

    def execute_command(self, job_id: str, commandline: list[str], env: dict, sync_method: str = "execute", uid: Optional[int] = None, gid: Optional[int] = None, nice: int = 0, log_path: Optional[str] = None) -> dict:
        """Execute a shell command"""
        return self.send_command("execute_command", job_id=job_id, commandline=commandline, env=env, sync_method=sync_method, uid=uid, gid=gid, nice=nice, log_path=log_path)

    def stop_command(self, job_id: Optional[str] = None) -> dict:
        """Stop current command"""
        return self.send_command("stop_command", job_id=job_id)

    def get_progress(self, job_id: Optional[str] = None) -> dict:
        """Get current sync progress"""
        return self.send_command("get_progress", job_id=job_id)


__all__ = [
    "BaseServer",
    "BaseClient",
    "HandshakeInfo",
    "PROTOCOL_VERSION",
    "APP_NAME",
    "HANDSHAKE_TIMEOUT",
    "expose",
    "WorkerServer",
    "WorkerClient",
    "WORKER_SOCKET_PATH",
]
