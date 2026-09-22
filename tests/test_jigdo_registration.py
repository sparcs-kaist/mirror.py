"""Tests for jigdo plugin registration and CLI command."""

import mirror
import mirror.plugin
import mirror.sync

import pytest
from click.testing import CliRunner

from mirror.command.worker_execute import worker_execute_group


pytestmark = pytest.mark.usefixtures("clean_plugin_registry")


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


def test_jigdo_command_help_contains_image_filter_options():
    """worker-execute jigdo --help must list both image filter options."""
    runner = CliRunner()
    result = runner.invoke(worker_execute_group, ["jigdo", "--help"])
    assert "--jigdo-include" in result.output
    assert "--jigdo-exclude" in result.output


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
        "--jigdo-include", ".*amd64-DVD-[1-3]\\.iso.*",
        "--jigdo-exclude", ".*kfreebsd.*",
    ])

    assert result.exit_code == 0, result.output
    assert captured, "run_standalone was never called"
    assert captured["src"] == "rsync://cdimage.debian.org/debian-cd/"
    assert captured["jigdo_file"] == "/usr/bin/jigdo-file"
    assert captured["debian_mirror"] == "file:/mirror/ftp/debian"
    assert captured["jigdo_include"] == ".*amd64-DVD-[1-3]\\.iso.*"
    assert captured["jigdo_exclude"] == ".*kfreebsd.*"
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
    assert captured["jigdo_include"] == mirror.sync.jigdo.JIGDO_INCLUDE_DEFAULT
    assert captured["jigdo_exclude"] == mirror.sync.jigdo.JIGDO_EXCLUDE_DEFAULT


@pytest.mark.parametrize("option", ["--jigdo-include", "--jigdo-exclude"])
def test_jigdo_command_rejects_repeated_image_filter(
    option,
    monkeypatch,
    tmp_path,
):
    """Each image filter option may be specified at most once."""
    called = False

    def fake_run_standalone(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("mirror.sync.jigdo.run_standalone", fake_run_standalone)

    runner = CliRunner()
    result = runner.invoke(worker_execute_group, [
        "jigdo",
        "--src", "rsync://host/debian-cd/",
        "--dst", str(tmp_path),
        "--jigdo-file", "/usr/bin/jigdo-file",
        "--debian-mirror", "file:/mirror/ftp/debian",
        option, ".*DVD-1\\.iso.*",
        option, ".*DVD-2\\.iso.*",
    ])

    assert result.exit_code == 2
    assert f"{option} may only be specified once" in result.output
    assert called is False
