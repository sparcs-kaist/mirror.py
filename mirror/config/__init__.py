import mirror
import mirror.event
import mirror.plugin
import mirror.structure
import mirror.config.config
import mirror.config.stat
import mirror.config.status
import mirror.toolbox
import time
import os
import tempfile
import threading

from pathlib import Path
import json

_reload_state_lock = threading.RLock()

# --- Global Path Variables ---
CONFIG_PATH: Path
STAT_DATA_PATH: Path
STATUS_PATH: Path
SOCKET_PATH: str


# --- Private Helpers ---

def _atomic_write_json(path: Path, payload: dict, indent: int = 4, mode: int | None = None) -> None:
    """Write JSON atomically: tempfile in same dir, then os.replace.

    Args:
        path(Path): Final destination path.
        payload(dict): JSON-serializable content.
        indent(int): json.dumps indent.
        mode(int, optional): If set, chmod the file before the swap. tempfile.mkstemp
            produces 0o600 by default; pass 0o644 for files the web UI / monitoring
            must read. Plug-in outputs leave this unset to inherit the secure default.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent)
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _default_package_status() -> dict:
    return {
        "status": "UNKNOWN",
        "statusinfo": {"errorcount": 0},
    }


def _merge_package_config_with_stat(pkg_config: dict, existing_stat: dict | None) -> dict:
    """Merge config-owned package fields with known runtime-only stat fields."""
    merged = pkg_config.copy()
    if not existing_stat:
        merged["status"] = _default_package_status()
        return merged

    if "status" in existing_stat:
        merged["status"] = existing_stat["status"]
    else:
        merged["status"] = _default_package_status()

    if "lastsync" in existing_stat:
        merged["lastsync"] = existing_stat["lastsync"]
    else:
        legacy_status = existing_stat.get("status")
        if isinstance(legacy_status, dict):
            legacy_statusinfo = legacy_status.get("statusinfo", {})
            if "lastsync" in legacy_statusinfo:
                merged["lastsync"] = legacy_statusinfo["lastsync"]

    if "timestamp" in existing_stat:
        merged["timestamp"] = existing_stat["timestamp"]

    return merged


# --- Loading Functions ---

def load(conf_path: Path):
    """
    Loads the main config file, derives other paths from it, synchronizes
    with the persistent stat file, and loads the state into the application.
    """
    global CONFIG_PATH
    CONFIG_PATH = conf_path

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")

    config_dict = json.loads(CONFIG_PATH.read_text())
    _load_from_dict(config_dict, source_path=conf_path, load_plugins=True)


def _load_from_dict(config_dict: dict, *, source_path: Path | None = None, load_plugins: bool = True) -> None:
    """Load application state from an already-parsed config dictionary.

    Used by load() for initial startup and by the reload path when the config
    has already been read and parsed. The lock is held for the entire operation
    so that concurrent status reads see a consistent view.

    Args:
        config_dict(dict): Parsed JSON content of the configuration file.
        source_path(Path, optional): If provided, CONFIG_PATH is updated to
            this value. Pass None when calling from a reload that already has
            CONFIG_PATH set.
        load_plugins(bool): When True, call
            mirror.plugin.load_external_plugins() after loading the config.
            Set to False in contexts where plugin registration must be skipped
            (e.g. unit tests or partial reloads).
    """
    global CONFIG_PATH, STAT_DATA_PATH, STATUS_PATH, SOCKET_PATH

    with _reload_state_lock:
        if source_path is not None:
            CONFIG_PATH = source_path

        # 1. Derive paths from settings block
        config = config_dict.get("settings", {})
        stat_path_str = config.get("statfile")
        status_path_str = config.get("statusfile")
        SOCKET_PATH = config.get("socket_path") or None

        if not stat_path_str or not status_path_str:
            raise ValueError("Config file must contain 'statfile' and 'statusfile' settings.")

        STAT_DATA_PATH = Path(stat_path_str)
        STATUS_PATH = Path(status_path_str)

        # Ensure STATE_PATH exists with restrictive permissions for ftpsync tempdirs.
        if not mirror.STATE_PATH.exists():
            mirror.STATE_PATH.mkdir(parents=True, mode=0o700, exist_ok=True)

        # 2. Load stat file and synchronize with config
        stat_dict = json.loads(STAT_DATA_PATH.read_text()) if STAT_DATA_PATH.exists() else {"packages": {}}
        config_packages = config_dict.get("packages", {})
        stat_packages = stat_dict.get("packages", {})
        final_stat_packages = {}

        for pkg_id, pkg_config in config_packages.items():
            existing_stat = stat_packages.get(pkg_id)
            final_stat_packages[pkg_id] = _merge_package_config_with_stat(pkg_config, existing_stat)

        # 3. Construct the full stat dictionary and save it atomically
        full_stat_to_save = {
            "mirrorname": config_dict.get("mirrorname"),
            "packages": final_stat_packages
        }
        try:
            _atomic_write_json(STAT_DATA_PATH, full_stat_to_save, mode=0o644)
        except Exception as e:
            mirror.log.error(f"Failed to save merged stat data to {STAT_DATA_PATH}: {e}")
            raise

        # 4. Prepare for in-memory loading
        loader_packages = {}
        for pkg_id, pkg_data in full_stat_to_save.get("packages", {}).items():
            loader_packages[pkg_id] = pkg_data

        # 5. Load into application
        mirror.conf = mirror.structure.Config.load_from_dict(config_dict)
        if load_plugins:
            mirror.plugin.load_external_plugins(mirror.conf.plugins)
        mirror.packages = mirror.structure.Packages(loader_packages)

        # 6. Load the web status file
        _load_web_status_data()

def _load_web_status_data():
    """Loads the data for the web status page."""
    if STATUS_PATH and STATUS_PATH.exists():
        mirror.status = json.loads(STATUS_PATH.read_text())
    else:
        mirror.log.warning(f"Web status file not found at {STATUS_PATH}. Web status will be unavailable.")
        mirror.status = {}

def _serialize_current_plugin_settings() -> dict:
    """Serialize the live plugin settings back to the dict shape Config.load_from_dict expects.

    Return:
        out(dict): Mapping of plugin name to {"enabled": bool, "config": dict}.
    """
    out: dict = {}
    for name, settings in (mirror.conf.plugins or {}).items():
        out[name] = {
            "enabled": getattr(settings, "enabled", True),
            "config": getattr(settings, "config", {}),
        }
    return out


def _validate_candidate_packages(sanitized_dict: dict) -> None:
    """Build candidate Packages from sanitized config without mutating state.

    Replicates the stat-merge that _load_from_dict performs, but only
    in-memory. Raises if any Package.from_dict / Packages constructor fails.

    Args:
        sanitized_dict(dict): Sanitized config dict (post-override).
    """
    config_packages = sanitized_dict.get("packages", {})
    if STAT_DATA_PATH and STAT_DATA_PATH.exists():
        current_stat = json.loads(STAT_DATA_PATH.read_text())
        current_stat_packages = current_stat.get("packages", {})
    else:
        current_stat_packages = {}

    simulated: dict = {}
    for pkgid, pkg_config in config_packages.items():
        existing = current_stat_packages.get(pkgid)
        simulated[pkgid] = _merge_package_config_with_stat(pkg_config, existing)

    mirror.structure.Packages(simulated)


def _perform_reload() -> dict:
    """Re-read config from disk and apply hot-reloadable changes.

    Sanitizes path-class / plugin / logger settings to current runtime values
    (warn-and-ignore policy), kills in-flight syncs of removed packages,
    validates the candidate config, then swaps state under
    ``_reload_state_lock``.

    Return:
        result(dict): {
            "status": "ok" | "error",
            "added": [pkgid, ...],
            "removed": [pkgid, ...],
            "modified": [pkgid, ...],
            "killed_inflight": [pkgid, ...],
            "killed_timeout": [pkgid, ...],
            "warnings": [str, ...],
            "error": str (only when status=="error"),
        }
    """
    import copy
    import mirror.socket.worker
    import mirror.sync
    import mirror.structure

    warnings: list[str] = []

    # Step a: Guard — CONFIG_PATH must be set and the file must exist.
    config_path: Path | None = globals().get("CONFIG_PATH")
    if not config_path or not Path(config_path).exists():
        return {"status": "error", "error": f"config path not set or does not exist: {config_path}"}

    # Step b: Read and parse the new config JSON.
    try:
        new_config_dict = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "error", "error": f"failed to read/parse config: {exc}"}

    # Step c: Validate top-level shape.
    if not isinstance(new_config_dict.get("settings"), dict):
        return {"status": "error", "error": "config missing required 'settings' dict"}
    if not isinstance(new_config_dict.get("packages"), dict):
        return {"status": "error", "error": "config missing required 'packages' dict"}

    # Step d: Build sanitized_dict — deep copy, then override runtime-locked fields.
    sanitized_dict = copy.deepcopy(new_config_dict)
    settings_block = sanitized_dict["settings"]

    def _check_and_override(key: str, current_value) -> None:
        supplied = settings_block.get(key)
        # Compare as strings so Path vs str differences don't matter.
        if supplied is not None and str(supplied) != str(current_value):
            warnings.append(f"{key} change requires daemon restart (kept current value)")
        settings_block[key] = current_value

    _check_and_override("socket_path", SOCKET_PATH)
    _check_and_override("logfolder", str(mirror.conf.logfolder))
    _check_and_override("webroot", str(mirror.conf.webroot))
    _check_and_override("statusfile", str(STATUS_PATH))
    _check_and_override("statfile", str(STAT_DATA_PATH))
    _check_and_override("uid", mirror.conf.uid)
    _check_and_override("gid", mirror.conf.gid)

    # logger: deep-copy current to avoid aliasing.
    supplied_logger = settings_block.get("logger")
    current_logger = copy.deepcopy(mirror.conf.logger)
    if supplied_logger is not None and supplied_logger != current_logger:
        warnings.append("logger change requires daemon restart (kept current value)")
    settings_block["logger"] = current_logger

    # plugins: serialize live state.
    current_plugins = _serialize_current_plugin_settings()
    supplied_plugins = settings_block.get("plugins")
    if supplied_plugins is not None and supplied_plugins != current_plugins:
        warnings.append("plugins change requires daemon restart (kept current value)")
    settings_block["plugins"] = current_plugins

    # Step e: Validate candidate config — catch any error without touching state.
    try:
        mirror.structure.Config.load_from_dict(sanitized_dict)
    except Exception as exc:
        return {
            "status": "error",
            "error": f"config validation failed: {exc}",
            "warnings": warnings,
        }

    # Validate package ids without requiring full stat data.
    new_pkg_ids = set(sanitized_dict.get("packages", {}).keys())
    for pkgid in new_pkg_ids:
        try:
            mirror.structure.Packages._validate_id(pkgid)
        except ValueError as exc:
            return {
                "status": "error",
                "error": f"config validation failed: {exc}",
                "warnings": warnings,
            }

    # Validate full Package construction (synctype, required fields, etc.)
    # by simulating the stat-merge that _load_from_dict performs, but only
    # in-memory. This must happen BEFORE any state mutation.
    try:
        _validate_candidate_packages(sanitized_dict)
    except Exception as exc:
        return {
            "status": "error",
            "error": f"config validation failed: {exc}",
            "warnings": warnings,
            "added": [],
            "removed": [],
            "modified": [],
            "killed_inflight": [],
            "killed_timeout": [],
        }

    # Step f: Compute pkgid set diff.
    with _reload_state_lock:
        old_pkg_ids = set(mirror.packages.keys())

    added = list(new_pkg_ids - old_pkg_ids)
    removed = list(old_pkg_ids - new_pkg_ids)
    retained = new_pkg_ids & old_pkg_ids

    # Detect modified packages by comparing config sub-dicts.
    _config_fields = {"name", "synctype", "syncrate", "settings", "link", "href"}
    modified: list[str] = []
    for pkgid in retained:
        new_pkg_cfg = new_config_dict["packages"].get(pkgid, {})
        with _reload_state_lock:
            old_pkg = mirror.packages.get(pkgid)
        if old_pkg is None:
            continue
        old_pkg_dict = old_pkg.to_dict()
        for field in _config_fields:
            if new_pkg_cfg.get(field) != old_pkg_dict.get(field):
                modified.append(pkgid)
                break

    # Step g: Stop in-flight syncs for removed packages.
    killed_inflight: list[str] = []
    removed_with_inflight: set[str] = set()
    for pkgid in removed:
        if pkgid in mirror.sync._in_progress:
            removed_with_inflight.add(pkgid)
            try:
                mirror.socket.worker.stop_command(job_id=pkgid)
                killed_inflight.append(pkgid)
            except Exception as exc:
                mirror.log.error(f"_perform_reload: stop_command({pkgid}) failed: {exc}")

    # Step h: Active-poll wait — up to 10 seconds, every 100ms.
    killed_timeout: list[str] = []
    if removed_with_inflight:
        deadline = time.monotonic() + 10.0
        remaining = set(removed_with_inflight)
        while remaining and time.monotonic() < deadline:
            for pkgid in list(remaining):
                if pkgid not in mirror.sync._in_progress:
                    remaining.discard(pkgid)
                    continue
                try:
                    mirror.socket.worker.get_progress(pkgid)
                except Exception:
                    pass
            if remaining:
                time.sleep(0.1)
        killed_timeout = list(remaining)

    # Step i: Warn about synctype change on in-progress retained packages.
    for pkgid in retained:
        if pkgid not in mirror.sync._in_progress:
            continue
        new_synctype = new_config_dict["packages"].get(pkgid, {}).get("synctype")
        with _reload_state_lock:
            old_pkg = mirror.packages.get(pkgid)
        if old_pkg is not None and new_synctype and new_synctype != old_pkg.synctype:
            warnings.append(
                f"package {pkgid}: synctype changed mid-sync; "
                "in-flight job will use NEW hooks"
            )

    # Step j: Apply — _load_from_dict holds _reload_state_lock internally.
    _load_from_dict(sanitized_dict, source_path=None, load_plugins=False)

    return {
        "status": "ok",
        "added": added,
        "removed": removed,
        "modified": modified,
        "killed_inflight": killed_inflight,
        "killed_timeout": killed_timeout,
        "warnings": warnings,
    }


def reload() -> dict:
    """Reloads all configurations.

    This is now a thin wrapper around ``_perform_reload``; see that function
    for the orchestration details.
    """
    return _perform_reload()

def generate_and_save_web_status():
    """
    Generates the web status dictionary from the current package states
    and saves it to the status.json file.
    """
    if not STATUS_PATH:
        mirror.log.error("Cannot save web status, path not set.")
        return

    with _reload_state_lock:
        web_status = {
            "mirrorname": mirror.conf.name,
            "lastupdate": time.time() * 1000,
            "lists": list(mirror.packages.keys()),
        }

        for pkg_id in mirror.packages.keys():
            package = getattr(mirror.packages, pkg_id)

            web_status[pkg_id] = {
                "name": package.name,
                "id": package.pkgid,
                "status": package.status,
                "synctype": package.synctype,
                "syncrate": mirror.toolbox.format_iso_duration(package.syncrate),
                "syncurl": package.settings.src,
                "href": package.href,
                "lastsync": package.lastsync,
                "lastsuccesstime": package.statusinfo.lastsuccesstime,
                "lasterrortime": package.statusinfo.lasterrortime,
                "lastsuccesslog": package.statusinfo.lastsuccesslog,
                "lasterrorlog": package.statusinfo.lasterrorlog,
                "errorcount": package.statusinfo.errorcount,
                "links": [link.to_dict() for link in package.link],
            }
            if mirror.plugin._status_web_hooks:
                plugin_extras = {}
                for plugin_name, hook in mirror.plugin._status_web_hooks:
                    try:
                        contributed = hook(package)
                        if contributed:
                            plugin_extras[plugin_name] = contributed
                    except Exception as e:
                        mirror.log.warning(f"Status plug-in {plugin_name} extend_web_status_fields failed: {e}")
                if plugin_extras:
                    web_status[pkg_id]["plugins"] = plugin_extras

        import mirror.plugin as _plugin
        if _plugin._web_status_transform_owner is not None:
            plugin_name, transform_fn = _plugin._web_status_transform_owner
            try:
                web_status = transform_fn(web_status)
            except Exception as e:
                mirror.log.warning(f"Web status transform from plug-in '{plugin_name}' failed: {e}")

        try:
            _atomic_write_json(STATUS_PATH, web_status, mode=0o644)
            mirror.log.info(f"Web status successfully generated and saved to {STATUS_PATH}")
        except Exception as e:
            mirror.log.error(f"Failed to save web status to {STATUS_PATH}: {e}")

def save_stat_data():
    """Saves the current package states to the persistent stat file."""
    if not STAT_DATA_PATH:
        mirror.log.error("Cannot save stat data, path not set.")
        return

    with _reload_state_lock:
        packages_dict = {}
        for pkg_id in mirror.packages.keys():
            package = mirror.packages.get(pkg_id)
            pkg_dict = package.to_dict()
            if mirror.plugin._status_stat_hooks:
                plugin_extras = {}
                for plugin_name, hook in mirror.plugin._status_stat_hooks:
                    try:
                        contributed = hook(package)
                        if contributed:
                            plugin_extras[plugin_name] = contributed
                    except Exception as e:
                        mirror.log.warning(f"Status plug-in {plugin_name} extend_stat_fields failed: {e}")
                if plugin_extras:
                    pkg_dict["status"]["statusinfo"]["plugins"] = plugin_extras
            packages_dict[pkg_id] = pkg_dict

        full_stat = {
            "mirrorname": mirror.conf.name,
            "packages": packages_dict,
        }

        import mirror.plugin as _plugin
        if _plugin._stat_transform_owner is not None:
            plugin_name, transform_fn = _plugin._stat_transform_owner
            try:
                full_stat = transform_fn(full_stat)
            except Exception as e:
                mirror.log.warning(f"Stat transform from plug-in '{plugin_name}' failed: {e}")

        try:
            _atomic_write_json(STAT_DATA_PATH, full_stat, mode=0o644)
        except Exception as e:
            mirror.log.error(f"Failed to save stat data to {STAT_DATA_PATH}: {e}")

def _resolve_output_path(plugin_name: str, output) -> Path:
    """Resolve the path for a StatusOutput, honoring config-based override.

    Args:
        plugin_name(str): Owning plug-in name.
        output(mirror.plugin.StatusOutput): Output declaration.

    Return:
        path(Path): Resolved filesystem path.
    """
    import mirror.plugin as _plugin
    if output.config_path_key:
        try:
            cfg = _plugin.get_config(plugin_name)
            override = cfg.get(output.config_path_key)
            if override:
                return Path(override)
        except KeyError:
            pass
    return Path(output.default_path)


def _write_status_outputs() -> None:
    """Write each plug-in-declared status output file.

    Per-plug-in isolation: failures are logged via mirror.log.warning and
    other outputs proceed.
    """
    import mirror.plugin as _plugin
    for output_name, (plugin_name, output) in _plugin._status_outputs.items():
        try:
            path = _resolve_output_path(plugin_name, output)
            payload = output.build(list(mirror.packages.values()))
            _atomic_write_json(path, payload)
        except Exception as e:
            mirror.log.warning(
                f"Status output '{output_name}' from plug-in '{plugin_name}' failed: {e}"
            )


@mirror.event.listener("MASTER.PACKAGE_STATUS_UPDATE.POST")
def _on_package_status_update(*args, **kwargs):
    """Automatically save status and stats when a package status changes."""
    generate_and_save_web_status()
    save_stat_data()
    _write_status_outputs()
