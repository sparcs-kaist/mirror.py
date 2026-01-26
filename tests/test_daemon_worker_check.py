
import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import time
from pathlib import Path

# Ensure PYTHONPATH is set
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import mirror
# Make sure the module is loaded
import mirror.command.daemon as daemon_mod

class TestDaemonWorkerCheck(unittest.TestCase):
    
    def test_daemon_startup_worker_check(self):
        # We'll use local patches inside the test to avoid annotation issues with shadowing
        
        # Get the actual module object to patch its attributes
        # Since 'mirror.command.daemon' might be the function in mirror.command,
        # we find it in sys.modules.
        mod = sys.modules['mirror.command.daemon']
        
        with patch.object(mod, 'MasterServer') as mock_master, \
             patch.object(mod, 'WorkerClient') as mock_worker_client, \
             patch('mirror.config.load'), \
             patch('mirror.logger.setup_logger'), \
             patch('mirror.sync.start'), \
             patch('time.sleep', side_effect=KeyboardInterrupt):
            
            # Setup Mocks
            mirror.log = MagicMock()
            mirror.packages = {}
            mirror.__version__ = "1.0.0"
            
            # Case 1: Worker is running
            mock_client_instance = MagicMock()
            mock_worker_client.return_value.__enter__.return_value = mock_client_instance
            mock_client_instance.ping.return_value = {"message": "pong"}
            
            try:
                mod.daemon("dummy_config.json")
            except KeyboardInterrupt:
                pass
                
            mirror.log.info.assert_any_call("Worker server is running and reachable.")
            
            # Case 2: Worker is NOT running
            mock_worker_client.return_value.__enter__.side_effect = Exception("Connection refused")
            mirror.log.reset_mock()
            
            try:
                mod.daemon("dummy_config.json")
            except KeyboardInterrupt:
                pass
                
            mirror.log.error.assert_any_call("Worker server is NOT running. Sync operations may fail if they rely on it.")

    def test_daemon_loop_monitoring(self):
        mod = sys.modules['mirror.command.daemon']
        
        with patch.object(mod, 'MasterServer'), \
             patch.object(mod, 'WorkerClient') as mock_worker_client, \
             patch('mirror.config.load'), \
             patch('mirror.logger.setup_logger'), \
             patch('mirror.sync.start') as mock_sync_start, \
             patch('time.sleep') as mock_sleep:
            
            # Mock mirror.log
            mirror.log = MagicMock()
            
            # Setup a package
            pkg = MagicMock()
            pkg.pkgid = "test_pkg"
            pkg.is_disabled.return_value = False
            pkg.is_syncing.side_effect = [False, True, False]
            pkg.lastsync = 0
            pkg.syncrate = 0
            pkg.status = "ACTIVE"
            
            mirror.packages = {"test_pkg": pkg}
            
            # Mock sleep to run exactly 3 iterations then stop
            mock_sleep.side_effect = [None, None, KeyboardInterrupt]
            
            # Mock worker running
            mock_worker_client.return_value.__enter__.return_value.ping.return_value = {}

            try:
                mod.daemon("dummy_config.json")
            except KeyboardInterrupt:
                pass
                
            # Verify it started sync
            mock_sync_start.assert_called_with(pkg)
            
            # Verify it logged transition to syncing
            mirror.log.info.assert_any_call(f"Package {pkg.pkgid} is now syncing...")
            
            # Verify it logged completion
            mirror.log.info.assert_any_call(f"Package {pkg.pkgid} sync finished. Status: {pkg.status}")

if __name__ == '__main__':
    unittest.main()
