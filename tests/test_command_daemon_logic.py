import pytest
import signal
import sys
import logging
from unittest.mock import MagicMock, patch
from pathlib import Path
from mirror.command.daemon import daemon
import mirror

@pytest.fixture
def mock_master_server():
    with patch("mirror.command.daemon.MasterServer") as mock:
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
def mock_dependencies():
    with patch("mirror.config.load"), \
         patch("mirror.logger.setup_logger"), \
         patch("mirror.sync.start"):
        
        # Setup mirror.packages
        original_packages = getattr(mirror, "packages", None)
        original_log = getattr(mirror, "log", None)
        
        mirror.packages = {}
        mirror.log = MagicMock()
        
        yield
        
        # Restore (though usually not strictly needed if running in isolated process, but good practice)
        if original_packages is not None:
            mirror.packages = original_packages
        if original_log is not None:
            mirror.log = original_log

def test_daemon_initialization(mock_master_server, mock_signal, mock_dependencies, mock_sys_exit):
    server_instance = mock_master_server.return_value
    
    # Run daemon (break loop with exception)
    with patch("time.sleep", side_effect=Exception("BreakLoop")):
        daemon("config.json")
        
    # Verify Server initialization
    mock_master_server.assert_called_once()
    server_instance.set_version.assert_called()
    server_instance.start.assert_called()
    
    # Verify Signal handlers
    assert mock_signal.call_count == 2
    args_list = mock_signal.call_args_list
    signals_registered = [args[0][0] for args in args_list]
    assert signal.SIGINT in signals_registered
    assert signal.SIGTERM in signals_registered
    
    # Verify exit called (due to exception)
    mock_sys_exit.assert_called_with(1)

def test_daemon_signal_handling(mock_master_server, mock_dependencies):
    server_instance = mock_master_server.return_value
    
    signal_handler = None
    def capture_signal(sig, handler):
        nonlocal signal_handler
        if sig == signal.SIGINT:
            signal_handler = handler
            
    with patch("signal.signal", side_effect=capture_signal):
        with patch("time.sleep", side_effect=Exception("BreakLoop")):
            # Suppress exit to continue test
            with patch("sys.exit") as mock_exit:
                daemon("config.json")
    
    assert signal_handler is not None
    
    # Trigger signal handler
    with patch("sys.exit") as mock_exit:
        signal_handler(signal.SIGINT, None)
        server_instance.stop.assert_called()
        mock_exit.assert_called_with(0)
