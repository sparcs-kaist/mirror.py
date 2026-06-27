"""Example mirror.py event plug-in.

Subscribes to MASTER.PACKAGE_STATUS_UPDATE.POST and logs every transition
through the mirror logger. Demonstrates:

- Declaring an entry point in pyproject.toml
- Implementing setup() to register a listener
- Reading per-plug-in config via mirror.plugin.get_config()

Install for local testing:
    uv pip install -e ./examples/mirror-plugin-echo
Then restart the mirror daemon.
"""
import logging

import mirror.event
import mirror.plugin

NAME = "echo"


def _on_status(package, status) -> None:
    """Listener invoked for every status transition.

    Args:
        package: mirror.structure.Package whose status just changed.
        status(str): The new status string ("ACTIVE", "SYNC", "ERROR", "UNKNOWN").
    """
    cfg = mirror.plugin.get_config(NAME)
    prefix = cfg.get("prefix", "[echo]")
    logging.getLogger("mirror").info(f"{prefix} {package.pkgid} -> {status}")


def setup() -> None:
    """Register the listener at plug-in load time."""
    mirror.event.on("MASTER.PACKAGE_STATUS_UPDATE.POST", _on_status)


def plugin():
    """Entry-point factory consumed by mirror.plugin.load_external_plugins.

    Return:
        record(mirror.plugin.PluginRecord): Event plug-in record.
    """
    from mirror.plugin import event_plugin
    return event_plugin(name=NAME, setup=setup, api_version=(1, 0))
