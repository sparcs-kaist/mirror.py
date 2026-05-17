"""Unit tests for event plug-in registration and listener dispatch."""
import pytest

import mirror.event
import mirror.plugin
import mirror.sync
from mirror.plugin import _register_event, event_plugin


# ---------------------------------------------------------------------------
# Fixture: restore registry after each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_registry():
    """Snapshot and restore the plug-in registry around each test.

    Rebuilds a clean baseline before snapshotting so that pollution from
    test_example_config.py (which replaces mirror.sync.methods with a new list)
    does not corrupt the snapshot.
    """
    from mirror.plugin import load_builtin_plugins

    # Rebuild clean baseline before snapshotting
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


# ---------------------------------------------------------------------------
# Event plug-in registration
# ---------------------------------------------------------------------------

def test_event_plugin_setup_called_on_register():
    """setup() must be called immediately when _register_event is invoked."""
    called = []

    def fake_setup():
        called.append(True)

    record = event_plugin(name="test-event-setup", setup=fake_setup)
    _register_event(record)

    assert called == [True], "setup() was not called during registration"
    assert "test-event-setup" in mirror.plugin._registry

    mirror.plugin._registry.pop("test-event-setup", None)


def test_event_plugin_listener_receives_event():
    """A listener registered inside setup() must be invoked when the event fires."""
    received = []

    def listener(*args, **kwargs):
        received.append((args, kwargs))

    def fake_setup():
        mirror.event.on("MASTER.PACKAGE_STATUS_UPDATE.POST", listener)

    record = event_plugin(name="test-event-listener", setup=fake_setup)
    _register_event(record)

    try:
        mirror.event.post_event(
            "MASTER.PACKAGE_STATUS_UPDATE.POST",
            "payload-arg",
            wait=True,
        )
    finally:
        mirror.event.off("MASTER.PACKAGE_STATUS_UPDATE.POST", listener)
        mirror.plugin._registry.pop("test-event-listener", None)

    assert len(received) == 1, f"Expected 1 call, got {len(received)}"
    assert received[0][0] == ("payload-arg",)


def test_event_plugin_listener_not_called_after_off():
    """After mirror.event.off, the listener must no longer be invoked."""
    received = []

    def listener(*args, **kwargs):
        received.append(True)

    def fake_setup():
        mirror.event.on("MASTER.PACKAGE_STATUS_UPDATE.POST", listener)

    record = event_plugin(name="test-event-off", setup=fake_setup)
    _register_event(record)
    mirror.plugin._registry.pop("test-event-off", None)

    # Deregister before firing
    mirror.event.off("MASTER.PACKAGE_STATUS_UPDATE.POST", listener)
    mirror.event.post_event("MASTER.PACKAGE_STATUS_UPDATE.POST", wait=True)

    assert received == [], "Listener was called even after off()"
