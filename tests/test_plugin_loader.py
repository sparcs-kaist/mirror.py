"""Unit tests for the mirror.plugin two-phase loader."""
import logging
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.plugin
import mirror.sync
from mirror.plugin import (
    PluginRecord,
    _register_event,
    _register_sync,
    event_plugin,
    get_config,
    get_record,
    load_builtin_plugins,
    load_external_plugins,
    sync_plugin,
)
from mirror.structure import PluginSettings


# ---------------------------------------------------------------------------
# Fixture: ensure clean built-in state before each test and restore after
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_registry():
    """Reset to a clean built-in baseline before each test, then restore after.

    test_example_config.py replaces mirror.sync.methods with a new list object
    (line: mirror.sync.methods = ['local', 'ftpsync', 'rsync']), which means
    any snapshot taken after that module runs would be incomplete. To guarantee
    isolation, we always rebuild a clean baseline here.
    """
    # Build a known-good baseline regardless of earlier test pollution
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
# Built-in load
# ---------------------------------------------------------------------------

def test_all_five_builtins_registered():
    """Phase A must populate all five built-in sync types."""
    expected = {"rsync", "ftpsync", "lftp", "bandersnatch", "local"}
    assert expected == set(mirror.sync.methods)
    for name in expected:
        assert name in mirror.plugin._registry, f"{name} missing from registry"


def test_builtin_names_set_populated():
    """_BUILTIN_NAMES must contain all five canonical names after phase A."""
    expected = {"rsync", "ftpsync", "lftp", "bandersnatch", "local"}
    assert expected.issubset(mirror.plugin._BUILTIN_NAMES)


# ---------------------------------------------------------------------------
# local synctype execute()
# ---------------------------------------------------------------------------

def test_local_execute_success(tmp_path, monkeypatch):
    """local.execute() calls on_sync_done(success=True) when dst exists."""
    from mirror.sync import local

    pkg = MagicMock()
    pkg.name = "test-local"
    pkg.pkgid = "test-local"
    pkg.settings.dst = str(tmp_path)

    pkg_logger = logging.getLogger("test.local.success")

    on_done = MagicMock()
    monkeypatch.setattr(mirror.sync, "on_sync_done", on_done)

    local.execute(pkg, pkg_logger)

    on_done.assert_called_once_with("test-local", success=True, returncode=0)


def test_local_execute_missing_dst(tmp_path, monkeypatch):
    """local.execute() calls on_sync_done(success=False) when dst is absent."""
    from mirror.sync import local

    nonexistent = tmp_path / "does_not_exist"

    pkg = MagicMock()
    pkg.name = "test-local-missing"
    pkg.pkgid = "test-local-missing"
    pkg.settings.dst = str(nonexistent)

    pkg_logger = logging.getLogger("test.local.missing")

    on_done = MagicMock()
    monkeypatch.setattr(mirror.sync, "on_sync_done", on_done)

    local.execute(pkg, pkg_logger)

    on_done.assert_called_once_with("test-local-missing", success=False, returncode=None)


# ---------------------------------------------------------------------------
# External plug-ins
# ---------------------------------------------------------------------------

def test_load_external_plugins_empty_dict_does_not_crash():
    """load_external_plugins({}) with no externals in env must not raise."""
    load_external_plugins({})


def test_disable_builtin_via_config():
    """Setting enabled=False in config removes the built-in from registry and methods."""
    assert "rsync" in mirror.plugin._registry
    assert "rsync" in mirror.sync.methods

    settings = {"rsync": PluginSettings(enabled=False)}
    load_external_plugins(settings)

    assert "rsync" not in mirror.plugin._registry
    assert "rsync" not in mirror.sync.methods


