import mirror
import mirror.structure
import mirror.logger

import time
import logging
import threading

from typing import Callable, Optional
from threading import Thread

methods = []

_start_lock = threading.Lock()
_in_progress: set[str] = set()

def get_module(method: str) -> Callable:
    """Return the loaded sync module for the given method name.

    Args:
        method(str): Sync method name (e.g. "rsync", "ftpsync").

    Return:
        module(Callable): The loaded sync module object.
    """
    import mirror.sync
    return getattr(mirror.sync, method)

def start(package: "mirror.structure.Package", trigger: str = "auto") -> None:
    """Start sync for a package.

    Rejects if a sync for the same pkgid is already in progress.

    Args:
        package(mirror.structure.Package): Package to sync.
        trigger(str): Source of the trigger ("auto", "manual", etc.).

    Raises:
        ValueError: If sync method is unknown.
        RuntimeError: If a sync for this pkgid is already in progress.
    """
    import mirror.sync
    import mirror.logger

    method = package.synctype
    if method not in methods:
        raise ValueError(f"Unknown sync method: {method}")

    pkgid = package.pkgid
    with _start_lock:
        if pkgid in _in_progress:
            raise RuntimeError(f"Package {pkgid} sync already in progress")
        _in_progress.add(pkgid)

    started = False
    try:
        start_time = time.time()
        pkg_logger = mirror.logger.create_logger(pkgid, start_time)

        package.set_status("SYNC")
        mirror.log.info(f"Starting sync for {package.name} ({method})")
        pkg_logger.info(f"Starting sync for {package.name} ({method})")
        pkg_logger.info(f"Time: {time.ctime(start_time)}")
        pkg_logger.info(f"Trigger: {trigger}")

        import mirror.plugin
        sync_record = mirror.plugin.get_record(method)
        if sync_record is None or sync_record.execute is None:
            raise RuntimeError(f"Sync plug-in '{method}' has no execute callable")

        def _runner() -> None:
            try:
                sync_record.execute(package, pkg_logger)
            except Exception as exc:
                pkg_logger.error(f"Unhandled exception in sync runner for {pkgid}: {exc}")
                # If execute() failed before worker delegation, on_sync_done
                # will not be called by the worker; ensure cleanup here too.
                try:
                    on_sync_done(pkgid, success=False, returncode=None)
                except Exception:
                    with _start_lock:
                        _in_progress.discard(pkgid)
            finally:
                # Belt-and-suspenders: guarantee removal even if on_sync_done
                # itself raised (set.discard is idempotent).
                with _start_lock:
                    _in_progress.discard(pkgid)

        thread = Thread(target=_runner, daemon=True)
        thread.start()
        started = True
    finally:
        if not started:
            with _start_lock:
                _in_progress.discard(pkgid)

def on_sync_done(pkgid: str, success: bool, returncode: Optional[int]) -> None:
    """Handle sync completion: log result, call per-module hook, update package status.

    Args:
        pkgid(str): Package identifier.
        success(bool): Whether the sync succeeded.
        returncode(int, optional): Process return code, or None if unavailable.
    """
    import mirror.sync

    package = mirror.packages.get(pkgid)
    if not package:
        raise ValueError(f"Unknown package: {pkgid}")

    pkglogger = mirror.logger.get(pkgid)

    if success:
        pkglogger.info("Sync done successfully")
        pkglogger.info(f"Returncode: {returncode}")
    else:
        pkglogger.error("Sync failed")
        pkglogger.error(f"Returncode: {returncode}")

    # Call plugin-specific on_sync_done if defined
    import mirror.plugin
    sync_record = mirror.plugin.get_record(package.synctype)
    on_done_hook = getattr(sync_record, "on_sync_done", None) if sync_record else None
    if on_done_hook is not None:
        try:
            on_done_hook(package, pkglogger, success, returncode)
        except Exception as e:
            pkglogger.error(f"Plugin on_sync_done failed: {e}")

    # close_logger compresses the file (when gzip is enabled) and returns the
    # final on-disk path. We must record THAT path in stat.json, not the
    # pre-compression path from get_log_path which no longer exists after gzip.
    logpath = mirror.logger.close_logger(pkglogger)
    package.lastsync = time.time()
    package.set_status("ACTIVE" if success else "ERROR", logfile=logpath)

    with _start_lock:
        _in_progress.discard(pkgid)


def execute(package: "mirror.structure.Package", logger: logging.Logger) -> None:
    """Module-level execute placeholder; sync modules override this."""
    ...
