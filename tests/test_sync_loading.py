"""Verify plug-in registration produces the expected mirror.sync.methods set."""
import pytest

import mirror
import mirror.sync
import mirror.plugin
from mirror.plugin import load_builtin_plugins


@pytest.fixture(autouse=True)
def _ensure_builtins_loaded():
    """Guarantee all five built-ins are registered before each test.

    test_example_config.py replaces mirror.sync.methods with a truncated list,
    so we always rebuild from scratch here rather than relying on whatever state
    earlier tests left behind.
    """
    mirror.plugin._registry.clear()
    mirror.plugin._BUILTIN_NAMES.clear()
    mirror.sync.methods.clear()
    load_builtin_plugins()

    orig_registry = dict(mirror.plugin._registry)
    orig_methods = list(mirror.sync.methods)
    orig_builtins = set(mirror.plugin._BUILTIN_NAMES)

    yield

    mirror.plugin._registry.clear()
    mirror.plugin._registry.update(orig_registry)
    mirror.sync.methods[:] = orig_methods
    mirror.plugin._BUILTIN_NAMES.clear()
    mirror.plugin._BUILTIN_NAMES.update(orig_builtins)


def test_builtin_methods_present():
    """All five built-in sync types must be registered after phase A."""
    expected = {"rsync", "ftpsync", "lftp", "bandersnatch", "local"}
    assert expected == set(mirror.sync.methods), (
        f"Expected {expected}, got {set(mirror.sync.methods)}"
    )


def test_rsync_record_is_valid():
    """get_record('rsync') returns a PluginRecord with the expected shape."""
    record = mirror.plugin.get_record("rsync")
    assert record is not None, "rsync PluginRecord not found in registry"
    assert record.name == "rsync"
    assert record.type == "sync"
    assert callable(record.execute), "rsync execute must be callable"


def test_all_five_records_have_execute():
    """Every built-in sync plug-in must expose a callable execute."""
    for name in ("rsync", "ftpsync", "lftp", "bandersnatch", "local"):
        record = mirror.plugin.get_record(name)
        assert record is not None, f"{name} not in registry"
        assert callable(record.execute), f"{name}.execute not callable"
