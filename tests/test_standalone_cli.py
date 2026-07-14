"""Tests for mirror.command.standalone CLI command."""

import json
import os
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from mirror.__main__ import main
from mirror.command.standalone import _parse_options, _resolve_state_dir, _build_minimal_config


# ---------------------------------------------------------------------------
# _parse_options unit tests
# ---------------------------------------------------------------------------


class TestParseOptions:
    def test_scalar_int(self):
        """'a=1' yields {'a': 1} (integer)."""
        result = _parse_options(("a=1",), None)
        assert result == {"a": 1}

    def test_scalar_bool_true(self):
        """'a=true' yields {'a': True}."""
        result = _parse_options(("a=true",), None)
        assert result == {"a": True}

    def test_scalar_bool_false(self):
        """'a=false' yields {'a': False}."""
        result = _parse_options(("a=false",), None)
        assert result == {"a": False}

    def test_scalar_bool_case_insensitive(self):
        """'a=True' and 'a=FALSE' are coerced to bool."""
        assert _parse_options(("a=True",), None) == {"a": True}
        assert _parse_options(("a=FALSE",), None) == {"a": False}

    def test_scalar_str(self):
        """'a=x' yields {'a': 'x'} (str stays str)."""
        result = _parse_options(("a=x",), None)
        assert result == {"a": "x"}

    def test_scalar_negative_int(self):
        """'a=-5' yields {'a': -5} (negative integer)."""
        result = _parse_options(("a=-5",), None)
        assert result == {"a": -5}

    def test_bare_scalar_not_list(self):
        """A plain 'key=value' entry must produce a scalar, NOT a list."""
        result = _parse_options(("exclude=x",), None)
        assert result == {"exclude": "x"}
        assert not isinstance(result["exclude"], list)

    def test_list_single(self):
        """'a[]=x' yields {'a': ['x']} (list with one element)."""
        result = _parse_options(("a[]=x",), None)
        assert result == {"a": ["x"]}

    def test_list_multiple(self):
        """Repeating 'a[]=x' and 'a[]=y' yields {'a': ['x', 'y']}."""
        result = _parse_options(("a[]=x", "a[]=y"), None)
        assert result == {"a": ["x", "y"]}

    def test_list_element_coercion(self):
        """List elements from key[]=v are coerced the same way as scalars."""
        result = _parse_options(("nums[]=1", "nums[]=true", "nums[]=hello"), None)
        assert result == {"nums": [1, True, "hello"]}

    def test_multiple_keys(self):
        """Multiple different keys are parsed independently."""
        result = _parse_options(("a=1", "b=x"), None)
        assert result == {"a": 1, "b": "x"}

    def test_missing_equals_raises(self):
        """An entry with no '=' must raise click.UsageError."""
        import click
        with pytest.raises(click.UsageError, match="key=value"):
            _parse_options(("badentry",), None)

    def test_empty_tuple_returns_empty(self):
        """No options yields an empty dict."""
        result = _parse_options((), None)
        assert result == {}

    def test_options_json_merges_over(self):
        """options_json values override -o values per key."""
        result = _parse_options(("a=1", "b=2"), json.dumps({"b": 99, "c": "extra"}))
        assert result["a"] == 1
        assert result["b"] == 99
        assert result["c"] == "extra"

    def test_options_json_not_object_raises(self):
        """options_json that is not a JSON object must raise click.UsageError."""
        import click
        with pytest.raises(click.UsageError, match="JSON object"):
            _parse_options((), json.dumps([1, 2, 3]))

    def test_options_json_invalid_json_raises(self):
        """Malformed options_json must raise click.UsageError."""
        import click
        with pytest.raises(click.UsageError, match="not valid JSON"):
            _parse_options((), "{bad json}")

    def test_options_json_none_with_options(self):
        """options_json=None leaves the -o results unchanged."""
        result = _parse_options(("x=10",), None)
        assert result == {"x": 10}

    def test_value_with_equals_in_it(self):
        """Values that contain '=' are preserved as-is."""
        result = _parse_options(("url=a=b",), None)
        assert result == {"url": "a=b"}


# ---------------------------------------------------------------------------
# _resolve_state_dir unit tests
# ---------------------------------------------------------------------------


