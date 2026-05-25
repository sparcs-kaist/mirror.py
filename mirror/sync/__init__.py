import mirror
import mirror.structure
import mirror.logger

import time
import logging
import threading

from pathlib import Path
from typing import Callable, Optional
from threading import Thread

methods = []

# PRE/POST listeners for MASTER.PACKAGE_STATUS_UPDATE MUST NOT acquire
# _start_lock. set_status() fires PRE synchronously (wait=True) while holding
# _start_lock; a listener that re-enters _start_lock from the same or another
# thread would deadlock. In-tree audit: no such listener exists; only the POST
# web-status persistence listener at mirror/config/__init__.py:584, which does
# not touch _start_lock.
_start_lock = threading.Lock()
_extra_args: dict[str, dict[str, str]] = {}
_watchdog_fired: set[str] = set()


def get_module(method: str) -> Callable:
    """Return the loaded sync module for the given method name.

    Args:
        method(str): Sync method name (e.g. "rsync", "ftpsync").

    Return:
        module(Callable): The loaded sync module object.
    """
    import mirror.sync
    return getattr(mirror.sync, method)

def _validate_extra_args(extra_args: dict[str, str]) -> dict[str, str]:
    """Coerce and validate an extra_args mapping for subprocess env use.

    Args:
        extra_args(dict[str, str]): Caller-supplied mapping.

    Return:
        clean(dict[str, str]): Validated copy with str keys/values.

    Raises:
        ValueError: keys/values are not strings, key is empty, or any string
            contains characters disallowed in subprocess env (NUL, '=' in key).
    """
    clean: dict[str, str] = {}
    for k, v in extra_args.items():
        if not isinstance(k, str):
            raise ValueError(f"extra_args key must be str, got {type(k)!r}")
        if not isinstance(v, str):
            raise ValueError(f"extra_args value for key {k!r} must be str, got {type(v)!r}")
        if not k:
            raise ValueError("extra_args key must not be empty")
        if "=" in k:
            raise ValueError(f"extra_args key {k!r} must not contain '='")
        if "\x00" in k:
            raise ValueError(f"extra_args key {k!r} must not contain NUL")
        if "\x00" in v:
            raise ValueError(f"extra_args value for key {k!r} must not contain NUL")
        clean[k] = v
    return clean


def mark_watchdog_fired(pkgid: str) -> bool:
    """Atomically claim the watchdog kill for this pkgid.

    Args:
        pkgid(str): Package identifier.

    Return:
        first(bool): True if this is the first time the watchdog fired for
            pkgid since its last sync start; False if already fired.
    """
    with _start_lock:
        if pkgid in _watchdog_fired:
            return False
        _watchdog_fired.add(pkgid)
        return True


def release_watchdog_fired(pkgid: str) -> None:
    """Release a previously-claimed watchdog marker (e.g., on stop_command failure).

    Args:
        pkgid(str): Package identifier.
    """
    with _start_lock:
        _watchdog_fired.discard(pkgid)


def should_kill_for_max_runtime(uptime: Optional[float], max_runtime_seconds: int) -> bool:
    """Decide whether the watchdog should kill a sync for exceeding max_runtime.

    Args:
        uptime(float, optional): Seconds since the sync started, as reported by
            the worker, or None if the worker did not return uptime info.
        max_runtime_seconds(int): The package's configured cap; 0 disables the
            watchdog.

    Return:
        kill(bool): True if uptime is known and exceeds the cap.
    """
    if max_runtime_seconds <= 0:
        return False
    if uptime is None:
        return False
    return uptime > max_runtime_seconds


