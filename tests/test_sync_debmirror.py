"""Tests for mirror.sync.debmirror — worker delegation, preflight, and build_command."""

import logging
import sys
import unittest
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.socket
import mirror.socket.worker
import mirror.structure
import mirror.sync
from mirror.sync.debmirror import build_command, execute, plugin, _redact_command


# ---------------------------------------------------------------------------
# Delegation / preflight / lifecycle-invariant tests (worker-boundary level)
# ---------------------------------------------------------------------------

class TestSyncDebmirrorDelegation(unittest.TestCase):
    def setUp(self):
        # Resync mirror submodule attributes with sys.modules in case a prior
        # test replaced them (see tests/test_sync_worker_delegation.py).
        mirror.sync = sys.modules["mirror.sync"]
        mirror.socket.worker = sys.modules["mirror.socket.worker"]

        mirror.log = MagicMock()
        mirror.packages = {}
        mirror.conf = MagicMock()
        mirror.conf.uid = 4242
        mirror.conf.gid = 4343

        self.pkg = MagicMock(spec=mirror.structure.Package)
        self.pkg.pkgid = "debmirror-debian"
        self.pkg.name = "Debian (debmirror)"
        self.pkg.synctype = "debmirror"
        self.pkg.settings = MagicMock()
        self.pkg.settings.src = "http://deb.debian.org/debian"
        self.pkg.settings.dst = "/srv/mirror/debmirror-debian"
        self.pkg.settings.options = {
            "dist": ["bookworm", "bookworm-updates"],
            "section": "main,contrib",
            "arch": "amd64",
        }
        self.pkg.status = "ACTIVE"
        self.pkg.syncrate = 3600
        self.pkg.lastsync = 0

    def _run_execute(self, pkg=None, options=None, dst=None):
        """Run execute() with command_exists=True and worker/on_sync_done mocked; return the mocks."""
        pkg = pkg if pkg is not None else self.pkg
        if options is not None:
            pkg.settings.options = options
        if dst is not None:
            pkg.settings.dst = dst
        mock_logger = MagicMock()
        mock_logger.handlers = []
        with patch("mirror.toolbox.command_exists", return_value=True) as mock_exists, \
                patch("mirror.socket.worker.execute_command") as mock_exec, \
                patch("mirror.sync.on_sync_done") as mock_on_done:
            execute(pkg, mock_logger)
        return mock_exists, mock_exec, mock_on_done

    def test_delegation_builds_expected_argv(self):
        _, mock_exec, mock_on_done = self._run_execute()

        mock_exec.assert_called_once()
        mock_on_done.assert_not_called()
        call_kwargs = mock_exec.call_args[1]
        cmd = call_kwargs["commandline"]

        self.assertEqual(cmd[0], "debmirror")
        self.assertEqual(cmd[cmd.index("--method") + 1], "http")
        self.assertEqual(cmd[cmd.index("--host") + 1], "deb.debian.org")
        self.assertEqual(cmd[cmd.index("--dist") + 1], "bookworm,bookworm-updates")
        self.assertEqual(cmd[-1], self.pkg.settings.dst)
        self.assertEqual(call_kwargs["sync_method"], "debmirror")
        self.assertEqual(call_kwargs.get("uid"), mirror.conf.uid)
        self.assertEqual(call_kwargs.get("gid"), mirror.conf.gid)
        self.assertEqual(call_kwargs["job_id"], self.pkg.pkgid)
        self.pkg.set_status.assert_not_called()

    @patch("mirror.socket.worker.execute_command")
    @patch("mirror.sync.on_sync_done")
    @patch("mirror.toolbox.command_exists", return_value=False)
    def test_preflight_missing_binary_routes_to_on_sync_done(
        self, mock_command_exists, mock_on_sync_done, mock_execute_command
    ):
        mock_logger = MagicMock()
        mock_logger.handlers = []

        execute(self.pkg, mock_logger)

        mock_execute_command.assert_not_called()
        mock_on_sync_done.assert_called_once_with(self.pkg.pkgid, success=False, returncode=None)
        self.pkg.set_status.assert_not_called()

    def test_build_error_missing_dist_routes_to_on_sync_done(self):
        _, mock_exec, mock_on_done = self._run_execute(options={})

        mock_exec.assert_not_called()
        mock_on_done.assert_called_once_with(self.pkg.pkgid, success=False, returncode=None)
        self.pkg.set_status.assert_not_called()

    def test_relative_dst_routes_to_on_sync_done(self):
        _, mock_exec, mock_on_done = self._run_execute(
            options={"dist": "bookworm"}, dst="relative/path"
        )

        mock_exec.assert_not_called()
        mock_on_done.assert_called_once_with(self.pkg.pkgid, success=False, returncode=None)
        self.pkg.set_status.assert_not_called()

    def test_invalid_options_route_to_on_sync_done(self):
        invalid_option_sets = [
            {},
            {"dist": ""},
            {"dist": "book\nworm"},
            {"dist": "bookworm", "method": "gopher"},
            {"dist": "bookworm", "diff": "bogus"},
            {"dist": "bookworm", "timeout": "30"},
        ]
        for options in invalid_option_sets:
            with self.subTest(options=options):
                pkg = MagicMock(spec=mirror.structure.Package)
                pkg.pkgid = "debmirror-invalid"
                pkg.name = "Debmirror Invalid"
                pkg.settings = MagicMock()
                pkg.settings.src = "http://deb.debian.org/debian"
                pkg.settings.dst = "/srv/mirror/debian"
                pkg.settings.options = options

                _, mock_exec, mock_on_done = self._run_execute(pkg=pkg)

                mock_exec.assert_not_called()
                mock_on_done.assert_called_once_with(pkg.pkgid, success=False, returncode=None)
                pkg.set_status.assert_not_called()

    def test_execute_warns_when_keyring_file_missing(self):
        """The keyring file-existence check lives in execute() (not the pure
        builder); a missing file logs a warning but still delegates."""
        mock_logger = MagicMock()
        mock_logger.handlers = []
        self.pkg.settings.options = {"dist": "bookworm", "keyring": "/nonexistent/keyring.gpg"}
        with patch("mirror.toolbox.command_exists", return_value=True), \
                patch("mirror.socket.worker.execute_command") as mock_exec, \
                patch("mirror.sync.on_sync_done"):
            execute(self.pkg, mock_logger)
        mock_exec.assert_called_once()
        self.assertTrue(
            any(
                "keyring not found" in str(call.args[0]).lower()
                for call in mock_logger.warning.call_args_list
            )
        )

    def test_execute_warns_on_http_auth(self):
        """execute() warns that credentials are ignored for methods without inline auth."""
        mock_logger = MagicMock()
        mock_logger.handlers = []
        self.pkg.settings.src = "http://deb.debian.org/debian"
        self.pkg.settings.options = {"dist": "bookworm", "user": "someone", "password": "secret"}
        with patch("mirror.toolbox.command_exists", return_value=True), \
                patch("mirror.socket.worker.execute_command") as mock_exec, \
                patch("mirror.sync.on_sync_done"):
            execute(self.pkg, mock_logger)
        mock_exec.assert_called_once()
        self.assertTrue(
            any("auth" in str(call.args[0]).lower() for call in mock_logger.warning.call_args_list)
        )

    def test_execute_warns_when_no_keyring_configured(self):
        """check_gpg on (default) with no keyring configured warns from execute()."""
        mock_logger = MagicMock()
        mock_logger.handlers = []
        self.pkg.settings.options = {"dist": "bookworm"}
        with patch("mirror.toolbox.command_exists", return_value=True), \
                patch("mirror.socket.worker.execute_command") as mock_exec, \
                patch("mirror.sync.on_sync_done"):
            execute(self.pkg, mock_logger)
        mock_exec.assert_called_once()
        self.assertTrue(
            any(
                "keyring" in str(call.args[0]).lower() and "configured" in str(call.args[0]).lower()
                for call in mock_logger.warning.call_args_list
            )
        )


