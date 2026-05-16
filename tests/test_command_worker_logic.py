import pytest
import signal
import sys
import logging
import json
from unittest.mock import MagicMock, patch
from pathlib import Path
from mirror.command.worker import worker

@pytest.fixture
def mock_server():
    # Patch at source to avoid shadowing issues
    with patch("mirror.socket.worker.WorkerServer") as mock:
        yield mock

@pytest.fixture
def mock_signal():
    with patch("signal.signal") as mock:
        yield mock

@pytest.fixture
def mock_sys_exit():
    with patch("sys.exit") as mock:
        yield mock

@pytest.fixture
def mock_logging():
    with patch("logging.basicConfig") as mock_basic:
        with patch("logging.getLogger") as mock_get:
            yield mock_basic, mock_get

def test_worker_initialization(mock_server, mock_signal, mock_logging, mock_sys_exit):
    # Setup mocks
    server_instance = mock_server.return_value
    
    # Run worker (we need to break the infinite loop)
    with patch("time.sleep", side_effect=Exception("BreakLoop")):
        worker("config.json", "/tmp/socket")
    
    # Verify it exited with error because of our forced exception
    mock_sys_exit.assert_called_with(1)

    # Verify Server initialization
    mock_server.assert_called_with(socket_path="/tmp/socket")
    server_instance.set_version.assert_called()
    server_instance.start.assert_called()


    # Verify Signal handlers
    assert mock_signal.call_count == 2
    
    # We can check if signal was called with SIGINT and SIGTERM
    args_list = mock_signal.call_args_list
    signals_registered = [args[0][0] for args in args_list]
    assert signal.SIGINT in signals_registered
    assert signal.SIGTERM in signals_registered

def test_worker_signal_handling(mock_server):
    server_instance = mock_server.return_value
    
    # Capture the signal handler
    signal_handler = None
    def capture_signal(sig, handler):
        nonlocal signal_handler
        if sig == signal.SIGINT:
            signal_handler = handler
            
    with patch("signal.signal", side_effect=capture_signal):
        with patch("time.sleep", side_effect=Exception("BreakLoop")):
            try:
                worker("config.json")
            except:
                pass

    assert signal_handler is not None
    
    # Trigger signal handler
    with patch("sys.exit") as mock_exit:
        signal_handler(signal.SIGINT, None)
        server_instance.stop.assert_called()
        mock_exit.assert_called_with(0)

def test_worker_config_usage(mock_server, mock_logging):
    mock_basic_config, _ = mock_logging
    
    # Create a mock config content
    mock_config = '{"settings": {"logger": {"level": "DEBUG"}}}'
    
    with patch("builtins.open", new_callable=MagicMock) as mock_open:
        mock_file = MagicMock()
        mock_file.__enter__.return_value.read.return_value = mock_config
        
        # Mock json.load to return the dict
        with patch("json.load", return_value={"settings": {"logger": {"level": "DEBUG"}}}) as mock_json:
            with patch("pathlib.Path.exists", return_value=True):
                 with patch("time.sleep", side_effect=Exception("BreakLoop")):
                    try:
                        worker("config.json")
                    except:
                        pass
    
    # Verify basicConfig was called with DEBUG level
    mock_basic_config.assert_called_with(
        level=logging.DEBUG,
        format="[%(asctime)s] %(levelname)s # %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )


def test_worker_reads_socket_path_without_loading_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    stat_path = tmp_path / "stat.json"
    socket_dir = tmp_path / "sockets"
    stat_path.write_text('{"packages": {"pkg": {"lastsync": 123.0}}}')
    config_path.write_text(json.dumps({
        "settings": {
            "logger": {"level": "INFO"},
            "socket_path": str(socket_dir),
            "statfile": str(stat_path),
        }
    }))
    before = stat_path.read_text()
    init_calls = []
    monkeypatch.setattr("mirror.config.SOCKET_PATH", None, raising=False)

    monkeypatch.setattr(
        "mirror.config.load",
        lambda path: (_ for _ in ()).throw(AssertionError("must not load config")),
    )

    def fake_init(role, **kwargs):
        init_calls.append({"role": role, "kwargs": kwargs})
        server = MagicMock()
        server.socket_path = socket_dir / "worker.sock"
        return server

    monkeypatch.setattr("mirror.socket.init", fake_init)
    monkeypatch.setattr("mirror.worker.manage", lambda: (_ for _ in ()).throw(Exception("BreakLoop")))
    monkeypatch.setattr("sys.exit", lambda code: None)

    worker(str(config_path))

    assert init_calls == [{"role": "worker", "kwargs": {"socket_path": None}}]
    import mirror.config
    assert mirror.config.SOCKET_PATH == str(socket_dir)
    assert stat_path.read_text() == before


def test_worker_explicit_socket_path_takes_precedence(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "settings": {
            "logger": {"level": "INFO"},
            "socket_path": str(tmp_path / "configured"),
        }
    }))
    init_calls = []

    def fake_init(role, **kwargs):
        init_calls.append({"role": role, "kwargs": kwargs})
        server = MagicMock()
        server.socket_path = Path(kwargs["socket_path"])
        return server

    monkeypatch.setattr("mirror.socket.init", fake_init)
    monkeypatch.setattr("mirror.worker.manage", lambda: (_ for _ in ()).throw(Exception("BreakLoop")))
    monkeypatch.setattr("sys.exit", lambda code: None)

    worker(str(config_path), "/tmp/explicit.sock")

    assert init_calls == [{"role": "worker", "kwargs": {"socket_path": "/tmp/explicit.sock"}}]
