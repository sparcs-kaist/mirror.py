"""Tests for mirror.command.setup provisioning logic."""
import json
import types
import importlib
from pathlib import Path

import pytest

import mirror.command.setup
setup_mod = importlib.import_module("mirror.command.setup")


def _redirect_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(setup_mod, "_CONFIG_PATH", tmp_path / "etc/mirror/config.json")
    monkeypatch.setattr(setup_mod, "_SYSTEMD_PATH", tmp_path / "etc/systemd/system")
    (tmp_path / "etc/systemd/system").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(setup_mod, "_DIRECTORIES", [
        tmp_path / "etc/mirror",
        tmp_path / "var/run/mirror",
        tmp_path / "var/lib/mirror",
        tmp_path / "var/log/mirror",
        tmp_path / "var/log/mirror/packages",
        tmp_path / "var/www/mirror",
    ])


def _patch_happy_path(monkeypatch):
    monkeypatch.setattr(setup_mod.os, "geteuid", lambda: 0)
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(setup_mod, "command_exists", lambda b: True)
    monkeypatch.setattr(
        setup_mod.subprocess,
        "run",
        lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(setup_mod, "print_formatted_text", print)


def test_setup_aborts_when_not_root(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(setup_mod, "print_formatted_text", print)
    monkeypatch.setattr(setup_mod.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Linux")

    setup_mod.setup()

    config_path = tmp_path / "etc/mirror/config.json"
    assert not config_path.exists()
    for d in [
        tmp_path / "var/run/mirror",
        tmp_path / "var/lib/mirror",
        tmp_path / "var/log/mirror",
    ]:
        assert not d.exists()


def test_setup_aborts_when_not_linux(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(setup_mod, "print_formatted_text", print)
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(setup_mod.os, "geteuid", lambda: 0)

    setup_mod.setup()

    config_path = tmp_path / "etc/mirror/config.json"
    assert not config_path.exists()
    for d in [
        tmp_path / "var/run/mirror",
        tmp_path / "var/lib/mirror",
    ]:
        assert not d.exists()


@pytest.mark.parametrize("missing", ["rsync", "lftp", "bandersnatch"])
def test_setup_fails_on_missing_required_binary(monkeypatch, tmp_path, capsys, missing):
    _redirect_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(setup_mod, "print_formatted_text", print)
    monkeypatch.setattr(setup_mod.os, "geteuid", lambda: 0)
    monkeypatch.setattr(setup_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        setup_mod,
        "command_exists",
        lambda b: b != missing,
    )

    setup_mod.setup()

    config_path = tmp_path / "etc/mirror/config.json"
    assert not config_path.exists()
    for d in setup_mod._DIRECTORIES:
        assert not d.exists()

    captured = capsys.readouterr()
    assert missing in captured.out
    assert "Setup aborted" in captured.out


def test_setup_warns_on_missing_optional_git(monkeypatch, tmp_path, capsys):
    _redirect_paths(monkeypatch, tmp_path)
    _patch_happy_path(monkeypatch)
    monkeypatch.setattr(setup_mod, "command_exists", lambda b: b != "git")

    setup_mod.setup()

    config_path = tmp_path / "etc/mirror/config.json"
    assert config_path.exists()
    captured = capsys.readouterr()
    assert "git" in captured.out
    assert "bundled fallback" in captured.out


def test_setup_skips_existing_config(monkeypatch, tmp_path, capsys):
    _redirect_paths(monkeypatch, tmp_path)
    _patch_happy_path(monkeypatch)

    config_path = tmp_path / "etc/mirror/config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    sentinel_content = '{"sentinel": true}'
    config_path.write_text(sentinel_content)

    setup_mod.setup()

    assert config_path.read_text() == sentinel_content
    captured = capsys.readouterr()
    assert "skipping config write" in captured.out.lower()


def test_setup_writes_default_config_when_absent(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    _patch_happy_path(monkeypatch)

    setup_mod.setup()

    config_path = tmp_path / "etc/mirror/config.json"
    assert config_path.exists()
    loaded = json.loads(config_path.read_text())
    assert isinstance(loaded, dict)
    assert loaded["packages"] == {}
    assert "plugins" not in loaded["settings"]


def test_setup_creates_all_directories(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    _patch_happy_path(monkeypatch)

    setup_mod.setup()

    for d in setup_mod._DIRECTORIES:
        assert d.exists() and d.is_dir()


def test_setup_invokes_systemctl_daemon_reload(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    _patch_happy_path(monkeypatch)

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("args"))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(setup_mod.subprocess, "run", fake_run)

    setup_mod.setup()

    assert len(calls) == 1
    assert calls[0] == ["systemctl", "daemon-reload"]


def test_setup_warns_when_systemctl_fails(monkeypatch, tmp_path, capsys):
    _redirect_paths(monkeypatch, tmp_path)
    _patch_happy_path(monkeypatch)

    monkeypatch.setattr(
        setup_mod.subprocess,
        "run",
        lambda *a, **kw: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )

    setup_mod.setup()

    captured = capsys.readouterr()
    assert "boom" in captured.out


def test_setup_warns_when_systemctl_binary_missing(monkeypatch, tmp_path, capsys):
    _redirect_paths(monkeypatch, tmp_path)
    _patch_happy_path(monkeypatch)

    def raise_not_found(*a, **kw):
        raise FileNotFoundError(2, "No such file or directory", "systemctl")

    monkeypatch.setattr(setup_mod.subprocess, "run", raise_not_found)

    setup_mod.setup()

    out = capsys.readouterr().out
    assert "'systemctl' not found" in out
    edit_pos = out.find("Edit /etc/mirror/config.json")
    enable_pos = out.find("systemctl enable")
    assert edit_pos != -1 and enable_pos != -1, out
    assert edit_pos < enable_pos
    config_path = tmp_path / "etc/mirror/config.json"
    assert config_path.exists()


def test_default_config_has_no_legacy_plugins_key():
    from mirror.config.config import DEFAULT_CONFIG

    assert "plugins" not in DEFAULT_CONFIG["settings"]
    assert DEFAULT_CONFIG["packages"] == {}
    assert DEFAULT_CONFIG["settings"]["logfolder"] == "/var/log/mirror/ftpsync"


def test_required_directories_are_declared():
    fresh = importlib.reload(importlib.import_module("mirror.command.setup"))
    expected = {
        Path("/etc/mirror"),
        Path("/var/run/mirror"),
        Path("/var/lib/mirror"),
        Path("/var/log/mirror"),
        Path("/var/log/mirror/packages"),
        Path("/var/www/mirror"),
    }
    assert set(fresh._DIRECTORIES) == expected


def test_required_binaries_are_declared():
    fresh = importlib.reload(importlib.import_module("mirror.command.setup"))
    assert set(fresh._REQUIRED_BINARIES) == {"rsync", "lftp", "bandersnatch"}


def test_next_steps_edit_config_before_enable_when_fresh(monkeypatch, tmp_path, capsys):
    _redirect_paths(monkeypatch, tmp_path)
    _patch_happy_path(monkeypatch)

    setup_mod.setup()

    out = capsys.readouterr().out
    edit_pos = out.find("Edit /etc/mirror/config.json")
    enable_pos = out.find("systemctl enable")
    assert edit_pos != -1, out
    assert enable_pos != -1, out
    assert edit_pos < enable_pos, "edit-config guidance must precede enable/start"