class TestDebmirrorPlugin(unittest.TestCase):
    def test_plugin_returns_correct_record(self):
        record = plugin()
        self.assertEqual(record.name, "debmirror")
        self.assertIs(record.execute, execute)
        self.assertEqual(record.type, "sync")
        self.assertIsNone(record.on_sync_done)


# ---------------------------------------------------------------------------
# build_command — pure unit tests
# ---------------------------------------------------------------------------

def _make_pkg(src="http://deb.debian.org/debian", dst="/srv/mirror/debian",
              options=None, pkgid="debmirror-test"):
    """Build a MagicMock package suitable for build_command() unit tests."""
    pkg = MagicMock()
    pkg.pkgid = pkgid
    pkg.settings = MagicMock()
    pkg.settings.src = src
    pkg.settings.dst = dst
    pkg.settings.options = options if options is not None else {"dist": "bookworm"}
    return pkg


# --- src parsing ---

def test_build_command_http_src_parsing():
    argv, _ = build_command(_make_pkg(src="http://deb.debian.org/debian"))
    assert argv[argv.index("--method") + 1] == "http"
    assert argv[argv.index("--host") + 1] == "deb.debian.org"
    assert argv[argv.index("--root") + 1] == "debian"


def test_build_command_https_src_parsing():
    argv, _ = build_command(_make_pkg(src="https://deb.debian.org/debian"))
    assert argv[argv.index("--method") + 1] == "https"
    assert argv[argv.index("--host") + 1] == "deb.debian.org"


