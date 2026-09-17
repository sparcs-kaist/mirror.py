"""Tests for the mirror plugin config create CLI and helper function."""

import copy
import json
from pathlib import Path
from typing import Optional

import pytest
from click.testing import CliRunner

import mirror.plugin
import mirror.sync
from mirror.__main__ import main
from mirror.config.config import DEFAULT_CONFIG
from mirror.plugin import (
    ConfigCreateResult,
    PluginRecord,
    event_plugin,
    load_builtin_plugins,
    status_plugin,
    sync_plugin,
)


# ---------------------------------------------------------------------------
# Fixture: restore built-in registry state around each test
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
# Fixture: dummy plug-in with a working create_config callback
# ---------------------------------------------------------------------------

def _make_dummy_plugin(tmp_path: Path) -> PluginRecord:
    """Build a PluginRecord whose create_config writes a file under tmp_path.

    Args:
        tmp_path(pathlib.Path): Directory where the dummy config file is written.

    Return:
        record(PluginRecord): Validated sync PluginRecord with create_config set.
    """
    config_file = tmp_path / "dummy_cfg.conf"
    # Track the number of actual writes so behaviour (create vs skip vs
    # overwrite) is observable through the file content alone — the command
    # prints via prompt_toolkit, whose output is not reliably captured under
    # click's CliRunner, so tests assert on filesystem state, not stdout.
    state = {"writes": 0}

    def _create_config(force: bool) -> ConfigCreateResult:
        """Write dummy config, honouring force semantics."""
        if config_file.exists() and not force:
            return ConfigCreateResult(path=str(config_file), created=False)
        state["writes"] += 1
        config_file.write_text(f"# dummy plugin config write={state['writes']}\n")
        return ConfigCreateResult(path=str(config_file), created=True)

    def _noop_execute(package, logger):
        """No-op sync execute callable."""

    return sync_plugin(
        name="dummy_cfg",
        execute=_noop_execute,
        create_config=_create_config,
    )


@pytest.fixture()
def dummy_plugin(tmp_path: Path):
    """Register dummy_cfg plug-in, yield the PluginRecord, then clean up.

    Yields:
        record(PluginRecord): The registered dummy PluginRecord.
    """
    record = _make_dummy_plugin(tmp_path)
    mirror.plugin._registry[record.name] = record
    if record.name not in mirror.sync.methods:
        mirror.sync.methods.append(record.name)
    try:
        yield record
    finally:
        mirror.plugin._registry.pop(record.name, None)
        if record.name in mirror.sync.methods:
            mirror.sync.methods.remove(record.name)


# ---------------------------------------------------------------------------
# Helper: build a minimal config.json for CLI tests
# ---------------------------------------------------------------------------

