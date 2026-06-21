"""Local sync method: this server is the authoritative master mirror.

Performs no upstream transfer. The local data living at package.settings.dst
is what other mirrors pull FROM us, so there is nothing to sync. We verify
the destination directory exists (operator misconfiguration if not) and
immediately mark the sync ACTIVE.
"""
import logging
from pathlib import Path

import mirror
import mirror.structure
import mirror.sync


def execute(package: mirror.structure.Package, pkg_logger: logging.Logger, trigger: str = "auto") -> None:
    """Mark a local-mirror package as successfully 'synced' without transferring.

    Args:
        package(mirror.structure.Package): Package whose synctype is 'local'.
        pkg_logger(logging.Logger): Per-sync session logger.
    """
    pkg_logger.info(f"Local mirror for {package.name}; no upstream sync required.")
    dst = Path(package.settings.dst)
    if not dst.exists():
        pkg_logger.error(f"Local sync dst does not exist: {dst}")
        mirror.sync.on_sync_done(package.pkgid, success=False, returncode=None)
        return
    mirror.sync.on_sync_done(package.pkgid, success=True, returncode=0)


def plugin():
    """Entry-point factory for the local plug-in.

    Return:
        record(mirror.plugin.PluginRecord): Sync plug-in record exposing execute.
    """
    from mirror.plugin import sync_plugin
    return sync_plugin(name="local", execute=execute)