def test_build_command_rsync_src_parsing():
    argv, _ = build_command(_make_pkg(src="rsync://mirror.example.org/debian"))
    assert argv[argv.index("--method") + 1] == "rsync"
    assert argv[argv.index("--host") + 1] == "mirror.example.org"
    assert argv[argv.index("--root") + 1] == "debian"


def test_build_command_file_src_requires_host_override():
    pkg = _make_pkg(src="file:///srv/repo/debian", options={"dist": "bookworm", "host": "localhost"})
    argv, _ = build_command(pkg)
    assert argv[argv.index("--method") + 1] == "file"
    assert argv[argv.index("--host") + 1] == "localhost"
    assert argv[argv.index("--root") + 1] == "srv/repo/debian"


def test_build_command_file_src_without_host_override_raises():
    pkg = _make_pkg(src="file:///srv/repo/debian", options={"dist": "bookworm"})
    with pytest.raises(ValueError):
        build_command(pkg)


def test_build_command_src_option_overrides():
    pkg = _make_pkg(
        src="http://deb.debian.org/debian",
        options={
            "dist": "bookworm",
            "method": "https",
            "host": "mirror.example.org",
            "root": "custom/root",
        },
    )
    argv, _ = build_command(pkg)
    assert argv[argv.index("--method") + 1] == "https"
    assert argv[argv.index("--host") + 1] == "mirror.example.org"
    assert argv[argv.index("--root") + 1] == "custom/root"


# --- source / nosource ---

def test_build_command_default_nosource():
    argv, _ = build_command(_make_pkg())
    assert "--nosource" in argv
    assert "--source" not in argv


def test_build_command_source_opt_in():
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm", "source": True}))
    assert "--source" in argv
    assert "--nosource" not in argv


# --- GPG ---

def test_build_command_gpg_default_with_keyring():
    keyring = "/usr/share/keyrings/debian-archive-keyring.gpg"
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm", "keyring": keyring}))
    assert argv[argv.index("--keyring") + 1] == keyring
    assert "--no-check-gpg" not in argv


def test_build_command_gpg_enabled_emits_check_gpg():
    """GPG on (default) must emit --check-gpg explicitly so a host debmirror.conf
    cannot silently disable verification."""
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm", "keyring": "/k.gpg"}))
    assert "--check-gpg" in argv
    assert "--no-check-gpg" not in argv


