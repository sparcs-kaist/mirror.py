import mirror
import mirror.config
import mirror.logger
import mirror.sync
import mirror.socket
import mirror.socket.worker
import mirror.event
import mirror.structure

import time
import signal
import sys
import os
from pathlib import Path

SETUP_GRACE_SECONDS = 60
MISMATCH_GRACE_SECONDS = 5

_mismatch_first_seen: dict[str, float] = {}


def _note_mismatch(pkgid: str, now: float) -> float:
    """Record first-seen timestamp for a daemon-observed worker/status mismatch.

    Args:
        pkgid(str): Package ID.
        now(float): Current time.time() value.

    Return:
        first_seen(float): The original first-seen timestamp (may be earlier than now).
    """
    return _mismatch_first_seen.setdefault(pkgid, now)


def _clear_mismatch(pkgid: str) -> None:
    """Reset the mismatch tracking for a pkgid after a consistent observation."""
    _mismatch_first_seen.pop(pkgid, None)


def _cleanup_daemon(socket_server, pid_file: Path, sock_path_file: Path) -> None:
    try:
        socket_server.stop()
    except Exception as exc:
        mirror.log.debug(f"Failed to stop master socket server: {exc}")
    mirror.socket.stop()
    if pid_file.exists():
        pid_file.unlink()
    if sock_path_file.exists():
        sock_path_file.unlink()


def _watchdog_check(package: mirror.structure.Package) -> None:
    """Probe the worker for uptime and kill the sync if max_runtime exceeded.

    Reads the global cap from `mirror.conf.max_runtime_seconds`. The cap is
    operator-wide; per-package overrides are intentionally not supported.

    Args:
        package(Package): Currently-syncing package to inspect.
    """
    max_runtime_seconds = getattr(mirror.conf, "max_runtime_seconds", 0)
    if max_runtime_seconds <= 0:
        return

    try:
        progress = mirror.socket.worker.get_progress(package.pkgid)
    except Exception as exc:
        mirror.log.debug(f"watchdog: get_progress failed for {package.pkgid}: {exc}")
        return

    if not progress.get("syncing"):
        return

    info = progress.get("info") or {}
    uptime = info.get("uptime")
    if not mirror.sync.should_kill_for_max_runtime(uptime, max_runtime_seconds):
        return

    if not mirror.sync.mark_watchdog_fired(package.pkgid):
        return

    mirror.log.error(
        f"Package {package.pkgid} sync exceeded max_runtime "
        f"(limit={max_runtime_seconds}s, ran={uptime:.0f}s); killing"
    )
    try:
        result = mirror.socket.worker.stop_command(job_id=package.pkgid)
    except Exception as exc:
        # Release the marker so the next iteration can retry. Without this a
        # transient RPC error would leave the package locked out of watchdog
        # retries and stuck in SYNC indefinitely.
        mirror.sync.release_watchdog_fired(package.pkgid)
        mirror.log.error(f"watchdog: stop_command failed for {package.pkgid}: {exc}")
        return

    status = result.get("status") if isinstance(result, dict) else None
    if status != "stopped":
        # Worker did not actually stop the job (e.g. "not_found"). Release the
        # marker so subsequent iterations can re-evaluate; if on_sync_done has
        # already fired the release is a harmless no-op.
        mirror.sync.release_watchdog_fired(package.pkgid)
        mirror.log.warning(
            f"watchdog: stop_command for {package.pkgid} returned status={status!r}"
        )


def should_auto_sync(package: mirror.structure.Package, now: float, errorcontinuetime: int) -> bool:
    """Decide whether the daemon auto-loop should start a sync for this package.

    Args:
        package(Package): Package to evaluate.
        now(float): Current epoch seconds (injected for testability).
        errorcontinuetime(int): Seconds to wait before retrying after an ERROR.

    Return:
        should_sync(bool): True if the auto-loop should call sync.start(package).
    """
    if package.syncrate <= 0:
        return False
    if now - package.lastsync > package.syncrate:
        return True
    if package.status == "ERROR" and now - package.lastsync > errorcontinuetime:
        return True
    return False


