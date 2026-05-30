"""Tests for jigdo plugin registration and CLI command."""

import mirror
import mirror.plugin
import mirror.sync
from mirror.plugin import load_builtin_plugins

import pytest
from click.testing import CliRunner

from mirror.command.worker_execute import worker_execute_group


# ---------------------------------------------------------------------------
# Fixture: ensure clean plugin state for each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_registry():
    """Reset to a clean built-in baseline before each test, then restore after."""
    mirror.plugin._registry.clear()
    mirror.plugin._BUILTIN_NAMES.clear()
    mirror.plugin._status_stat_hooks.clear()
    mirror.plugin._status_web_hooks.clear()
    mirror.sync.methods.clear()
    load_builtin_plugins()

    clean_registry = dict(mirror.plugin._registry)
    clean_methods = list(mirror.sync.methods)
    clean_builtins = set(mirror.plugin._BUILTIN_NAMES)

    yield

    mirror.plugin._registry.clear()
    mirror.plugin._registry.update(clean_registry)
    mirror.sync.methods[:] = clean_methods
    mirror.plugin._BUILTIN_NAMES.clear()
    mirror.plugin._BUILTIN_NAMES.update(clean_builtins)
    mirror.plugin._status_stat_hooks.clear()
    mirror.plugin._status_web_hooks.clear()


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

def test_jigdo_in_sync_methods():
    """jigdo must appear in mirror.sync.methods after builtin plugins load."""
    assert "jigdo" in mirror.sync.methods


def test_jigdo_in_plugin_registry():
    """jigdo must appear in mirror.plugin._registry after builtin plugins load."""
    assert "jigdo" in mirror.plugin._registry


def test_jigdo_in_builtin_names():
    """jigdo must be present in mirror.plugin._BUILTIN_NAMES."""
    assert "jigdo" in mirror.plugin._BUILTIN_NAMES


def test_ubuntu_still_registered():
    """ubuntu must remain registered alongside jigdo."""
    assert "ubuntu" in mirror.sync.methods
    assert "ubuntu" in mirror.plugin._registry


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

def test_jigdo_command_help_exits_zero():
    """worker-execute jigdo --help must exit with code 0."""
    runner = CliRunner()
    result = runner.invoke(worker_execute_group, ["jigdo", "--help"])
    assert result.exit_code == 0


def test_jigdo_command_help_contains_jigdo():
    """worker-execute jigdo --help output must mention 'jigdo'."""
    runner = CliRunner()
    result = runner.invoke(worker_execute_group, ["jigdo", "--help"])
    assert "jigdo" in result.output


def test_jigdo_command_help_contains_jigdo_file_option():
    """worker-execute jigdo --help output must list --jigdo-file option."""
    runner = CliRunner()
    result = runner.invoke(worker_execute_group, ["jigdo", "--help"])
    assert "--jigdo-file" in result.output


def test_jigdo_command_help_contains_debian_mirror_option():
    """worker-execute jigdo --help output must list --debian-mirror option."""
    runner = CliRunner()
    result = runner.invoke(worker_execute_group, ["jigdo", "--help"])
    assert "--debian-mirror" in result.output


def test_jigdo_command_no_args_shows_help():
    """Invoking jigdo with no args must print help (no_args_is_help=True)."""
    runner = CliRunner()
    result = runner.invoke(worker_execute_group, ["jigdo"])
    assert "--jigdo-file" in result.output
    assert "--debian-mirror" in result.output


def test_jigdo_command_dispatches_to_run_standalone(monkeypatch, tmp_path):
    """jigdo CLI must call run_standalone with the provided arguments."""
    captured: dict = {}

    def fake_run_standalone(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("mirror.sync.jigdo.run_standalone", fake_run_standalone)

    runner = CliRunner()
    result = runner.invoke(worker_execute_group, [
        "jigdo",
        "--src", "rsync://cdimage.debian.org/debian-cd/",
        "--dst", str(tmp_path),
        "--jigdo-file", "/usr/bin/jigdo-file",
        "--debian-mirror", "file:/mirror/ftp/debian",
    ])

    assert result.exit_code == 0, result.output
    assert captured, "run_standalone was never called"
    assert captured["src"] == "rsync://cdimage.debian.org/debian-cd/"
    assert captured["jigdo_file"] == "/usr/bin/jigdo-file"
    assert captured["debian_mirror"] == "file:/mirror/ftp/debian"
    assert captured["trace"] is True


def test_jigdo_command_default_excludes_and_includes(monkeypatch, tmp_path):
    """When no --template-exclude / --final-include args are given, module defaults are used."""
    import mirror.sync.jigdo

    captured: dict = {}

    def fake_run_standalone(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("mirror.sync.jigdo.run_standalone", fake_run_standalone)

    runner = CliRunner()
    result = runner.invoke(worker_execute_group, [
        "jigdo",
        "--src", "rsync://host/debian-cd/",
        "--dst", str(tmp_path),
        "--jigdo-file", "/usr/bin/jigdo-file",
        "--debian-mirror", "file:/mirror/ftp/debian",
    ])

    assert result.exit_code == 0, result.output
    assert captured["template_excludes"] == mirror.sync.jigdo.JIGDO_TEMPLATE_EXCLUDES
    assert captured["final_includes"] == mirror.sync.jigdo.JIGDO_FINAL_INCLUDES