def test_build_command_is_pure_no_filesystem_check_on_keyring(caplog):
    """build_command must not touch the filesystem: a nonexistent keyring path is
    still emitted without raising or logging a 'not found' warning (that check
    lives in execute())."""
    caplog.set_level(logging.WARNING, logger="mirror")
    missing = "/nonexistent/path/to/keyring.gpg"
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm", "keyring": missing}))
    assert argv[argv.index("--keyring") + 1] == missing
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)


def test_build_command_gpg_disabled_uses_no_check_gpg():
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm", "check_gpg": False}))
    assert "--no-check-gpg" in argv
    assert "--keyring" not in argv


def test_build_command_gpg_on_without_keyring_does_not_log(caplog):
    """build_command is pure: the 'no keyring configured' warning is emitted by
    execute(), not the builder. The builder still emits --check-gpg."""
    caplog.set_level(logging.WARNING, logger="mirror")
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm"}))
    assert "--check-gpg" in argv
    assert "--keyring" not in argv
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)


def test_build_command_ignore_release_gpg_flag():
    argv, _ = build_command(_make_pkg(options={
        "dist": "bookworm", "keyring": "/k.gpg", "ignore_release_gpg": True,
    }))
    assert "--ignore-release-gpg" in argv


def test_build_command_ignore_missing_release_flag():
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm", "ignore_missing_release": True}))
    assert "--ignore-missing-release" in argv


# --- cleanup ---

def test_build_command_cleanup_default_postcleanup():
    argv, _ = build_command(_make_pkg())
    assert "--postcleanup" in argv


def test_build_command_cleanup_precleanup():
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm", "cleanup": "precleanup"}))
    assert "--precleanup" in argv


def test_build_command_cleanup_nocleanup():
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm", "cleanup": "nocleanup"}))
    assert "--nocleanup" in argv


def test_build_command_cleanup_invalid_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(options={"dist": "bookworm", "cleanup": "bogus"}))


# --- diff / rsync-extra enums ---

def test_build_command_diff_enum():
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm", "diff": "use"}))
    assert argv[argv.index("--diff") + 1] == "use"


def test_build_command_diff_invalid_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(options={"dist": "bookworm", "diff": "bogus"}))


def test_build_command_rsync_extra_enum_list():
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm", "rsync_extra": ["doc", "indices"]}))
    assert argv[argv.index("--rsync-extra") + 1] == "doc,indices"


def test_build_command_rsync_extra_invalid_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(options={"dist": "bookworm", "rsync_extra": "bogus"}))


# --- DI options ---

def test_build_command_di_options():
    argv, _ = build_command(_make_pkg(options={
        "dist": "bookworm", "di_dist": ["bookworm"], "di_arch": "amd64",
    }))
    assert argv[argv.index("--di-dist") + 1] == "bookworm"
    assert argv[argv.index("--di-arch") + 1] == "amd64"


# --- filter regexes ---

def test_build_command_filter_regexes():
    argv, _ = build_command(_make_pkg(options={
        "dist": "bookworm",
        "exclude": ["^extra/"],
        "include": ["^main/"],
        "exclude_deb_section": ["non-free"],
        "limit_priority": ["extra|optional"],
    }))
    assert argv[argv.index("--exclude") + 1] == "^extra/"
    assert argv[argv.index("--include") + 1] == "^main/"
    assert argv[argv.index("--exclude-deb-section") + 1] == "non-free"
    assert argv[argv.index("--limit-priority") + 1] == "extra|optional"


# --- proxy / passive ---

def test_build_command_proxy_and_passive():
    argv, _ = build_command(_make_pkg(options={
        "dist": "bookworm", "proxy": "http://proxy.example.org:3128", "passive": True,
    }))
    assert argv[argv.index("--proxy") + 1] == "http://proxy.example.org:3128"
    assert "--passive" in argv


def test_build_command_proxy_with_whitespace_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(options={"dist": "bookworm", "proxy": "http://a b"}))


# --- auth ---