def _write_cfg(tmp_path: Path) -> tuple[Path, str, str]:
    """Write a valid config.json to tmp_path and return key paths.

    Sets statfile and statusfile to paths inside tmp_path that do NOT exist
    yet so we can assert they were never created.

    Args:
        tmp_path(pathlib.Path): Temporary directory for the test.

    Return:
        cfg_path(pathlib.Path): Path to the written config.json.
        statfile(str): Expected path for stat.json (must remain absent).
        statusfile(str): Expected path for status.json (must remain absent).
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    statfile = str(tmp_path / "stat.json")
    statusfile = str(tmp_path / "status.json")
    cfg["settings"]["statfile"] = statfile
    cfg["settings"]["statusfile"] = statusfile
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg))
    return cfg_path, statfile, statusfile


# ---------------------------------------------------------------------------
# 1. Help wiring tests
# ---------------------------------------------------------------------------

def test_plugin_help_contains_config():
    """mirror plugin --help must list the config sub-group."""
    r = CliRunner()
    result = r.invoke(main, ["plugin", "--help"])
    assert result.exit_code == 0
    assert "config" in result.output


def test_plugin_config_help_contains_create():
    """mirror plugin config --help must list the create sub-command."""
    r = CliRunner()
    result = r.invoke(main, ["plugin", "config", "--help"])
    assert result.exit_code == 0
    assert "create" in result.output


def test_plugin_config_create_help_shows_flags():
    """mirror plugin config create --help must list --force, --config, and PLUGIN."""
    r = CliRunner()
    result = r.invoke(main, ["plugin", "config", "create", "--help"])
    assert result.exit_code == 0
    assert "--force" in result.output
    assert "--config" in result.output
    # Click uppercases argument metavars; accept either case.
    assert "PLUGIN" in result.output or "plugin" in result.output


def test_plugin_config_create_no_args_shows_help():
    """Invoking create with no arguments must print help (no_args_is_help)."""
    r = CliRunner()
    result = r.invoke(main, ["plugin", "config", "create"])
    assert "--force" in result.output


# ---------------------------------------------------------------------------
# 2. _run_create_config unit tests
# ---------------------------------------------------------------------------

from mirror.command.plugin import _run_create_config


def test_run_create_config_fresh(dummy_plugin: PluginRecord, tmp_path: Path):
    """First call creates the file and returns created=True."""
    result = _run_create_config("dummy_cfg", force=False)
    assert result.created is True
    assert Path(result.path).exists()


def test_run_create_config_no_overwrite_without_force(
    dummy_plugin: PluginRecord, tmp_path: Path
):
    """Second call without force returns created=False and leaves file unchanged."""
    first = _run_create_config("dummy_cfg", force=False)
    original_content = Path(first.path).read_text()

    second = _run_create_config("dummy_cfg", force=False)
    assert second.created is False
    assert Path(second.path).read_text() == original_content


def test_run_create_config_force_overwrites(dummy_plugin: PluginRecord, tmp_path: Path):
    """Call with force=True after file exists returns created=True."""
    _run_create_config("dummy_cfg", force=False)

    result = _run_create_config("dummy_cfg", force=True)
    assert result.created is True


def test_run_create_config_unknown_plugin_raises_key_error():
    """Unknown plugin name raises KeyError."""
    with pytest.raises(KeyError):
        _run_create_config("no_such_plugin_xyz", force=False)


def test_run_create_config_none_create_config_raises_value_error():
    """Plug-in with create_config=None raises ValueError."""
    def _noop_execute(package, logger):
        """No-op execute callable."""

    null_record = PluginRecord(
        name="null_create_cfg",
        type="sync",
        execute=_noop_execute,
        create_config=None,
    )
    mirror.plugin._registry[null_record.name] = null_record
    mirror.sync.methods.append(null_record.name)
    try:
        with pytest.raises(ValueError):
            _run_create_config("null_create_cfg", force=False)
    finally:
        mirror.plugin._registry.pop(null_record.name, None)
        if null_record.name in mirror.sync.methods:
            mirror.sync.methods.remove(null_record.name)


# ---------------------------------------------------------------------------
# 3. Full CLI + side-effect guard tests
# ---------------------------------------------------------------------------

def test_cli_create_config_success(dummy_plugin: PluginRecord, tmp_path: Path):
    """CLI create succeeds, writes the plugin file, and does NOT create statfile.

    The command prints via prompt_toolkit, whose output is not reliably captured
    under click's CliRunner, so this asserts on exit code and filesystem state
    rather than stdout.
    """
    cfg_path, statfile, _ = _write_cfg(tmp_path)

    r = CliRunner()
    result = r.invoke(main, [
        "plugin", "config", "create", "dummy_cfg",
        "--config", str(cfg_path),
    ])

    assert result.exit_code == 0, result.output
    plugin_cfg_path = tmp_path / "dummy_cfg.conf"
    assert plugin_cfg_path.exists()
    # First write produces the write=1 marker.
    assert "write=1" in plugin_cfg_path.read_text()
    # The critical side-effect guard: stat.json must NOT have been created.
    assert not Path(statfile).exists(), (
        "stat.json was created — mirror.config.load side-effects ran unexpectedly"
    )


def test_cli_create_config_skip_without_force(dummy_plugin: PluginRecord, tmp_path: Path):
    """Second invocation without --force exits 0 and leaves the file unrewritten."""
    cfg_path, _, _ = _write_cfg(tmp_path)
    plugin_cfg_path = tmp_path / "dummy_cfg.conf"
    r = CliRunner()

    # First call creates the file (write=1).
    r.invoke(main, [
        "plugin", "config", "create", "dummy_cfg",
        "--config", str(cfg_path),
    ])
    content_after_first = plugin_cfg_path.read_text()

    # Second call without force must skip: exit 0, file content unchanged.
    result = r.invoke(main, [
        "plugin", "config", "create", "dummy_cfg",
        "--config", str(cfg_path),
    ])
    assert result.exit_code == 0, result.output
    assert plugin_cfg_path.read_text() == content_after_first
    assert "write=1" in content_after_first


def test_cli_create_config_force_overwrites(dummy_plugin: PluginRecord, tmp_path: Path):
    """Invocation with --force after the file exists rewrites it and exits 0."""
    cfg_path, _, _ = _write_cfg(tmp_path)
    plugin_cfg_path = tmp_path / "dummy_cfg.conf"
    r = CliRunner()

    # First call creates the file (write=1).
    r.invoke(main, [
        "plugin", "config", "create", "dummy_cfg",
        "--config", str(cfg_path),
    ])

    # Force overwrite must produce a second write (write=2).
    result = r.invoke(main, [
        "plugin", "config", "create", "dummy_cfg",
        "--config", str(cfg_path),
        "--force",
    ])
    assert result.exit_code == 0, result.output
    assert "write=2" in plugin_cfg_path.read_text()


def test_cli_create_config_unknown_plugin_exits_nonzero(
    dummy_plugin: PluginRecord, tmp_path: Path
):
    """Unknown plugin name causes a handled error exit (code 1, not a crash)."""
    cfg_path, _, _ = _write_cfg(tmp_path)

    r = CliRunner()
    result = r.invoke(main, [
        "plugin", "config", "create", "does_not_exist",
        "--config", str(cfg_path),
    ])

    # The command catches the unknown-plugin KeyError and calls sys.exit(1);
    # a click usage error would instead be exit code 2.
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# 4. Factory validation: non-callable create_config raises TypeError
# ---------------------------------------------------------------------------

def test_sync_plugin_non_callable_create_config_raises_type_error():
    """sync_plugin with a non-callable, non-None create_config must raise TypeError."""
    def _noop_execute(package, logger):
        """No-op execute callable."""

    with pytest.raises(TypeError):
        sync_plugin(name="x_bad_create", execute=_noop_execute, create_config=123)


def test_event_plugin_non_callable_create_config_raises_type_error():
    """event_plugin with a non-callable, non-None create_config must raise TypeError."""
    def _noop_setup() -> None:
        """No-op setup callable."""

    with pytest.raises(TypeError):
        event_plugin(name="x_bad_event_create", setup=_noop_setup, create_config=123)


def test_status_plugin_non_callable_create_config_raises_type_error():
    """status_plugin with a non-callable, non-None create_config must raise TypeError."""
    def _extend(package) -> dict:
        """No-op stat field extender."""
        return {}

    with pytest.raises(TypeError):
        status_plugin(
            name="x_bad_status_create",
            extend_stat_fields=_extend,
            create_config=123,
        )