def daemon(config: str) -> None:
    """Run the mirror master daemon.

    Args:
        config(str): Path to the main JSON configuration file.
    """
    # Load all configurations from the single config file path.
    mirror.config.load(Path(config))
    mirror.logger.setup_logger()

    # Write PID file
    pid_file = mirror.RUN_PATH / "mirror.pid"
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()))
    except OSError as e:
        mirror.log.error(f"Failed to write PID file {pid_file}: {e}")
        sys.exit(1)

    # Fire initialization complete event
    mirror.event.post_event("MASTER.INIT.PRE", wait=True)

    # Start Master Server socket
    socket_server = mirror.socket.init("master")

    sock_path_file = mirror.RUN_PATH / "master.sock.path"
    try:
        sock_path_file.write_text(str(socket_server.socket_path))
        sock_path_file.chmod(0o644)
    except OSError as e:
        mirror.log.warning(f"Failed to write socket path metadata to {sock_path_file}: {e}")

    mirror.log.info(f"Master Daemon listening on {socket_server.socket_path}")
    mirror.log.info("Daemon started and configuration loaded.")

    if mirror.socket.worker.is_worker_running():
        mirror.log.info("Worker server is running and reachable.")
    else:
        mirror.log.error("Worker server is NOT running. Sync operations may fail if they rely on it.")

    shutdown_requested = False

    def signal_handler(sig, frame):
        nonlocal shutdown_requested
        shutdown_requested = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    def sighup_handler(sig, frame):
        # SIGNAL-HANDLER SAFETY: this MUST do as little as possible.
        # request_signal() ONLY does `self._sighup_pending = True` (no lock, no Event).
        from mirror.config.reload_controller import reload_controller
        reload_controller.request_signal()

    signal.signal(signal.SIGHUP, sighup_handler)

    from mirror.config.reload_controller import reload_controller

    mirror.event.post_event("MASTER.INIT.POST")

    try:
        while True:
            if shutdown_requested:
                break

            should_reload, responses = reload_controller.consume_pending()
            if should_reload:
                t0 = time.monotonic()
                try:
                    result = mirror.config._perform_reload()
                    result.setdefault("status", "ok")
                except Exception as e:
                    mirror.log.error(f"Reload failed: {e}")
                    result = {"status": "error", "error": str(e)}
                result["duration_seconds"] = round(time.monotonic() - t0, 3)
                if result.get("status") == "ok":
                    mirror.log.info(f"Reload done: {result}")
                reload_controller.signal_done(responses, result)

            for package in mirror.packages.values():
                try:
                    if package.is_disabled():
                        _clear_mismatch(package.pkgid)
                        mirror.log.debug(f"Package {package.pkgid} is disabled. Skipping.")
                        continue

                    if package.is_syncing():
                        if mirror.socket.worker.is_worker_running(package.pkgid):
                            _clear_mismatch(package.pkgid)
                            _watchdog_check(package)
                            continue
                        # Worker has no job for this package. Either we're still in setup
                        # (sync.start ran but execute_command hasn't been called yet) or the
                        # worker lost/finished the job without notifying master. Distinguish
                        # by sync age — Package.timestamp is ms since epoch, set when the
                        # status flipped to SYNC.
                        now = time.time()
                        first_seen = _note_mismatch(package.pkgid, now)
                        if now - first_seen < MISMATCH_GRACE_SECONDS:
                            continue
                        sync_age = now - (package.timestamp / 1000.0)  # timestamp is ms
                        if sync_age < SETUP_GRACE_SECONDS:
                            continue
                        with mirror.config._reload_state_lock:
                            if not package.is_syncing():
                                _clear_mismatch(package.pkgid)
                                continue
                            mirror.log.warning(
                                f"Package {package.pkgid} marked as syncing but no worker found "
                                f"after {sync_age:.0f}s; transitioning to ERROR"
                            )
                            pkg_logger = mirror.logger.get(package.pkgid)
                            if pkg_logger and pkg_logger.handlers:
                                mirror.logger.close_logger(pkg_logger)
                            package.set_status("ERROR")
                        _clear_mismatch(package.pkgid)
                        continue
                    elif mirror.socket.worker.is_worker_running(package.pkgid):
                        now = time.time()
                        first_seen = _note_mismatch(package.pkgid, now)
                        if now - first_seen < MISMATCH_GRACE_SECONDS:
                            continue
                        with mirror.config._reload_state_lock:
                            if not package.is_syncing():
                                mirror.log.error(
                                    f"Package is syncing while status is {package.status}. "
                                    "Changed the status to syncing."
                                )
                                package.set_status("SYNC")
                        _clear_mismatch(package.pkgid)
                        continue
                    else:
                        _clear_mismatch(package.pkgid)

                    if should_auto_sync(package, time.time(), mirror.conf.errorcontinuetime):
                        mirror.log.info(f"Package {package.pkgid} requires sync (last_sync={package.lastsync}, syncrate={package.syncrate}, status={package.status})")
                        mirror.sync.start(package)
                except Exception as e:
                    # A single package failing must not crash the whole daemon.
                    mirror.log.error(f"Package {package.pkgid} loop iteration failed: {e}")

            time.sleep(1)
        mirror.log.info("Master Daemon stopping...")
        _cleanup_daemon(socket_server, pid_file, sock_path_file)
        sys.exit(0)
    except Exception as e:
        mirror.log.error(f"Daemon failed: {e}")
        _cleanup_daemon(socket_server, pid_file, sock_path_file)
        sys.exit(1)
