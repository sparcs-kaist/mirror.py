"""Unit tests for event plug-in registration and listener dispatch."""
import pytest

import mirror.event
import mirror.plugin
import mirror.sync
from mirror.plugin import _register_event, event_plugin

pytestmark = pytest.mark.usefixtures("clean_plugin_registry")


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