def start(package: "mirror.structure.Package", trigger: str = "auto", extra_args: Optional[dict[str, str]] = None) -> None:
    """Start sync for a package.

    Rejects if a sync for the same pkgid is already in progress.

    Args:
        package(mirror.structure.Package): Package to sync.
        trigger(str): Source of the trigger ("auto", "manual", etc.).
        extra_args(dict[str, str], optional): Extra key-value pairs to associate
            with this sync (str->str). Validated before the lock is acquired.
            Cleared from the registry on completion or on scoped launch failure.
            Raises ValueError on bad input (non-str keys/values, empty key,
            '=' or NUL in key, NUL in value). Raises RuntimeError if a sync
            for this pkgid is already in progress (existing behavior unchanged).

    Raises:
        ValueError: If sync method is unknown or extra_args is invalid.
        RuntimeError: If a sync for this pkgid is already in progress.
    """
    import mirror.sync
    import mirror.logger
    import mirror.config

    method = package.synctype
    if method not in methods:
        raise ValueError(f"Unknown sync method: {method}")

    # Validate before acquiring the lock so bad input never touches shared state.
    clean: dict[str, str] = _validate_extra_args(extra_args) if extra_args is not None else {}

    pkgid = package.pkgid
    registered_extra_args = False
    with mirror.config._reload_state_lock:
        with _start_lock:
            if package.is_syncing():
                raise RuntimeError(f"Package {pkgid} sync already in progress")
            package.set_status("SYNC")
            if clean:
                _extra_args[pkgid] = clean
                registered_extra_args = True
            else:
                _extra_args.pop(pkgid, None)
            _watchdog_fired.discard(pkgid)

    started = False
    try:
        start_time = time.time()
        pkg_logger = mirror.logger.create_logger(pkgid, start_time)
        with mirror.config._reload_state_lock:
            log_path = mirror.logger.get_log_path(pkg_logger)
            if log_path is not None:
                package.statusinfo.runninglog = str(log_path)
                mirror.config.save_stat_data()
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
                    pass
            finally:
                # Belt-and-suspenders: guarantee cleanup even if on_sync_done
                # itself raised (discard/pop are idempotent).
                with _start_lock:
                    _extra_args.pop(pkgid, None)
                    _watchdog_fired.discard(pkgid)

        thread = Thread(target=_runner, daemon=True)
        thread.start()
        started = True
    finally:
        if not started:
            with mirror.config._reload_state_lock:
                with _start_lock:
                    pkg_logger = mirror.logger.get(pkgid)
                    if pkg_logger and pkg_logger.handlers:
                        try:
                            mirror.logger.close_logger(pkg_logger)
                        except Exception as exc:
                            mirror.log.error(f"start({pkgid}): close_logger failed: {exc}")
                    package.statusinfo.runninglog = None
                    package.set_status("ERROR")
                    if registered_extra_args:
                        _extra_args.pop(pkgid, None)
                    _watchdog_fired.discard(pkgid)

def get_extra_args(pkgid: str) -> dict[str, str]:
    """Return a shallow copy of the extra_args registered for an in-flight sync, or empty.

    Args:
        pkgid(str): Package ID.

    Return:
        extra_args(dict[str, str]): Copy of the stored mapping (empty if none).
    """
    with _start_lock:
        return dict(_extra_args.get(pkgid, {}))


def on_sync_done(pkgid: str, success: bool, returncode: Optional[int]) -> None:
    """Handle sync completion: log result, call per-module hook, update package status.

    Args:
        pkgid(str): Package identifier.
        success(bool): Whether the sync succeeded.
        returncode(int, optional): Process return code, or None if unavailable.
    """
    import mirror.sync
    import mirror.config

    with mirror.config._reload_state_lock:
        package = mirror.packages.get(pkgid)
        pkglogger = mirror.logger.get(pkgid)

        if package is not None and not mirror.logger.exists(pkgid) and package.statusinfo.runninglog:
            try:
                mirror.logger.reattach_logger(
                    pkglogger, Path(package.statusinfo.runninglog), pkgid
                )
            except Exception as exc:
                mirror.log.warning(f"on_sync_done({pkgid}): reattach failed: {exc}")

        if package is None:
            mirror.log.warning(
                f"on_sync_done({pkgid}): package no longer in config "
                "(likely removed via reload); cleaning up sync state without status update"
            )
            if pkglogger and pkglogger.handlers:
                try:
                    mirror.logger.close_logger(pkglogger)
                except Exception as exc:
                    mirror.log.error(f"on_sync_done({pkgid}): close_logger failed: {exc}")
            with _start_lock:
                _extra_args.pop(pkgid, None)
                _watchdog_fired.discard(pkgid)
            return

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
        package.statusinfo.runninglog = None
        package.set_status("ACTIVE" if success else "ERROR", logfile=logpath)

        with _start_lock:
            _extra_args.pop(pkgid, None)
            _watchdog_fired.discard(pkgid)


def execute(package: "mirror.structure.Package", logger: logging.Logger) -> None:
    """Module-level execute placeholder; sync modules override this."""
    ...
