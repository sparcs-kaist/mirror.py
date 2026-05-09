
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
        # Resync mirror submodule attributes with sys.modules in case a prior
        # test replaced them (e.g. test_socket.py's _load_module pattern)
        import mirror.socket
        mirror.sync = sys.modules["mirror.sync"]
        mirror.socket.worker = sys.modules["mirror.socket.worker"]

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

        # Check that uid and gid are passed
        self.assertEqual(call_kwargs.get("uid"), os.getuid())
        self.assertEqual(call_kwargs.get("gid"), os.getgid())

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
        # execute() should not call set_status — sync.start() handles SYNC,
        # on_sync_done() handles ACTIVE/ERROR
        self.ftp_pkg.set_status.assert_not_called()

        # Check if setup_ftpsync was called to prepare the environment
        mock_setup_ftpsync.assert_called_once()

        # Check if execute_command was called with the ftpsync script path
        mock_execute_command.assert_called_once()
        call_kwargs = mock_execute_command.call_args[1]
        self.assertEqual(call_kwargs['job_id'], "test-ftpsync")
        self.assertTrue(call_kwargs['commandline'][0].endswith("ftpsync"))

    @patch('mirror.socket.worker.execute_command')
    @patch('mirror.sync.ftpsync.setup_ftpsync')
    def test_ftpsync_extra_args_passed_as_env(self, mock_setup_ftpsync, mock_execute_command):
        # Seed extra_args directly to simulate an in-flight sync started with extra_args
        pkgid = self.ftp_pkg.pkgid
        seeded = {
            "SSH_ORIGINAL_COMMAND": "ftpsync sync:archive:debian",
            "SSH_CONNECTION": "203.0.113.10 54321 198.51.100.5 22",
        }
        mirror.sync._extra_args[pkgid] = seeded

        mock_logger = MagicMock()
        mock_logger.handlers = []
        mock_execute_command.return_value = {"status": "started", "job_pid": 789}

        import mirror.sync.ftpsync as ftpsync_module
        try:
            ftpsync_module.execute(self.ftp_pkg, mock_logger)
        finally:
            mirror.sync._extra_args.pop(pkgid, None)

        mock_execute_command.assert_called_once()
        call_kwargs = mock_execute_command.call_args[1]
        self.assertEqual(call_kwargs['env'], seeded)

    def test_rsync_ffts_uptodate_routes_through_on_sync_done(self):
        """When FFTS reports up-to-date, rsync.execute must call on_sync_done, not execute_command."""
        from unittest.mock import patch
        from mirror.sync import rsync as rsync_mod

        pkg = MagicMock()
        pkg.pkgid = "ffts_uptodate_test"
        pkg.name = "FFTS Test"
        pkg.settings.src = "rsync://example.org/test"
        pkg.settings.dst = "/tmp/test_dst"
        pkg.settings.options = {"ffts": True, "fftsfile": "filelist"}
        pkg_logger = MagicMock()
        pkg_logger.handlers = []

        with patch.object(rsync_mod, "check_ffts_update", return_value=False) as ffts_mock, \
             patch("mirror.sync.on_sync_done") as on_done, \
             patch("mirror.socket.worker.execute_command") as exec_cmd:
            rsync_mod.execute(pkg, pkg_logger)

        ffts_mock.assert_called_once_with(pkg, pkg_logger)
        on_done.assert_called_once_with(pkg.pkgid, success=True, returncode=0)
        exec_cmd.assert_not_called()


if __name__ == '__main__':
    unittest.main()
