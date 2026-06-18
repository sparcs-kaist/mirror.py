
import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import logging
import tempfile
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
        mirror.conf.uid = 1234
        mirror.conf.gid = 5678
        mirror.conf.logfolder = Path("/tmp/mirror-ftpsync")
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
        self.assertEqual(call_kwargs.get("uid"), mirror.conf.uid)
        self.assertEqual(call_kwargs.get("gid"), mirror.conf.gid)

    @patch('mirror.socket.worker.execute_command')
    @patch('mirror.sync.ftpsync.setup_ftpsync')
    def test_ftpsync_uses_log_helper_when_file_handler_exists(self, mock_setup_ftpsync, mock_execute_command):
        import mirror.sync.ftpsync as ftpsync_module

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            mirror.conf.logfolder = base / "ftpsync"
            package_log = base / "package.log"
            handler = logging.FileHandler(package_log)
            mock_logger = MagicMock()
            mock_logger.handlers = [handler]

            try:
                ftpsync_module.execute(self.ftp_pkg, mock_logger)
            finally:
                handler.close()

            call_kwargs = mock_execute_command.call_args[1]
            self.assertNotEqual(Path(call_kwargs["log_path"]), package_log)
            self.assertEqual(Path(call_kwargs["log_path"]).name, "prelude.log")
            helper = call_kwargs["log_helper_command"]
            self.assertIsInstance(helper, list)
            self.assertIn("mirror.worker.logmerge", helper)
            self.assertIn(str(package_log), helper)

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
        self.assertEqual(call_kwargs.get("uid"), mirror.conf.uid)
        self.assertEqual(call_kwargs.get("gid"), mirror.conf.gid)

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

    @patch('mirror.socket.worker.execute_command')
    def test_lftp_delegation_uses_configured_uid_gid(self, mock_execute_command):
        pkg = MagicMock(spec=mirror.structure.Package)
        pkg.pkgid = "test-lftp"
        pkg.name = "Test LFTP"
        pkg.settings = MagicMock()
        pkg.settings.src = "ftp://ftp.example.org/path"
        pkg.settings.dst = "/srv/mirror/lftp"
        pkg.settings.options = {}

        mock_logger = MagicMock()
        mock_logger.handlers = []

        import mirror.sync.lftp as lftp_module
        lftp_module.execute(pkg, mock_logger)

        mock_execute_command.assert_called_once()
        call_kwargs = mock_execute_command.call_args[1]
        self.assertEqual(call_kwargs.get("uid"), mirror.conf.uid)
        self.assertEqual(call_kwargs.get("gid"), mirror.conf.gid)

    @patch('mirror.socket.worker.execute_command')
    def test_bandersnatch_delegation_uses_configured_uid_gid(self, mock_execute_command):
        pkg = MagicMock(spec=mirror.structure.Package)
        pkg.pkgid = "test-bandersnatch"
        pkg.name = "Test Bandersnatch"

        mock_logger = MagicMock()
        mock_logger.handlers = []

        import mirror.sync.bandersnatch as bandersnatch_module
        bandersnatch_module.execute(pkg, mock_logger)

        mock_execute_command.assert_called_once()
        call_kwargs = mock_execute_command.call_args[1]
        self.assertEqual(call_kwargs.get("uid"), mirror.conf.uid)
        self.assertEqual(call_kwargs.get("gid"), mirror.conf.gid)

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

    def test_rsync_ffts_check_uses_process_timeout(self):
        from mirror.sync import rsync as rsync_mod

        pkg = MagicMock()
        pkg.pkgid = "ffts_timeout_test"
        pkg.name = "FFTS Timeout Test"
        pkg.settings.src = "rsync://example.org/test"
        pkg.settings.dst = "/tmp/test_dst"
        pkg.settings.options = {"fftsfile": "filelist"}
        pkg_logger = MagicMock()

        result = MagicMock()
        result.returncode = 0
        result.stdout = ""

        with patch.object(rsync_mod.subprocess, "run", return_value=result) as run:
            rsync_mod.check_ffts_update(pkg, pkg_logger)

        self.assertEqual(run.call_args.kwargs["timeout"], 60)

    def test_rsync_ffts_check_timeout_assumes_update_needed(self):
        from mirror.sync import rsync as rsync_mod

        pkg = MagicMock()
        pkg.pkgid = "ffts_timeout_test"
        pkg.name = "FFTS Timeout Test"
        pkg.settings.src = "rsync://example.org/test"
        pkg.settings.dst = "/tmp/test_dst"
        pkg.settings.options = {"fftsfile": "filelist"}
        pkg_logger = MagicMock()

        timeout = rsync_mod.subprocess.TimeoutExpired(cmd=["rsync"], timeout=60)
        with patch.object(rsync_mod.subprocess, "run", side_effect=timeout):
            needs_update = rsync_mod.check_ffts_update(pkg, pkg_logger)

        self.assertTrue(needs_update)


