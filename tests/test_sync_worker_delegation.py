
import unittest
from unittest.mock import MagicMock, patch
import os
import sys
from pathlib import Path

# Set PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import mirror
import mirror.sync
import mirror.structure

class TestSyncWorkerDelegation(unittest.TestCase):
    def setUp(self):
        # Mock default settings
        mirror.log = MagicMock()
        mirror.packages = {}
        mirror.conf = MagicMock()
        mirror.conf.logger = {"fileformat": {"gzip": True}}

        # Create a virtual package (for rsync)
        self.rsync_pkg = MagicMock(spec=mirror.structure.Package)
        self.rsync_pkg.pkgid = "test-rsync"
        self.rsync_pkg.name = "Test Rsync"
        self.rsync_pkg.synctype = "rsync"
        self.rsync_pkg.settings = MagicMock()
        self.rsync_pkg.settings.src = "/remote/source"
        self.rsync_pkg.settings.dst = "/local/destination"
        self.rsync_pkg.settings.options = {
            "ffts": False,
            "user": "syncuser",
            "password": "syncpassword",
        }
        self.rsync_pkg.status = "ACTIVE"
        self.rsync_pkg.syncrate = 3600
        self.rsync_pkg.lastsync = 0

        # Create a virtual package (for ftpsync)
        self.ftp_pkg = MagicMock(spec=mirror.structure.Package)
        self.ftp_pkg.pkgid = "test-ftpsync"
        self.ftp_pkg.name = "Test FTPSync"
        self.ftp_pkg.synctype = "ftpsync"
        self.ftp_pkg.settings = MagicMock()
        self.ftp_pkg.settings.src = "ftp.debian.org"
        self.ftp_pkg.settings.dst = "/var/www/debian"
        self.ftp_pkg.settings.options = {
            "email": "admin@example.com",
            "hub": "false",
            "path": "/debian"
        }
        self.ftp_pkg.status = "ACTIVE"
        self.ftp_pkg.syncrate = 3600
        self.ftp_pkg.lastsync = 0

    @patch('mirror.socket.worker.execute_command')
    @patch('mirror.logger.create_logger')
    def test_rsync_delegation(self, mock_create_logger, mock_execute_command):
        # 1. Setup mocks
        mock_logger = MagicMock()
        mock_logger.handlers = []
        mock_create_logger.return_value = mock_logger
        mock_execute_command.return_value = {"status": "started", "job_pid": 123}

        # 2. Execute rsync sync
        import mirror.sync.rsync as rsync_module
        rsync_module.execute(self.rsync_pkg, mock_logger)

        # 3. Verify
        # Check if execute_command was called with correct args
        mock_execute_command.assert_called_once()
        call_kwargs = mock_execute_command.call_args[1]
        self.assertEqual(call_kwargs['job_id'], "test-rsync")
        self.assertIn("rsync", call_kwargs['commandline'])
        self.assertIn("/remote/source/", call_kwargs['commandline'])
        self.assertIn("/local/destination/", call_kwargs['commandline'])

        # Check if environment variables were passed (auth)
        self.assertEqual(call_kwargs['env']['RSYNC_PASSWORD'], "syncpassword")
        self.assertEqual(call_kwargs['env']['USER'], "syncuser")

    @patch('mirror.socket.worker.execute_command')
    @patch('mirror.logger.create_logger')
    @patch('mirror.sync.ftpsync.setup_ftpsync')
    def test_ftpsync_delegation(self, mock_setup_ftpsync, mock_create_logger, mock_execute_command):
        # 1. Setup mocks
        mock_logger = MagicMock()
        mock_logger.handlers = []
        mock_create_logger.return_value = mock_logger
        mock_execute_command.return_value = {"status": "started", "job_pid": 456}

        # 2. Execute ftpsync sync
        import mirror.sync.ftpsync as ftpsync_module
        ftpsync_module.execute(self.ftp_pkg, mock_logger)

        # 3. Verify
        # Check if status was set to SYNC
        self.ftp_pkg.set_status.assert_any_call("SYNC")

        # Check if setup_ftpsync was called to prepare the environment
        mock_setup_ftpsync.assert_called_once()

        # Check if execute_command was called with the ftpsync script path
        mock_execute_command.assert_called_once()
        call_kwargs = mock_execute_command.call_args[1]
        self.assertEqual(call_kwargs['job_id'], "test-ftpsync")
        self.assertTrue(call_kwargs['commandline'][0].endswith("ftpsync"))

if __name__ == '__main__':
    unittest.main()