def test_disabled_builtin_reloads_after_load_builtin_plugins():
    """After disabling rsync and clearing the registry, load_builtin_plugins restores it."""
    settings = {"rsync": PluginSettings(enabled=False)}
    load_external_plugins(settings)

    assert "rsync" not in mirror.plugin._registry

    # Clear everything so we can re-load from scratch without duplicate errors
    mirror.plugin._registry.clear()
    mirror.plugin._BUILTIN_NAMES.clear()
    mirror.sync.methods.clear()

    load_builtin_plugins()
    assert "rsync" in mirror.plugin._registry
    assert "rsync" in mirror.sync.methods


# ---------------------------------------------------------------------------
# Duplicate name raises ValueError
# ---------------------------------------------------------------------------

def test_duplicate_sync_name_raises():
    """Registering a second sync plug-in with the same name must raise ValueError."""
    record = sync_plugin(name="rsync", execute=lambda pkg, log: None)
    with pytest.raises(ValueError, match="already registered"):
        _register_sync(record)


def test_duplicate_name_across_types_raises():
    """A sync name colliding with an event name must raise ValueError."""
    def fake_setup():
        pass

    # rsync is already registered as sync; trying to register it as event raises
    event_rec = event_plugin(name="rsync", setup=fake_setup)
    with pytest.raises(ValueError, match="already registered"):
        _register_event(event_rec)


# ---------------------------------------------------------------------------
# get_config
# ---------------------------------------------------------------------------

def test_get_config_returns_empty_dict_for_registered_no_config():
    """get_config for a registered plug-in with no config entry returns {}."""
    result = get_config("rsync")
    assert result == {}


def test_get_config_raises_for_unknown():
    """get_config for an unregistered plug-in raises KeyError."""
    with pytest.raises(KeyError):
        get_config("no-such-plugin-xyz")


# ---------------------------------------------------------------------------
# External entry-point discovery (phase B)
# ---------------------------------------------------------------------------

def _make_fake_entry_point(name: str, group: str, plugin_callable):
    """Build a fake importlib.metadata.EntryPoint for tests."""
    ep = MagicMock()
    ep.name = name
    ep.group = group
    ep.load = MagicMock(return_value=plugin_callable)
    return ep


def test_external_sync_plugin_registers_via_entry_point():
    """A non-built-in entry point in mirror.sync group registers and joins methods."""
    captured = {}

    def fake_execute(package, logger):
        captured["called"] = True

    def fake_factory():
        return sync_plugin(name="myproto", execute=fake_execute)

    ep = _make_fake_entry_point("myproto", "mirror.sync", fake_factory)

    def fake_entry_points(*, group):
        return [ep] if group == "mirror.sync" else []

    with patch("mirror.plugin.importlib.metadata.entry_points", fake_entry_points):
        load_external_plugins({})

    assert "myproto" in mirror.plugin._registry
    assert "myproto" in mirror.sync.methods
    record = get_record("myproto")
    assert record is not None and record.execute is fake_execute


def test_external_plugin_disabled_via_config_is_skipped():
    """An external plug-in with enabled=False must not even be loaded (no ep.load call)."""
    fake_factory = MagicMock()
    ep = _make_fake_entry_point("disabled-ext", "mirror.event", fake_factory)

    def fake_entry_points(*, group):
        return [ep] if group == "mirror.event" else []

    settings = {"disabled-ext": PluginSettings(enabled=False)}

    with patch("mirror.plugin.importlib.metadata.entry_points", fake_entry_points):
        load_external_plugins(settings)

    ep.load.assert_not_called()
    assert "disabled-ext" not in mirror.plugin._registry


def test_legacy_list_plugins_config_logs_and_returns_empty(monkeypatch):
    """A list-shaped plugins value triggers deprecation warning and yields {}."""
    from mirror.structure import Config

    captured = []
    fake_log = MagicMock()
    fake_log.warning = lambda msg, *a, **kw: captured.append(msg if not a else msg % a)
    monkeypatch.setattr(mirror, "log", fake_log, raising=False)

    parsed = Config._parse_plugins(["/some/legacy/path.py", "/another.py"])
    assert parsed == {}
    assert any("Legacy 'plugins' list-of-strings shape" in msg for msg in captured), \
        f"deprecation warning not emitted; captured: {captured}"
