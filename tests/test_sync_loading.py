"""Verify plug-in registration produces the expected mirror.sync.methods set."""
import pytest

import mirror
import mirror.sync
import mirror.plugin

pytestmark = pytest.mark.usefixtures("clean_plugin_registry")


def test_builtin_methods_present():
    """All nine built-in sync types must be registered after phase A."""
    expected = {"rsync", "ftpsync", "lftp", "bandersnatch", "local", "ubuntu", "jigdo", "debmirror", "apt-mirror2"}
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


def test_all_builtin_records_have_execute():
    """Every built-in sync plug-in must expose a callable execute."""
    for name in ("rsync", "ftpsync", "lftp", "bandersnatch", "local", "ubuntu", "jigdo", "debmirror", "apt-mirror2"):
        record = mirror.plugin.get_record(name)
        assert record is not None, f"{name} not in registry"
        assert callable(record.execute), f"{name}.execute not callable"