def test_build_command_ftp_user_passwd():
    argv, env = build_command(_make_pkg(
        src="ftp://ftp.debian.org/debian",
        options={"dist": "bookworm", "user": "anon", "password": "secret"},
    ))
    assert argv[argv.index("--user") + 1] == "anon"
    assert argv[argv.index("--passwd") + 1] == "secret"
    assert "RSYNC_PASSWORD" not in env


def test_build_command_rsync_auth_env_and_host():
    argv, env = build_command(_make_pkg(
        src="rsync://mirror.example.org/debian",
        options={"dist": "bookworm", "user": "syncuser", "password": "syncpass"},
    ))
    assert env["RSYNC_PASSWORD"] == "syncpass"
    assert argv[argv.index("--host") + 1] == "syncuser@mirror.example.org"
    assert "--passwd" not in argv


def test_build_command_rsync_auth_user_with_space_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(
            src="rsync://mirror.example.org/debian",
            options={"dist": "bookworm", "user": "bad user", "password": "x"},
        ))


def test_build_command_ftp_auth_user_with_control_char_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(
            src="ftp://ftp.debian.org/debian",
            options={"dist": "bookworm", "user": "an\non", "password": "x"},
        ))


def test_build_command_http_auth_not_emitted_and_pure(caplog):
    """http/https have no inline basic auth: build_command omits --user/--passwd
    and (being pure) emits no warning; execute() logs the ignored-auth warning."""
    caplog.set_level(logging.WARNING, logger="mirror")
    argv, _ = build_command(_make_pkg(
        src="http://deb.debian.org/debian",
        options={"dist": "bookworm", "user": "someone", "password": "secret"},
    ))
    assert "--user" not in argv
    assert "--passwd" not in argv
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)


# --- rsync-options / timeout / rename / symlinks ---

def test_build_command_rsync_options_passthrough():
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm", "rsync_options": "--bwlimit=1000"}))
    assert argv[argv.index("--rsync-options") + 1] == "--bwlimit=1000"


def test_build_command_timeout_positive_int():
    argv, _ = build_command(_make_pkg(options={"dist": "bookworm", "timeout": 120}))
    assert argv[argv.index("--timeout") + 1] == "120"


def test_build_command_timeout_non_int_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(options={"dist": "bookworm", "timeout": "30"}))


def test_build_command_timeout_non_positive_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(options={"dist": "bookworm", "timeout": 0}))


def test_build_command_allow_dist_rename_and_omit_symlinks():
    argv, _ = build_command(_make_pkg(options={
        "dist": "bookworm", "allow_dist_rename": True, "omit_suite_symlinks": True,
    }))
    assert "--allow-dist-rename" in argv
    assert "--omit-suite-symlinks" in argv


# --- positional dst ---

def test_build_command_dst_is_last_argument():
    argv, _ = build_command(_make_pkg(dst="/srv/mirror/debian"))
    assert argv[-1] == "/srv/mirror/debian"


def test_build_command_relative_dst_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(dst="relative/path"))


# --- validation routing ---

def test_build_command_bad_method_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(options={"dist": "bookworm", "method": "gopher"}))


def test_build_command_control_char_in_dist_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(options={"dist": "book\nworm"}))


def test_build_command_empty_dist_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(options={"dist": ""}))


def test_build_command_missing_dist_raises():
    with pytest.raises(ValueError):
        build_command(_make_pkg(options={}))


# ---------------------------------------------------------------------------
# _redact_command
# ---------------------------------------------------------------------------

def test_redact_command_masks_passwd_value():
    argv = ["debmirror", "--method", "ftp", "--user", "anon", "--passwd", "secret", "/dst"]
    redacted = _redact_command(argv)
    assert "secret" not in redacted
    assert "--passwd ***" in redacted


def test_redact_command_leaves_other_args_untouched():
    argv = ["debmirror", "--dist", "bookworm", "/dst"]
    redacted = _redact_command(argv)
    assert redacted == "debmirror --dist bookworm /dst"


if __name__ == "__main__":
    unittest.main()