class TestRsyncOptions(unittest.TestCase):
    """Tests for rsync option_exclude, option_include, and exclude list support."""

    def _make_logger(self):
        return MagicMock(spec=logging.Logger)

    def _call_rsync(self, **kwargs):
        from mirror.sync.rsync import rsync
        logger = self._make_logger()
        return rsync(logger, "pkg", "rsync://example.org/mod", Path("/srv/dst"), "", "", **kwargs)

    # --- flag manipulation ---

    def test_option_exclude_removes_flag(self):
        command, _ = self._call_rsync(option_exclude="S")
        flags_arg = command[1]
        self.assertIn("-", flags_arg)
        self.assertNotIn("S", flags_arg)
        self.assertIn("v", flags_arg)
        self.assertIn("H", flags_arg)
        # full default minus S
        self.assertEqual(flags_arg, "-vrltDH")

    def test_option_include_adds_flag(self):
        command, _ = self._call_rsync(option_include="z")
        flags_arg = command[1]
        self.assertIn("z", flags_arg)
        self.assertTrue(flags_arg.startswith("-vrltDSH"))
        self.assertTrue(flags_arg.endswith("z"))

    def test_option_include_no_duplicate(self):
        command, _ = self._call_rsync(option_include="v")
        flags_arg = command[1]
        self.assertEqual(flags_arg.count("v"), 1)

    def test_option_include_and_exclude_combined(self):
        # include "z" then exclude "S": result = vrltDHz
        command, _ = self._call_rsync(option_include="z", option_exclude="S")
        flags_arg = command[1]
        self.assertIn("z", flags_arg)
        self.assertNotIn("S", flags_arg)
        self.assertIn("v", flags_arg)

    def test_default_options_unchanged(self):
        command, _ = self._call_rsync()
        self.assertIn("-vrltDSH", command)

    def test_option_exclude_all_flags_no_lone_dash(self):
        # Remove every default flag character; no lone "-" should appear in argv
        command, _ = self._call_rsync(option_exclude="vrltDSH")
        for arg in command:
            self.assertNotEqual(arg, "-")
        # The flags arg should be absent entirely
        self.assertNotIn("-vrltDSH", command)

    # --- exclude list ---

    def test_exclude_list_adds_patterns(self):
        command, _ = self._call_rsync(excludes=["S3-mirror", "*.bak"])
        self.assertIn("--exclude=*.~tmp~", command)
        self.assertIn("--exclude=S3-mirror", command)
        self.assertIn("--exclude=*.bak", command)

    def test_exclude_list_hardcoded_pattern_always_present(self):
        command, _ = self._call_rsync(excludes=["extra"])
        self.assertIn("--exclude=*.~tmp~", command)

    def test_exclude_list_user_excludes_before_src_dst(self):
        command, _ = self._call_rsync(excludes=["foo"])
        exclude_idx = command.index("--exclude=foo")
        src_idx = command.index("rsync://example.org/mod/")
        self.assertLess(exclude_idx, src_idx)

    def test_exclude_leading_dash_allowed(self):
        command, _ = self._call_rsync(excludes=["-foo"])
        self.assertIn("--exclude=-foo", command)

    # --- validation: option_include whitelist ---

    def test_option_include_blocked_flag_e_raises(self):
        from mirror.sync.rsync import rsync
        logger = self._make_logger()
        with self.assertRaises(ValueError):
            rsync(logger, "p", "s", Path("d"), "", "", option_include="e")

    def test_option_include_dash_raises(self):
        from mirror.sync.rsync import rsync
        logger = self._make_logger()
        with self.assertRaises(ValueError):
            rsync(logger, "p", "s", Path("d"), "", "", option_include="-")

    def test_option_include_space_raises(self):
        from mirror.sync.rsync import rsync
        logger = self._make_logger()
        with self.assertRaises(ValueError):
            rsync(logger, "p", "s", Path("d"), "", "", option_include=" ")

    def test_option_include_non_string_raises(self):
        from mirror.sync.rsync import rsync
        logger = self._make_logger()
        with self.assertRaises(ValueError):
            rsync(logger, "p", "s", Path("d"), "", "", option_include=42)

    # --- validation: option_exclude ---

    def test_option_exclude_dash_raises(self):
        from mirror.sync.rsync import rsync
        logger = self._make_logger()
        with self.assertRaises(ValueError):
            rsync(logger, "p", "s", Path("d"), "", "", option_exclude="-")

    def test_option_exclude_space_raises(self):
        from mirror.sync.rsync import rsync
        logger = self._make_logger()
        with self.assertRaises(ValueError):
            rsync(logger, "p", "s", Path("d"), "", "", option_exclude=" ")

    def test_option_exclude_non_string_raises(self):
        from mirror.sync.rsync import rsync
        logger = self._make_logger()
        with self.assertRaises(ValueError):
            rsync(logger, "p", "s", Path("d"), "", "", option_exclude=["S"])

    # --- validation: excludes list ---

    def test_excludes_not_list_raises(self):
        from mirror.sync.rsync import _validate_excludes
        with self.assertRaises(ValueError):
            _validate_excludes("not-a-list")

    def test_excludes_non_string_item_raises(self):
        from mirror.sync.rsync import _validate_excludes
        with self.assertRaises(ValueError):
            _validate_excludes([123])

    def test_excludes_control_char_raises(self):
        from mirror.sync.rsync import _validate_excludes
        with self.assertRaises(ValueError):
            _validate_excludes(["foo\nbar"])

    def test_excludes_nul_raises(self):
        from mirror.sync.rsync import _validate_excludes
        with self.assertRaises(ValueError):
            _validate_excludes(["foo\x00bar"])

    # --- execute() path: options read from package.settings.options ---

    @patch('mirror.socket.worker.execute_command')
    def test_execute_option_exclude_applied(self, mock_execute_command):
        pkg = MagicMock()
        pkg.pkgid = "rsync-opt-test"
        pkg.name = "Option Test"
        pkg.settings.src = "rsync://example.org/mod"
        pkg.settings.dst = "/srv/dst"
        pkg.settings.options = {
            "ffts": False,
            "user": "",
            "password": "",
            "option_exclude": "S",
        }
        pkg.syncrate = 3600
        pkg.lastsync = 0
        pkg_logger = MagicMock()
        pkg_logger.handlers = []
        mock_execute_command.return_value = {}

        import mirror.sync.rsync as rsync_module
        rsync_module.execute(pkg, pkg_logger)

        mock_execute_command.assert_called_once()
        cmd = mock_execute_command.call_args[1]["commandline"]
        self.assertIn("-vrltDH", cmd)
        self.assertNotIn("-vrltDSH", cmd)

    @patch('mirror.socket.worker.execute_command')
    def test_execute_invalid_option_include_calls_on_sync_done(self, mock_execute_command):
        import mirror.sync.rsync as rsync_module
        pkg = MagicMock()
        pkg.pkgid = "rsync-invalid-test"
        pkg.name = "Invalid Option Test"
        pkg.settings.src = "rsync://example.org/mod"
        pkg.settings.dst = "/srv/dst"
        pkg.settings.options = {
            "ffts": False,
            "user": "",
            "password": "",
            "option_include": "e",
        }
        pkg.syncrate = 3600
        pkg.lastsync = 0
        pkg_logger = MagicMock()
        pkg_logger.handlers = []

        with patch("mirror.sync.on_sync_done") as on_done:
            rsync_module.execute(pkg, pkg_logger)

        mock_execute_command.assert_not_called()
        on_done.assert_called_once_with(pkg.pkgid, success=False, returncode=None)

    # --- validation: DEL (0x7f) control character ---

    def test_option_include_del_char_raises(self):
        from mirror.sync.rsync import rsync
        logger = self._make_logger()
        with self.assertRaises(ValueError):
            rsync(logger, "p", "s", Path("d"), "", "", option_include="\x7f")

    def test_option_exclude_del_char_raises(self):
        from mirror.sync.rsync import rsync
        logger = self._make_logger()
        with self.assertRaises(ValueError):
            rsync(logger, "p", "s", Path("d"), "", "", option_exclude="\x7f")

    def test_excludes_del_char_raises(self):
        from mirror.sync.rsync import _validate_excludes
        with self.assertRaises(ValueError):
            _validate_excludes(["foo\x7fbar"])

    # --- validation via execute() path ---

    def _run_execute_with_options(self, options):
        """Run rsync.execute with the given options dict and capture on_sync_done."""
        import mirror.sync.rsync as rsync_module
        pkg = MagicMock()
        pkg.pkgid = "rsync-execpath-test"
        pkg.name = "Exec Path Test"
        pkg.settings.src = "rsync://example.org/mod"
        pkg.settings.dst = "/srv/dst"
        pkg.settings.options = {"ffts": False, "user": "", "password": "", **options}
        pkg.syncrate = 3600
        pkg.lastsync = 0
        pkg_logger = MagicMock()
        pkg_logger.handlers = []
        with patch("mirror.socket.worker.execute_command") as mock_exec, \
                patch("mirror.sync.on_sync_done") as on_done:
            rsync_module.execute(pkg, pkg_logger)
        return mock_exec, on_done, pkg.pkgid

    def test_execute_invalid_options_route_to_on_sync_done(self):
        invalid_options = [
            {"option_exclude": "-"},
            {"option_exclude": " "},
            {"option_exclude": ["S"]},
            {"option_include": "-"},
            {"option_include": " "},
            {"option_include": 42},
            {"exclude": "not-a-list"},
            {"exclude": ["foo\nbar"]},
        ]
        for options in invalid_options:
            with self.subTest(options=options):
                mock_exec, on_done, pkgid = self._run_execute_with_options(options)
                mock_exec.assert_not_called()
                on_done.assert_called_once_with(pkgid, success=False, returncode=None)


if __name__ == '__main__':
    unittest.main()