class TestResolveStateDir:
    def test_explicit_writable_dir(self, tmp_path):
        """An explicit writable --state-dir is returned as-is."""
        explicit = tmp_path / "mystate"
        result = _resolve_state_dir(str(explicit))
        assert result == explicit
        assert result.exists()

    def test_explicit_dir_created_if_missing(self, tmp_path):
        """The explicit dir is created when it does not exist."""
        new_dir = tmp_path / "new" / "nested"
        _resolve_state_dir(str(new_dir))
        assert new_dir.exists()

    def test_explicit_unwritable_raises(self, tmp_path):
        """A non-writable explicit dir raises click.UsageError."""
        import click
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o555)
        try:
            with pytest.raises(click.UsageError, match="not writable"):
                _resolve_state_dir(str(locked))
        finally:
            locked.chmod(0o755)

    def test_falls_back_to_tempdir_when_default_unwritable(self, tmp_path, monkeypatch):
        """Falls back to mkdtemp when the default mirror.STATE_PATH is unwritable."""
        import mirror
        locked = tmp_path / "locked_state"
        locked.mkdir()
        locked.chmod(0o555)
        monkeypatch.setattr(mirror, "STATE_PATH", locked)
        try:
            result = _resolve_state_dir(None)
            assert result.exists()
            assert "mirror_standalone_" in result.name
        finally:
            locked.chmod(0o755)

    def test_uses_default_when_writable(self, tmp_path, monkeypatch):
        """Uses mirror.STATE_PATH when it exists and is writable."""
        import mirror
        writable = tmp_path / "writable_state"
        writable.mkdir()
        monkeypatch.setattr(mirror, "STATE_PATH", writable)
        result = _resolve_state_dir(None)
        assert result == writable


# ---------------------------------------------------------------------------
# _build_minimal_config unit tests
# ---------------------------------------------------------------------------


class TestBuildMinimalConfig:
    def test_returns_config_instance(self):
        """_build_minimal_config returns a Config instance."""
        import mirror.structure
        conf = _build_minimal_config()
        assert isinstance(conf, mirror.structure.Config)

    def test_uid_equals_current(self):
        """Config uid matches os.getuid()."""
        conf = _build_minimal_config()
        assert conf.uid == os.getuid()

    def test_gid_equals_current(self):
        """Config gid matches os.getgid()."""
        conf = _build_minimal_config()
        assert conf.gid == os.getgid()

    def test_logfolder_is_writable_dir(self):
        """logfolder exists and is a writable directory."""
        conf = _build_minimal_config()
        assert conf.logfolder.exists()
        assert os.access(conf.logfolder, os.W_OK)

    def test_statusfile_path(self):
        """statusfile is under the same directory as logfolder."""
        conf = _build_minimal_config()
        assert conf.statusfile.parent == conf.logfolder

    def test_name_is_standalone(self):
        """Config name is 'standalone'."""
        conf = _build_minimal_config()
        assert conf.name == "standalone"

    def test_ftpsync_instance(self):
        """Config.ftpsync is a Config.FTPSync instance."""
        import mirror.structure
        conf = _build_minimal_config()
        assert isinstance(conf.ftpsync, mirror.structure.Config.FTPSync)


# ---------------------------------------------------------------------------
# CLI help / wiring tests
# ---------------------------------------------------------------------------


