"""Entry-points based plug-in framework for mirror.py.

Loading splits into two phases:
  Phase A (load_builtin_plugins): imports and registers the five built-in sync
    modules at package-import time so mirror.sync.methods is populated before
    package validation runs.
  Phase B (load_external_plugins): called from mirror.config.load() after the
    config dict has been parsed; disables config-disabled built-ins and
    discovers + registers third-party plug-ins via importlib.metadata.

Per-plugin configuration is read from a JSON file in the same directory as the
main config.json.  The default filename is ``<plugin-name>.json``.  Operators
can override the filename per-plugin by setting ``config_filename`` on the
PluginRecord.  The config file is read lazily by get_config() and is never
cached, so changes take effect on the next call.

The plugins block in config.json uses an enable-only shape::

    {
        "<name>": {"enabled": true}
    }

The ``config`` sub-key previously accepted in that block is no longer
supported; move any per-plugin settings to ``<config_dir>/<name>.json``.

Versioning
----------
``PLUGIN_API_VERSION`` is a ``(major, minor)`` tuple that describes the
plug-in contract implemented by this core release.

- **major** increments on breaking changes (calling convention, factory surface,
  entry-point groups, plug-in types).  A plug-in whose declared major differs
  from the core major is skipped at load time.
- **minor** increments on additive, backward-compatible changes (new optional
  hook, new optional field).  A plug-in whose declared minor is *greater* than
  the core minor is also skipped (it was built against features the core does not
  yet provide).  An older plug-in (declared minor <= core minor) continues to load.

Plug-ins declare their target version via the ``api_version`` parameter of the
factory helpers (``sync_plugin``, ``event_plugin``, ``status_plugin``).  The
gate is applied only to external plug-ins loaded in Phase B; built-ins are
loaded ungated in Phase A because they ship in lockstep with the core.  A plug-in
that omits ``api_version`` (``None``) still loads but emits a deprecation warning.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

import mirror

log = logging.getLogger("mirror")

# ---------------------------------------------------------------------------
# StatusOutput
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StatusOutput:
    """Declarative description of an additional status output file written by a status plug-in.

    Args:
        name(str): Globally unique output name (across all plug-ins).
        default_path(str): Filesystem path to write the output to. Operator can
            override via plug-in config if config_path_key is set.
        build(Callable): Callable producing the JSON payload from an iterable of packages.
        config_path_key(Optional[str]): If set, the plug-in's config dict key whose
            value (when present) overrides default_path.
    """

    name: str
    default_path: str
    build: Callable
    config_path_key: Optional[str] = None


# ---------------------------------------------------------------------------
# ConfigCreateResult
# ---------------------------------------------------------------------------

@dataclass
class ConfigCreateResult:
    """Outcome of a plug-in's create_config() call.

    Args:
        path(str): Filesystem path of the plug-in's config file.
        created(bool): True if the file was written; False if the file already
            existed and was left untouched (skipped because --force was not given).
    """

    path: str
    created: bool


# ---------------------------------------------------------------------------
# PluginRecord
# ---------------------------------------------------------------------------

@dataclass
class PluginRecord:
    """Typed descriptor for a registered plug-in.

    Args:
        name(str): Globally unique plug-in name.
        type(str): One of "sync", "event", or "status".
        execute(Callable, optional): Sync execute callable. Sync plug-ins only.
        on_sync_done(Callable, optional): Post-sync hook. Sync plug-ins only.
        setup(Callable, optional): Setup callable (required for event plug-ins).
        extend_stat_fields(Callable, optional): Returns extra stat.json fields for a package.
        extend_web_status_fields(Callable, optional): Returns extra web status fields for a package.
        transform_stat_payload(Callable, optional): Transforms the full stat.json payload dict.
            Single-owner: only one plug-in may register this per daemon instance.
        transform_web_status_payload(Callable, optional): Transforms the full web status payload dict.
            Single-owner: only one plug-in may register this per daemon instance.
        outputs(list, optional): List of StatusOutput instances describing additional
            output files this plug-in writes on every status update.
        create_config(Callable, optional): On-demand config-file creation callable.
            Signature create_config(force: bool) -> ConfigCreateResult. The plug-in
            writes its own config file at a path/name it owns, reading any global
            settings from mirror.conf and its own per-plug-in config via
            mirror.plugin.get_config(<name>); when the file already exists and force
            is False it skips and returns created=False.
        config_filename(str, optional): Optional override for the per-plugin config filename.
            Defaults to ``<name>.json`` when absent. The file is resolved relative to the
            directory that contains the main config.json.
        api_version(tuple, optional): ``(major, minor)`` API version this plug-in was built
            against.  Compared against ``PLUGIN_API_VERSION`` at load time for external
            plug-ins.  ``None`` means undeclared (loads with a deprecation warning).
    """

    name: str
    type: Literal["sync", "event", "status"]
    execute: Optional[Callable] = field(default=None)
    on_sync_done: Optional[Callable] = field(default=None)
    setup: Optional[Callable] = field(default=None)
    extend_stat_fields: Optional[Callable] = field(default=None)
    extend_web_status_fields: Optional[Callable] = field(default=None)
    transform_stat_payload: Optional[Callable] = field(default=None)
    transform_web_status_payload: Optional[Callable] = field(default=None)
    outputs: Optional[list] = field(default=None)  # list[StatusOutput] — kept generic to avoid forward-ref issues
    create_config: Optional[Callable] = field(default=None)
    config_filename: Optional[str] = field(default=None)
    api_version: Optional[tuple[int, int]] = field(default=None)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _normalize_api_version(name: str, api_version) -> Optional[tuple[int, int]]:
    """Return a normalized (major, minor) tuple, or None if api_version is None."""
    if api_version is None:
        return None
    if not isinstance(api_version, (tuple, list)) or len(api_version) != 2:
        raise TypeError(
            f"plug-in '{name}': api_version must be a 2-element tuple or list, "
            f"got {type(api_version)!r}"
        )
    major, minor = api_version
    # Reject bool, which is a subclass of int.
    if isinstance(major, bool) or not isinstance(major, int):
        raise TypeError(
            f"plug-in '{name}': api_version major must be an int (not bool), "
            f"got {type(major)!r}"
        )
    if isinstance(minor, bool) or not isinstance(minor, int):
        raise TypeError(
            f"plug-in '{name}': api_version minor must be an int (not bool), "
            f"got {type(minor)!r}"
        )
    if major < 1:
        raise ValueError(
            f"plug-in '{name}': api_version major must be >= 1, got {major!r}"
        )
    if minor < 0:
        raise ValueError(
            f"plug-in '{name}': api_version minor must be >= 0, got {minor!r}"
        )
    return (major, minor)


def _is_api_compatible(name: str, api_version: Optional[tuple[int, int]]) -> bool:
    """Return True if the already-normalized api_version is compatible with PLUGIN_API_VERSION."""
    if api_version is None:
        log.warning(
            "Plug-in %r does not declare api_version; loading with deprecation warning. "
            "Declare api_version=%r in the factory call to suppress this warning.",
            name, PLUGIN_API_VERSION,
        )
        return True
    if api_version[0] != PLUGIN_API_VERSION[0]:
        log.warning(
            "Plug-in %r declared api_version major %d but core supports major %d; "
            "skipping (incompatible breaking version).",
            name, api_version[0], PLUGIN_API_VERSION[0],
        )
        return False
    if api_version[1] > PLUGIN_API_VERSION[1]:
        log.warning(
            "Plug-in %r declared api_version minor %d but core only supports minor %d; "
            "skipping (plug-in requires a newer core).",
            name, api_version[1], PLUGIN_API_VERSION[1],
        )
        return False
    return True


def sync_plugin(
    name: str,
    execute: Callable,
    on_sync_done: Optional[Callable] = None,
    setup: Optional[Callable] = None,
    create_config: Optional[Callable] = None,
    config_filename: Optional[str] = None,
    api_version: Optional[tuple[int, int]] = None,
) -> PluginRecord:
    """Build a PluginRecord for a sync plug-in with contract validation.

    Args:
        name(str): Unique plug-in name.
        execute(Callable): Sync execute callable — must be provided and callable.
        on_sync_done(Callable, optional): Post-sync hook callable.
        setup(Callable, optional): Optional setup callable.
        create_config(Callable, optional): On-demand config-file creation callable.
        config_filename(str, optional): Override for the per-plugin config filename.
            Defaults to ``<name>.json`` when absent.
        api_version(tuple, optional): ``(major, minor)`` API version this plug-in targets.
            Validated and stored on the returned PluginRecord.

    Return:
        record(PluginRecord): Validated sync PluginRecord.

    Raises:
        TypeError: If execute is missing or not callable, or api_version has wrong type/shape.
        ValueError: If api_version has out-of-range major or minor.
    """
    if execute is None or not callable(execute):
        raise TypeError(
            f"sync_plugin '{name}': execute must be a callable, got {type(execute)!r}"
        )
    if on_sync_done is not None and not callable(on_sync_done):
        raise TypeError(
            f"sync_plugin '{name}': on_sync_done must be callable or None, got {type(on_sync_done)!r}"
        )
    if setup is not None and not callable(setup):
        raise TypeError(
            f"sync_plugin '{name}': setup must be callable or None, got {type(setup)!r}"
        )
    if create_config is not None and not callable(create_config):
        raise TypeError(
            f"sync_plugin '{name}': create_config must be callable or None, got {type(create_config)!r}"
        )
    norm_api_version = _normalize_api_version(name, api_version)
    return PluginRecord(
        name=name,
        type="sync",
        execute=execute,
        on_sync_done=on_sync_done,
        setup=setup,
        create_config=create_config,
        config_filename=config_filename,
        api_version=norm_api_version,
    )


def event_plugin(
    name: str,
    setup: Callable,
    create_config: Optional[Callable] = None,
    config_filename: Optional[str] = None,
    api_version: Optional[tuple[int, int]] = None,
) -> PluginRecord:
    """Build a PluginRecord for an event plug-in with contract validation.

    Args:
        name(str): Unique plug-in name.
        setup(Callable): Required setup callable that registers event listeners.
        create_config(Callable, optional): On-demand config-file creation callable.
        config_filename(str, optional): Override for the per-plugin config filename.
            Defaults to ``<name>.json`` when absent.
        api_version(tuple, optional): ``(major, minor)`` API version this plug-in targets.
            Validated and stored on the returned PluginRecord.

    Return:
        record(PluginRecord): Validated event PluginRecord.

    Raises:
        TypeError: If setup is missing or not callable, or api_version has wrong type/shape.
        ValueError: If api_version has out-of-range major or minor.
    """
    if setup is None or not callable(setup):
        raise TypeError(
            f"event_plugin '{name}': setup is required and must be callable, got {type(setup)!r}"
        )
    if create_config is not None and not callable(create_config):
        raise TypeError(
            f"event_plugin '{name}': create_config must be callable or None, got {type(create_config)!r}"
        )
    norm_api_version = _normalize_api_version(name, api_version)
    return PluginRecord(
        name=name,
        type="event",
        setup=setup,
        create_config=create_config,
        config_filename=config_filename,
        api_version=norm_api_version,
    )


def status_plugin(
    name: str,
    extend_stat_fields: Optional[Callable] = None,
    extend_web_status_fields: Optional[Callable] = None,
    transform_stat_payload: Optional[Callable] = None,
    transform_web_status_payload: Optional[Callable] = None,
    outputs: Optional[list] = None,
    setup: Optional[Callable] = None,
    create_config: Optional[Callable] = None,
    config_filename: Optional[str] = None,
    api_version: Optional[tuple[int, int]] = None,
) -> PluginRecord:
    """Build a PluginRecord for a status plug-in with contract validation.

    Args:
        name(str): Unique plug-in name.
        extend_stat_fields(Callable, optional): Returns extra stat.json fields for a package.
        extend_web_status_fields(Callable, optional): Returns extra web status fields for a package.
        transform_stat_payload(Callable, optional): Transforms the full stat.json payload dict.
        transform_web_status_payload(Callable, optional): Transforms the full web status payload dict.
        outputs(list, optional): List of StatusOutput instances for additional output files.
        setup(Callable, optional): Optional setup callable.
        create_config(Callable, optional): On-demand config-file creation callable.
        config_filename(str, optional): Override for the per-plugin config filename.
            Defaults to ``<name>.json`` when absent.
        api_version(tuple, optional): ``(major, minor)`` API version this plug-in targets.
            Validated and stored on the returned PluginRecord.

    Return:
        record(PluginRecord): Validated status PluginRecord.

    Raises:
        TypeError: If none of extend_*, transform_*, or outputs is provided,
            or if any callable argument is not actually callable, or if outputs
            items are not StatusOutput instances, or if api_version has wrong type/shape.
        ValueError: If api_version has out-of-range major or minor.
    """
    has_outputs = outputs is not None and len(outputs) > 0
    if (
        extend_stat_fields is None
        and extend_web_status_fields is None
        and transform_stat_payload is None
        and transform_web_status_payload is None
        and not has_outputs
    ):
        raise TypeError(
            "status_plugin requires at least one of extend_*, transform_*, or outputs"
        )
    if extend_stat_fields is not None and not callable(extend_stat_fields):
        raise TypeError(
            f"status_plugin '{name}': extend_stat_fields must be callable, "
            f"got {type(extend_stat_fields)!r}"
        )
    if extend_web_status_fields is not None and not callable(extend_web_status_fields):
        raise TypeError(
            f"status_plugin '{name}': extend_web_status_fields must be callable, "
            f"got {type(extend_web_status_fields)!r}"
        )
    if transform_stat_payload is not None and not callable(transform_stat_payload):
        raise TypeError(
            f"status_plugin '{name}': transform_stat_payload must be callable, "
            f"got {type(transform_stat_payload)!r}"
        )
    if transform_web_status_payload is not None and not callable(transform_web_status_payload):
        raise TypeError(
            f"status_plugin '{name}': transform_web_status_payload must be callable, "
            f"got {type(transform_web_status_payload)!r}"
        )
    if outputs is not None:
        for item in outputs:
            if not isinstance(item, StatusOutput):
                raise TypeError(
                    f"status_plugin '{name}': each item in outputs must be a StatusOutput instance, "
                    f"got {type(item)!r}"
                )
    if setup is not None and not callable(setup):
        raise TypeError(
            f"status_plugin '{name}': setup must be callable or None, got {type(setup)!r}"
        )
    if create_config is not None and not callable(create_config):
        raise TypeError(
            f"status_plugin '{name}': create_config must be callable or None, got {type(create_config)!r}"
        )
    norm_api_version = _normalize_api_version(name, api_version)
    return PluginRecord(
        name=name,
        type="status",
        extend_stat_fields=extend_stat_fields,
        extend_web_status_fields=extend_web_status_fields,
        transform_stat_payload=transform_stat_payload,
        transform_web_status_payload=transform_web_status_payload,
        outputs=outputs,
        setup=setup,
        create_config=create_config,
        config_filename=config_filename,
        api_version=norm_api_version,
    )


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

PLUGIN_API_VERSION: tuple[int, int] = (1, 0)

_registry: dict[str, PluginRecord] = {}
_BUILTIN_NAMES: set[str] = set()
_status_stat_hooks: list[tuple[str, Callable]] = []
_status_web_hooks: list[tuple[str, Callable]] = []
_stat_transform_owner: Optional[tuple] = None  # (plugin_name, callable)
_web_status_transform_owner: Optional[tuple] = None  # (plugin_name, callable)
_status_outputs: dict = {}  # output_name -> (plugin_name, StatusOutput)

# Hard-coded built-in entry-point references (module_path:attr)
_BUILTIN_ENTRY_POINTS: list[tuple[str, str]] = [
    ("mirror.sync.rsync", "plugin"),
    ("mirror.sync.ftpsync", "plugin"),
    ("mirror.sync.lftp", "plugin"),
    ("mirror.sync.bandersnatch", "plugin"),
    ("mirror.sync.local", "plugin"),
    ("mirror.sync.ubuntu", "plugin"),
    ("mirror.sync.jigdo", "plugin"),
]


# ---------------------------------------------------------------------------
# Internal registration helpers
# ---------------------------------------------------------------------------

def _register_sync(record: PluginRecord) -> None:
    """Register a sync plug-in into the registry and mirror.sync namespace.

    Args:
        record(PluginRecord): A validated sync PluginRecord.

    Raises:
        ValueError: If a plug-in with the same name is already registered.
    """
    import mirror.sync

    if record.name in _registry:
        raise ValueError(
            f"Plug-in name '{record.name}' is already registered "
            f"(type={_registry[record.name].type!r})"
        )
    _registry[record.name] = record
    if record.name not in mirror.sync.methods:
        mirror.sync.methods.append(record.name)
    # Intentionally do NOT setattr(mirror.sync, name, record): that would shadow
    # the underlying sync module (e.g. mirror.sync.rsync) and break callers that
    # access module-level helpers via mock.patch("mirror.sync.rsync.fn", ...).
    # Sync runtime code looks up records via mirror.plugin.get_record(name).


def _register_event(record: PluginRecord) -> None:
    """Register an event plug-in and immediately call its setup().

    Args:
        record(PluginRecord): A validated event PluginRecord.

    Raises:
        ValueError: If a plug-in with the same name is already registered.
    """
    if record.name in _registry:
        raise ValueError(
            f"Plug-in name '{record.name}' is already registered "
            f"(type={_registry[record.name].type!r})"
        )
    _registry[record.name] = record
    record.setup()


def _register_status(record: PluginRecord) -> None:
    """Register a status plug-in and append its hooks to the hook lists.

    Validates all conflicts before any mutation so a ValueError halfway through
    does not leave partial registration in _registry or hook lists.

    Args:
        record(PluginRecord): A validated status PluginRecord.

    Raises:
        ValueError: If a plug-in with the same name is already registered,
            if a single-owner transform channel is already claimed, or if an
            output name is already claimed by another plug-in.
    """
    global _stat_transform_owner, _web_status_transform_owner

    # Phase 1: validate everything — no side effects.
    if record.name in _registry:
        raise ValueError(
            f"Plug-in name '{record.name}' is already registered "
            f"(type={_registry[record.name].type!r})"
        )
    if record.transform_stat_payload is not None and _stat_transform_owner is not None:
        raise ValueError(
            f"stat transform already owned by '{_stat_transform_owner[0]}'"
        )
    if record.transform_web_status_payload is not None and _web_status_transform_owner is not None:
        raise ValueError(
            f"web_status transform already owned by '{_web_status_transform_owner[0]}'"
        )
    if record.outputs:
        seen_names: set[str] = set()
        for out in record.outputs:
            if out.name in seen_names:
                raise ValueError(
                    f"status_plugin '{record.name}': duplicate output name '{out.name}' "
                    "within the same plug-in's outputs list"
                )
            seen_names.add(out.name)
            if out.name in _status_outputs:
                raise ValueError(
                    f"output '{out.name}' already owned by '{_status_outputs[out.name][0]}'"
                )

    # Phase 2: mutate atomically — all validation passed.
    _registry[record.name] = record
    if record.extend_stat_fields is not None:
        _status_stat_hooks.append((record.name, record.extend_stat_fields))
    if record.extend_web_status_fields is not None:
        _status_web_hooks.append((record.name, record.extend_web_status_fields))
    if record.transform_stat_payload is not None:
        _stat_transform_owner = (record.name, record.transform_stat_payload)
    if record.transform_web_status_payload is not None:
        _web_status_transform_owner = (record.name, record.transform_web_status_payload)
    if record.outputs:
        for out in record.outputs:
            _status_outputs[out.name] = (record.name, out)


def _unregister(name: str) -> None:
    """Remove a plug-in from all state stores. Idempotent: no-op if unknown.

    Cleans up from _registry, _status_stat_hooks, _status_web_hooks,
    _stat_transform_owner, _web_status_transform_owner, _status_outputs, and
    mirror.sync.methods (for sync plug-ins).

    Does NOT undo setup() side effects (e.g. event listener registrations).

    Args:
        name(str): Plug-in name to remove.
    """
    global _stat_transform_owner, _web_status_transform_owner

    record = _registry.pop(name, None)
    if record is None:
        return

    # Clean hook lists.
    _status_stat_hooks[:] = [(n, h) for n, h in _status_stat_hooks if n != name]
    _status_web_hooks[:] = [(n, h) for n, h in _status_web_hooks if n != name]

    # Clean transform owners.
    if _stat_transform_owner is not None and _stat_transform_owner[0] == name:
        _stat_transform_owner = None
    if _web_status_transform_owner is not None and _web_status_transform_owner[0] == name:
        _web_status_transform_owner = None

    # Clean named outputs.
    keys_to_drop = [k for k, (owner, _) in _status_outputs.items() if owner == name]
    for k in keys_to_drop:
        del _status_outputs[k]

    # Clean sync methods.
    if record.type == "sync":
        import mirror.sync
        if name in mirror.sync.methods:
            mirror.sync.methods.remove(name)


_REGISTER_DISPATCH: dict[str, Callable[[PluginRecord], None]] = {
    "sync": _register_sync,
    "event": _register_event,
    "status": _register_status,
}


# ---------------------------------------------------------------------------
# Phase A — built-in plug-ins
# ---------------------------------------------------------------------------

def load_builtin_plugins() -> None:
    """Phase A: import and register all five built-in sync plug-ins.

    Hard-codes the five canonical sync module references so that
    mirror.sync.methods is fully populated before package validation runs.
    ImportError for any individual module is logged as a warning and skipped;
    a successful import that yields a malformed PluginRecord raises immediately
    (that is a programmer error, not a deployment error).
    """
    global _BUILTIN_NAMES

    for module_path, attr in _BUILTIN_ENTRY_POINTS:
        try:
            mod = importlib.import_module(module_path)
        except ImportError as exc:
            log.warning(
                "Built-in plug-in module %r could not be imported: %s", module_path, exc
            )
            continue

        factory = getattr(mod, attr, None)
        if factory is None or not callable(factory):
            raise RuntimeError(
                f"Built-in plug-in {module_path!r} has no callable attribute {attr!r}"
            )

        record = factory()
        if not isinstance(record, PluginRecord):
            raise RuntimeError(
                f"Built-in plug-in {module_path}:{attr}() must return a PluginRecord, "
                f"got {type(record)!r}"
            )

        _REGISTER_DISPATCH[record.type](record)
        _BUILTIN_NAMES.add(record.name)

        if record.setup is not None and record.type != "event":
            # event plug-ins have setup() called inside _register_event;
            # for sync/status built-ins with an optional setup(), call it now.
            try:
                record.setup()
            except Exception as exc:
                log.warning(
                    "Built-in plug-in %r setup() raised: %s", record.name, exc
                )


# ---------------------------------------------------------------------------
# Phase B — external plug-ins + config-based disabling
# ---------------------------------------------------------------------------

def load_external_plugins(plugin_settings: dict) -> None:
    """Phase B: disable built-ins per config and load third-party plug-ins.

    Must be called after mirror.conf is populated (i.e., from mirror.config.load())
    and before mirror.packages is constructed.

    Args:
        plugin_settings(dict): Mapping of plug-in name to PluginSettings (or
            equivalent with an .enabled bool).  Each entry uses the shape
            ``{"<name>": {"enabled": true}}``.  If this is a plain list
            (legacy format) a deprecation warning is logged and the function
            returns without doing anything.
    """
    # Backward-compat: old config had plugins as list[str] of file paths.
    if isinstance(plugin_settings, list):
        log.warning(
            "Deprecated: 'plugins' config is a list of file paths, which is no longer "
            "supported. Update your config to use the dict format: "
            "{\"<name>\": {\"enabled\": true}}. "
            "No plug-ins loaded from this config."
        )
        return

    # Pass 1: disable built-ins (and any already-registered externals) that the operator turned off.
    for name, settings in plugin_settings.items():
        enabled = getattr(settings, "enabled", True)
        if enabled:
            continue
        if name not in _registry:
            continue
        _unregister(name)
        log.info("Disabled plug-in %r per config.", name)

    # Pass 2: discover and load external (non-built-in) plug-ins.
    newly_registered: list[PluginRecord] = []

    for group in ("mirror.sync", "mirror.event", "mirror.status"):
        try:
            eps = importlib.metadata.entry_points(group=group)
        except Exception as exc:
            log.warning("Failed to query entry-point group %r: %s", group, exc)
            continue

        for ep in eps:
            if ep.name in _BUILTIN_NAMES:
                # Built-ins are already loaded in phase A; skip them here.
                continue

            settings = plugin_settings.get(ep.name, None)
            enabled = getattr(settings, "enabled", True) if settings is not None else True

            if not enabled:
                log.info(
                    "Skipping disabled external plug-in %r (group=%s).", ep.name, group
                )
                continue

            try:
                factory = ep.load()
            except Exception as exc:
                log.warning(
                    "Failed to load entry point %r from group %r: %s",
                    ep.name, group, exc,
                )
                continue

            if not callable(factory):
                log.warning(
                    "Entry point %r (group=%r) is not callable; skipping.",
                    ep.name, group,
                )
                continue

            try:
                record = factory()
            except Exception as exc:
                log.warning(
                    "Entry point %r (group=%r) factory() raised: %s",
                    ep.name, group, exc,
                )
                continue

            if not isinstance(record, PluginRecord):
                log.warning(
                    "Entry point %r (group=%r) factory() returned %r, expected PluginRecord; "
                    "skipping.",
                    ep.name, group, type(record),
                )
                continue

            try:
                norm = _normalize_api_version(ep.name, record.api_version)
            except (TypeError, ValueError) as exc:
                log.warning(
                    "External plug-in %r has a malformed api_version: %s; skipping.",
                    ep.name, exc,
                )
                continue

            if not _is_api_compatible(ep.name, norm):
                continue

            try:
                _REGISTER_DISPATCH[record.type](record)
            except (ValueError, KeyError) as exc:
                log.warning(
                    "Failed to register external plug-in %r: %s", ep.name, exc
                )
                continue

            newly_registered.append(record)

    # Finalize: call setup() on newly registered non-event plug-ins that have one.
    # (event plug-ins had setup() called inside _register_event already.)
    for record in newly_registered:
        if record.type == "event":
            continue
        if record.setup is None:
            continue
        try:
            record.setup()
        except Exception as exc:
            log.warning(
                "External plug-in %r setup() raised: %s", record.name, exc
            )


# ---------------------------------------------------------------------------
# Public utility
# ---------------------------------------------------------------------------

def _resolve_plugin_config_path(record: PluginRecord) -> "Path | None":
    """Resolve the filesystem path for a plug-in's per-plugin config file.

    The path is resolved relative to the directory containing the main
    config.json (mirror.config.CONFIG_PATH).  Returns None if the config
    path is unknown or if the filename fails the safety check.

    Args:
        record(PluginRecord): Registered plug-in record.

    Return:
        path(Path | None): Resolved path, or None when resolution is not possible.
    """
    import mirror.config

    config_path = getattr(mirror.config, "CONFIG_PATH", None)
    if config_path is None:
        return None

    filename = record.config_filename or f"{record.name}.json"

    # Safety: reject traversal attempts and empty/dot names.
    if Path(filename).name != filename or filename in ("", ".", ".."):
        log.warning(
            "Plug-in %r has an unsafe config_filename %r; skipping config file lookup.",
            record.name, filename,
        )
        return None

    return Path(config_path).parent / filename


def get_record(name: str) -> "PluginRecord | None":
    """Return the registered PluginRecord for the given plug-in name, or None.

    Args:
        name(str): Plug-in name to look up.

    Return:
        record(PluginRecord | None): The registered record, or None if absent.
    """
    return _registry.get(name)


def get_config(name: str) -> dict:
    """Return the per-plug-in config dict for a registered plug-in.

    Config is read from a JSON file in the same directory as the main
    config.json.  The filename defaults to ``<name>.json`` and can be
    overridden per-plugin via PluginRecord.config_filename.  The file is
    read on every call (no caching).

    Args:
        name(str): Registered plug-in name.

    Return:
        config(dict): The parsed JSON object from the plug-in config file,
            or an empty dict if the file is absent, unreadable, or not a JSON
            object.

    Raises:
        KeyError: If name is not in the registry (plug-in not loaded).
    """
    if name not in _registry:
        raise KeyError(f"No plug-in named {name!r} is registered")

    path = _resolve_plugin_config_path(_registry[name])
    if path is None or not path.exists():
        return {}

    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "Failed to read or parse plug-in config file %r for plug-in %r: %s",
            str(path), name, exc,
        )
        return {}

    if not isinstance(parsed, dict):
        log.warning(
            "Plug-in config file %r for plug-in %r must contain a JSON object, "
            "got %s; ignoring.",
            str(path), name, type(parsed).__name__,
        )
        return {}

    return parsed
