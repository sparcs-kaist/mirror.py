"""Tests for mirror.command.worker_execute CLI group."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from mirror.__main__ import main


def test_cli_help_shows_ubuntu():
    """worker-execute --help must list the ubuntu subcommand."""
    r = CliRunner()
    result = r.invoke(main, ["worker-execute", "--help"])
    assert result.exit_code == 0
    assert "ubuntu" in result.output


def test_cli_ubuntu_help_lists_options():
    """worker-execute ubuntu --help must list all expected options."""
    r = CliRunner()
    result = r.invoke(main, ["worker-execute", "ubuntu", "--help"])
    assert result.exit_code == 0
    for option in (
        "--src",
        "--dst",
        "--trace",
        "--no-trace",
        "--trace-path",
        "--trace-hostname",
        "--extra-rsync-arg",
        "--rsync-bin",
    ):
        assert option in result.output, f"Expected '{option}' in help output"


def test_cli_ubuntu_shows_help_when_no_args():
    """Invoking the subcommand with no arguments must print full usage/help
    instead of a terse 'missing option' error, so the user immediately sees
    which flags are available."""
    r = CliRunner()
    result = r.invoke(main, ["worker-execute", "ubuntu"])
    # All option flags must appear in the help output.
    for flag in (
        "--src",
        "--dst",
        "--trace",
        "--no-trace",
        "--trace-path",
        "--trace-hostname",
        "--extra-rsync-arg",
        "--rsync-bin",
    ):
        assert flag in result.output, f"expected {flag} in help output, got: {result.output}"


def test_cli_ubuntu_requires_src():
    """Omitting --src must trigger a click usage error."""
    r = CliRunner()
    result = r.invoke(main, ["worker-execute", "ubuntu", "--dst", "/tmp/x"])
    assert result.exit_code != 0
    assert "--src" in result.output


def test_cli_ubuntu_requires_dst():
    """Omitting --dst must trigger a click usage error."""
    r = CliRunner()
    result = r.invoke(main, ["worker-execute", "ubuntu", "--src", "rsync://host/u"])
    assert result.exit_code != 0
    assert "--dst" in result.output


def test_cli_ubuntu_dispatches_to_run_standalone(monkeypatch):
    """CLI must call run_standalone with exactly the provided arguments."""
    captured: dict = {}

    def fake(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("mirror.sync.ubuntu.run_standalone", fake)

    r = CliRunner()
    result = r.invoke(main, [
        "worker-execute", "ubuntu",
        "--src", "rsync://host/u",
        "--dst", "/tmp/x",
        "--no-trace",
        "--rsync-bin", "/opt/rsync",
        "--extra-rsync-arg", "--bw=10",
        "--extra-rsync-arg", "--stats",
    ])

    assert result.exit_code == 0, result.output
    assert captured, "run_standalone was never called"
    assert captured["src"] == "rsync://host/u"
    assert captured["dst"] == Path("/tmp/x")
    assert captured["trace"] is False
    assert captured["rsync_bin"] == "/opt/rsync"
    assert captured["extra_rsync_args"] == ("--bw=10", "--stats")


def test_cli_ubuntu_default_trace_on(monkeypatch):
    """When --no-trace is not passed, trace must default to True."""
    captured: dict = {}

    def fake(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("mirror.sync.ubuntu.run_standalone", fake)

    r = CliRunner()
    result = r.invoke(main, [
        "worker-execute", "ubuntu",
        "--src", "rsync://host/u",
        "--dst", "/tmp/x",
    ])

    assert result.exit_code == 0, result.output
    assert captured["trace"] is True


def test_cli_ubuntu_passes_trace_hostname(monkeypatch):
    """--trace-hostname must be forwarded verbatim to run_standalone."""
    captured: dict = {}

    def fake(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("mirror.sync.ubuntu.run_standalone", fake)

    r = CliRunner()
    result = r.invoke(main, [
        "worker-execute", "ubuntu",
        "--src", "rsync://host/u",
        "--dst", "/tmp/x",
        "--trace-hostname", "my.example.org",
    ])

    assert result.exit_code == 0, result.output
    assert captured["trace_hostname"] == "my.example.org"