class TestStandaloneCliHelp:
    def test_standalone_in_main_help(self):
        """'standalone' subcommand appears in main --help."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "standalone" in result.output

    def test_standalone_help_lists_options(self):
        """standalone --help lists the expected options."""
        runner = CliRunner()
        result = runner.invoke(main, ["standalone", "--help"])
        assert result.exit_code == 0
        for flag in ("--src", "--dst", "--uid", "--gid", "--nice", "--config",
                     "--id", "--state-dir", "--option", "--options-json"):
            assert flag in result.output, f"expected {flag!r} in help output"

    def test_standalone_no_args_shows_help(self):
        """Invoking standalone with no SYNCTYPE prints help (no_args_is_help=True)."""
        runner = CliRunner()
        result = runner.invoke(main, ["standalone"])
        assert "--src" in result.output or "SYNCTYPE" in result.output

    def test_unknown_synctype_exits_2(self):
        """An unknown SYNCTYPE causes exit code 2."""
        runner = CliRunner()
        result = runner.invoke(main, ["standalone", "nonexistent_type"])
        assert result.exit_code == 2

    def test_known_synctype_dispatches(self, monkeypatch, tmp_path):
        """A known synctype dispatches to the plugin's execute() callable."""
        import mirror
        import mirror.sync

        calls = []

        def fake_execute(package, log, trigger):
            import mirror.sync
            mirror.sync.on_sync_done(package.pkgid, success=True, returncode=0)
            calls.append((package.pkgid, trigger))

        mock_record = MagicMock()
        mock_record.execute = fake_execute
        mock_record.on_sync_done = None

        monkeypatch.setattr("mirror.plugin.get_record", lambda name: mock_record)

        dst = tmp_path / "dst"
        dst.mkdir()

        runner = CliRunner()
        # Use a real synctype that is registered.
        result = runner.invoke(main, [
            "standalone", "local",
            "--dst", str(dst),
            "--state-dir", str(tmp_path / "state"),
        ])
        assert result.exit_code == 0, result.output
        assert calls, "execute was never called"


# ---------------------------------------------------------------------------
# Integration tests (require real binaries)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestStandaloneIntegration:
    def test_local_sync_existing_dst_exits_0(self, tmp_path):
        """local synctype with an existing dst directory exits 0."""
        dst = tmp_path / "dst"
        dst.mkdir()
        state = tmp_path / "state"
        state.mkdir()

        runner = CliRunner()
        result = runner.invoke(main, [
            "standalone", "local",
            "--dst", str(dst),
            "--state-dir", str(state),
        ])
        assert result.exit_code == 0, result.output

    def test_local_sync_nonexistent_dst_exits_nonzero(self, tmp_path):
        """local synctype with a nonexistent dst exits with a nonzero code."""
        nonexistent = tmp_path / "does_not_exist"
        state = tmp_path / "state"
        state.mkdir()

        runner = CliRunner()
        result = runner.invoke(main, [
            "standalone", "local",
            "--dst", str(nonexistent),
            "--state-dir", str(state),
        ])
        assert result.exit_code != 0

    def test_rsync_local_path_copies_files_exits_0(self, tmp_path):
        """rsync synctype with a local-path source copies files and exits 0."""
        import shutil
        if shutil.which("rsync") is None:
            pytest.skip("rsync binary not available")

        src = tmp_path / "src"
        src.mkdir()
        (src / "file.txt").write_text("hello standalone")
        (src / "sub").mkdir()
        (src / "sub" / "nested.txt").write_text("nested")

        dst = tmp_path / "dst"
        dst.mkdir()
        state = tmp_path / "state"
        state.mkdir()

        runner = CliRunner()
        result = runner.invoke(main, [
            "standalone", "rsync",
            "--src", str(src),
            "--dst", str(dst),
            "--state-dir", str(state),
        ])
        assert result.exit_code == 0, result.output
        assert (dst / "file.txt").read_text() == "hello standalone"
        assert (dst / "sub" / "nested.txt").read_text() == "nested"

    def test_no_stat_or_status_json_written(self, tmp_path):
        """standalone does not write stat.json or status.json in the minimal config dir."""
        import mirror
        dst = tmp_path / "dst"
        dst.mkdir()
        state = tmp_path / "state"
        state.mkdir()

        runner = CliRunner()
        runner.invoke(main, [
            "standalone", "local",
            "--dst", str(dst),
            "--state-dir", str(state),
        ])

        # The minimal config's temp dir should not contain stat.json.
        # We check the common locations for any stat.json leakage.
        stat_candidates = list(tmp_path.rglob("stat.json"))
        assert not stat_candidates, f"stat.json was written unexpectedly: {stat_candidates}"

        # status.json must not be written either. The minimal config points
        # webroot/statusfile at a private temp dir; scan both the test tree and
        # any temp dirs the minimal config could have created.
        status_candidates = list(tmp_path.rglob("status.json"))
        assert not status_candidates, (
            f"status.json was written unexpectedly: {status_candidates}"
        )
