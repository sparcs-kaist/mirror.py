"""Unit tests for the mirror.plugin two-phase loader."""
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.config
import mirror.plugin
import mirror.sync
from mirror.plugin import (
    PLUGIN_API_VERSION,
    PluginRecord,
    _register_event,
    _register_sync,
    event_plugin,
    get_config,
    get_record,
    load_builtin_plugins,
    load_external_plugins,
    status_plugin,
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
    """Phase A must populate all eight built-in sync types."""
    expected = {"rsync", "ftpsync", "lftp", "bandersnatch", "local", "ubuntu", "jigdo", "apt-mirror"}
    assert expected == set(mirror.sync.methods)
    for name in expected:
        assert name in mirror.plugin._registry, f"{name} missing from registry"


def test_builtin_names_set_populated():
    """_BUILTIN_NAMES must contain all eight canonical names after phase A."""
    expected = {"rsync", "ftpsync", "lftp", "bandersnatch", "local", "ubuntu", "jigdo", "apt-mirror"}
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


# ---------------------------------------------------------------------------
# get_config — file-based config tests
# ---------------------------------------------------------------------------

def _setup_config_path(tmp_path: Path, monkeypatch) -> Path:
    """Write a placeholder config.json and point mirror.config.CONFIG_PATH at it."""
    config_file = tmp_path / "config.json"
    config_file.write_text("{}")
    monkeypatch.setattr(mirror.config, "CONFIG_PATH", config_file, raising=False)
    return config_file


def test_get_config_reads_plugin_json_file(tmp_path, monkeypatch):
    """get_config reads <config_dir>/<name>.json and returns its contents."""
    _setup_config_path(tmp_path, monkeypatch)
    (tmp_path / "rsync.json").write_text(json.dumps({"key": "value"}))

    result = get_config("rsync")

    assert result == {"key": "value"}


def test_get_config_returns_empty_dict_when_file_absent(tmp_path, monkeypatch):
    """get_config returns {} when no per-plugin JSON file exists."""
    _setup_config_path(tmp_path, monkeypatch)
    # No rsync.json written — file absent.

    result = get_config("rsync")

    assert result == {}


def test_get_config_returns_empty_dict_on_malformed_json(tmp_path, monkeypatch, caplog):
    """get_config returns {} and warns when the file contains malformed JSON."""
    _setup_config_path(tmp_path, monkeypatch)
    (tmp_path / "rsync.json").write_text("{not valid json")

    with caplog.at_level(logging.WARNING, logger="mirror"):
        result = get_config("rsync")

    assert result == {}
    assert any("rsync" in r.message for r in caplog.records)


def test_get_config_returns_empty_dict_on_non_dict_json(tmp_path, monkeypatch, caplog):
    """get_config returns {} and warns when file contains a JSON array or scalar."""
    _setup_config_path(tmp_path, monkeypatch)
    (tmp_path / "rsync.json").write_text(json.dumps(["not", "a", "dict"]))

    with caplog.at_level(logging.WARNING, logger="mirror"):
        result = get_config("rsync")

    assert result == {}
    assert any("rsync" in r.message for r in caplog.records)


def test_get_config_honors_config_filename_override(tmp_path, monkeypatch):
    """get_config uses PluginRecord.config_filename when set."""
    from mirror.plugin import _register_sync, _unregister

    _setup_config_path(tmp_path, monkeypatch)
    (tmp_path / "custom-rsync-cfg.json").write_text(json.dumps({"custom": True}))

    # Temporarily override the rsync record's config_filename.
    original_record = mirror.plugin._registry["rsync"]
    import dataclasses
    overridden = dataclasses.replace(original_record, config_filename="custom-rsync-cfg.json")
    mirror.plugin._registry["rsync"] = overridden

    try:
        result = get_config("rsync")
    finally:
        mirror.plugin._registry["rsync"] = original_record

    assert result == {"custom": True}


def test_get_config_returns_empty_on_traversal_attempt(tmp_path, monkeypatch, caplog):
    """get_config returns {} and warns when config_filename attempts path traversal."""
    from mirror.plugin import _register_sync, _unregister

    _setup_config_path(tmp_path, monkeypatch)

    original_record = mirror.plugin._registry["rsync"]
    import dataclasses
    traversal_record = dataclasses.replace(original_record, config_filename="../x.json")
    mirror.plugin._registry["rsync"] = traversal_record

    try:
        with caplog.at_level(logging.WARNING, logger="mirror"):
            result = get_config("rsync")
    finally:
        mirror.plugin._registry["rsync"] = original_record

    assert result == {}
    assert any("unsafe" in r.message for r in caplog.records)


def test_get_config_returns_empty_on_absolute_path_config_filename(tmp_path, monkeypatch, caplog):
    """get_config returns {} and warns when config_filename is an absolute path."""
    _setup_config_path(tmp_path, monkeypatch)

    original_record = mirror.plugin._registry["rsync"]
    import dataclasses
    abs_record = dataclasses.replace(original_record, config_filename="/etc/passwd")
    mirror.plugin._registry["rsync"] = abs_record

    try:
        with caplog.at_level(logging.WARNING, logger="mirror"):
            result = get_config("rsync")
    finally:
        mirror.plugin._registry["rsync"] = original_record

    assert result == {}
    assert any("unsafe" in r.message for r in caplog.records)


def test_plugin_absent_from_config_plugins_map_stays_enabled(monkeypatch):
    """A plug-in not mentioned in config plugins stays enabled after load_external_plugins."""
    assert "rsync" in mirror.plugin._registry

    # Pass an empty plugins map — rsync is absent, so it must remain enabled.
    load_external_plugins({})

    assert "rsync" in mirror.plugin._registry
    assert "rsync" in mirror.sync.methods


# ---------------------------------------------------------------------------
# _serialize_current_plugin_settings — enable-only output
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# api_version: factory stores normalized value on PluginRecord
# ---------------------------------------------------------------------------

def test_sync_plugin_stores_api_version_tuple():
    """sync_plugin stores the normalized api_version on the returned PluginRecord."""
    record = sync_plugin(name="test-sync-ver", execute=lambda pkg, log: None, api_version=(1, 0))
    assert record.api_version == PLUGIN_API_VERSION


def test_event_plugin_stores_api_version_tuple():
    """event_plugin stores the normalized api_version on the returned PluginRecord."""
    record = event_plugin(name="test-event-ver", setup=lambda: None, api_version=(1, 0))
    assert record.api_version == PLUGIN_API_VERSION


def test_status_plugin_stores_api_version_tuple():
    """status_plugin stores the normalized api_version on the returned PluginRecord."""
    record = status_plugin(
        name="test-status-ver",
        extend_stat_fields=lambda pkg: {},
        api_version=(1, 0),
    )
    assert record.api_version == PLUGIN_API_VERSION


def test_factory_normalizes_list_to_tuple():
    """A list [1, 0] passed as api_version is normalized to the tuple (1, 0)."""
    record = sync_plugin(name="test-list-ver", execute=lambda pkg, log: None, api_version=[1, 0])
    assert record.api_version == (1, 0)
    assert isinstance(record.api_version, tuple)


def test_factory_none_api_version_stores_none():
    """Omitting api_version leaves it as None on the record (no deprecation at factory time)."""
    record = sync_plugin(name="test-none-ver", execute=lambda pkg, log: None)
    assert record.api_version is None


# ---------------------------------------------------------------------------
# api_version: factory rejects malformed values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_value", ["1", 1, 1.0])
def test_factory_rejects_non_sequence_api_version(bad_value):
    """sync_plugin raises TypeError when api_version is not a tuple or list."""
    with pytest.raises(TypeError):
        sync_plugin(name="test-bad", execute=lambda pkg, log: None, api_version=bad_value)


def test_factory_rejects_wrong_length_api_version():
    """sync_plugin raises TypeError when api_version has more than 2 elements."""
    with pytest.raises(TypeError):
        sync_plugin(name="test-bad", execute=lambda pkg, log: None, api_version=(1, 0, 0))


@pytest.mark.parametrize("bad_value", [(True, 0), (1, True)])
def test_factory_rejects_bool_elements_in_api_version(bad_value):
    """sync_plugin raises TypeError when api_version contains bool elements."""
    with pytest.raises(TypeError):
        sync_plugin(name="test-bad", execute=lambda pkg, log: None, api_version=bad_value)


@pytest.mark.parametrize("bad_value", [("1", 0), (1, 0.0)])
def test_factory_rejects_non_int_elements_in_api_version(bad_value):
    """sync_plugin raises TypeError when api_version contains non-int elements."""
    with pytest.raises(TypeError):
        sync_plugin(name="test-bad", execute=lambda pkg, log: None, api_version=bad_value)


def test_factory_rejects_major_less_than_one():
    """sync_plugin raises ValueError when api_version major < 1."""
    with pytest.raises(ValueError):
        sync_plugin(name="test-bad", execute=lambda pkg, log: None, api_version=(0, 5))


def test_factory_rejects_negative_minor():
    """sync_plugin raises ValueError when api_version minor < 0."""
    with pytest.raises(ValueError):
        sync_plugin(name="test-bad", execute=lambda pkg, log: None, api_version=(1, -1))


# ---------------------------------------------------------------------------
# load_external_plugins: api_version gate
# ---------------------------------------------------------------------------

def test_external_plugin_matching_api_version_registers():
    """External plug-in with api_version == PLUGIN_API_VERSION registers normally."""
    def fake_execute(pkg, logger):
        pass

    def fake_factory():
        return sync_plugin(name="versioned-ok", execute=fake_execute, api_version=PLUGIN_API_VERSION)

    ep = _make_fake_entry_point("versioned-ok", "mirror.sync", fake_factory)

    def fake_entry_points(*, group):
        return [ep] if group == "mirror.sync" else []

    with patch("mirror.plugin.importlib.metadata.entry_points", fake_entry_points):
        load_external_plugins({})

    assert "versioned-ok" in mirror.plugin._registry
    assert "versioned-ok" in mirror.sync.methods


def test_external_plugin_wrong_major_is_skipped(caplog):
    """External plug-in with a different major is skipped and a warning is logged."""
    def fake_execute(pkg, logger):
        pass

    def fake_factory():
        return sync_plugin(name="wrong-major", execute=fake_execute, api_version=(2, 0))

    ep = _make_fake_entry_point("wrong-major", "mirror.sync", fake_factory)

    def fake_entry_points(*, group):
        return [ep] if group == "mirror.sync" else []

    with caplog.at_level(logging.WARNING, logger="mirror"):
        with patch("mirror.plugin.importlib.metadata.entry_points", fake_entry_points):
            load_external_plugins({})

    assert "wrong-major" not in mirror.plugin._registry
    assert "wrong-major" not in mirror.sync.methods
    assert any("wrong-major" in r.message for r in caplog.records)


def test_external_plugin_newer_minor_is_skipped(caplog):
    """External plug-in with minor > core minor is skipped and a warning is logged."""
    def fake_execute(pkg, logger):
        pass

    # api_version (1, 1) while PLUGIN_API_VERSION is (1, 0)
    def fake_factory():
        return sync_plugin(name="newer-minor", execute=fake_execute, api_version=(1, 1))

    ep = _make_fake_entry_point("newer-minor", "mirror.sync", fake_factory)

    def fake_entry_points(*, group):
        return [ep] if group == "mirror.sync" else []

    with caplog.at_level(logging.WARNING, logger="mirror"):
        with patch("mirror.plugin.importlib.metadata.entry_points", fake_entry_points):
            load_external_plugins({})

    assert "newer-minor" not in mirror.plugin._registry
    assert "newer-minor" not in mirror.sync.methods
    assert any("newer-minor" in r.message for r in caplog.records)


def test_external_plugin_none_api_version_registers_with_deprecation_warning(caplog):
    """External plug-in with api_version=None registers but emits a deprecation warning."""
    def fake_execute(pkg, logger):
        pass

    def fake_factory():
        return sync_plugin(name="undeclared-ver", execute=fake_execute, api_version=None)

    ep = _make_fake_entry_point("undeclared-ver", "mirror.sync", fake_factory)

    def fake_entry_points(*, group):
        return [ep] if group == "mirror.sync" else []

    with caplog.at_level(logging.WARNING, logger="mirror"):
        with patch("mirror.plugin.importlib.metadata.entry_points", fake_entry_points):
            load_external_plugins({})

    assert "undeclared-ver" in mirror.plugin._registry
    assert "undeclared-ver" in mirror.sync.methods
    assert any("undeclared-ver" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# load_external_plugins: malformed PluginRecord bypassing factory helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_api_version", [1, (True, 0), (1, 0, 0)])
def test_external_plugin_malformed_direct_record_is_skipped_fail_soft(bad_api_version, caplog):
    """Entry point returning PluginRecord with malformed api_version is skipped, no exception raised."""
    def fake_execute(pkg, logger):
        pass

    bad_record = PluginRecord(
        name="malformed-direct",
        type="sync",
        execute=fake_execute,
        api_version=bad_api_version,
    )

    def fake_factory():
        return bad_record

    ep = _make_fake_entry_point("malformed-direct", "mirror.sync", fake_factory)

    def fake_entry_points(*, group):
        return [ep] if group == "mirror.sync" else []

    with caplog.at_level(logging.WARNING, logger="mirror"):
        with patch("mirror.plugin.importlib.metadata.entry_points", fake_entry_points):
            load_external_plugins({})

    assert "malformed-direct" not in mirror.plugin._registry
    assert "malformed-direct" not in mirror.sync.methods
    assert any("malformed-direct" in r.message for r in caplog.records)


def test_serialize_current_plugin_settings_no_config_key(monkeypatch):
    """_serialize_current_plugin_settings must produce enable-only dicts with no 'config' key."""
    from mirror.config import _serialize_current_plugin_settings

    fake_conf = MagicMock()
    fake_conf.plugins = {
        "rsync": PluginSettings(enabled=True),
        "ftpsync": PluginSettings(enabled=False),
    }
    monkeypatch.setattr(mirror, "conf", fake_conf, raising=False)

    result = _serialize_current_plugin_settings()

    assert result == {
        "rsync": {"enabled": True},
        "ftpsync": {"enabled": False},
    }
    for name, entry in result.items():
        assert "config" not in entry, f"'config' key unexpectedly present for {name}"
