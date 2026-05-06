# Writing plug-ins for mirror.py

mirror.py uses a [PEP 621 entry-points](https://packaging.python.org/en/latest/specifications/entry-points/) based plug-in system. Plug-ins are regular pip-installable Python packages — discovery happens through `importlib.metadata.entry_points()`, not by listing file paths in config.

If you have written pytest, Sphinx, or Celery plug-ins before, the model will feel familiar.

## Three plug-in categories

| Category | Entry-point group | What it does | Example use case |
|---|---|---|---|
| `sync` | `mirror.sync` | Implements a new synctype (in addition to built-in `rsync`, `ftpsync`, `lftp`, `bandersnatch`, `local`) | Mirror via SFTP, S3, custom HTTP transport |
| `event` | `mirror.event` | Subscribes to mirror events to perform side effects | Slack/email notification on sync failure, push metrics to Prometheus, custom audit log |
| `status` | `mirror.status` | Contributes extra fields into `stat.json` and the web status JSON | Bandwidth-used counter, freshness classification, mirror health rollup |

A single distribution (one pip-installable package) can ship multiple plug-ins across categories — declare each as a separate entry-point.

## Quickstart: hello-world `event` plug-in

This is the smallest end-to-end plug-in. Source lives under [`examples/mirror-plugin-echo/`](../examples/mirror-plugin-echo).

### 1. Create the package

```
mirror-plugin-echo/
├── pyproject.toml
└── mirror_plugin_echo/
    └── __init__.py
```

`pyproject.toml`:
```toml
[project]
name = "mirror-plugin-echo"
version = "0.1.0"
dependencies = ["mirror.py>=1.0.0rc10"]

[project.entry-points."mirror.event"]
echo = "mirror_plugin_echo:plugin"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```

`mirror_plugin_echo/__init__.py`:
```python
import logging

import mirror.event
import mirror.plugin

NAME = "echo"


def _on_status(package, status) -> None:
    cfg = mirror.plugin.get_config(NAME)
    prefix = cfg.get("prefix", "[echo]")
    logging.getLogger("mirror").info(f"{prefix} {package.pkgid} -> {status}")


def setup() -> None:
    mirror.event.on("MASTER.PACKAGE_STATUS_UPDATE.POST", _on_status)


def plugin():
    from mirror.plugin import event_plugin
    return event_plugin(name=NAME, setup=setup)
```

### 2. Install into the daemon's environment

```bash
uv pip install ./mirror-plugin-echo
# or
pip install ./mirror-plugin-echo
```

### 3. Configure (optional)

`/etc/mirror/config.json` (the read-only-at-runtime config):
```json
{
  "settings": {
    "plugins": {
      "echo": {
        "enabled": true,
        "config": {"prefix": "[hello-from-echo]"}
      }
    }
  }
}
```

If you skip this, the plug-in is enabled by default with empty config.

### 4. Restart the daemon

`systemctl restart mirror` (or whichever service manager). Watch `/var/log/mirror/master.stdout.log` to see the echo plug-in print on every package status change.

## Plug-in contract

Every plug-in module must expose a callable referenced by its entry point that returns a `PluginRecord`. Use the factory helpers — they validate the contract and produce the right shape.

### `sync` plug-ins

```python
from mirror.plugin import sync_plugin

def execute(package, pkg_logger):
    """Required. Drive the sync, eventually call mirror.sync.on_sync_done."""
    ...

def on_sync_done(package, pkg_logger, success, returncode):
    """Optional. Called when the worker reports completion."""
    ...

def setup():
    """Optional. Module-level init at registration time."""
    ...

def plugin():
    return sync_plugin(name="myproto", execute=execute,
                      on_sync_done=on_sync_done, setup=setup)
```

`execute()` receives the `Package` and a per-sync `Logger`. It is called from a daemon thread. Common pattern: build a command, hand it to `mirror.socket.worker.execute_command(...)` for subprocess execution; the worker's `job_finished` notification eventually calls `mirror.sync.on_sync_done(pkgid, success, returncode)` automatically. If your sync runs synchronously (no worker), call `mirror.sync.on_sync_done` yourself before returning — see the bundled `local` synctype for the simplest example.

### `event` plug-ins

```python
from mirror.plugin import event_plugin

def setup():
    """Required. Register listeners via mirror.event.on(...)."""
    mirror.event.on("MASTER.PACKAGE_STATUS_UPDATE.POST", my_listener)

def plugin():
    return event_plugin(name="my-notifier", setup=setup)
```

Available events:

| Event name | Args | When it fires |
|---|---|---|
| `MASTER.INIT.PRE` | — | Master daemon starting (before socket bind), `wait=True` |
| `MASTER.INIT.POST` | — | Master daemon ready, after socket bind |
| `MASTER.PACKAGE_STATUS_UPDATE.PRE` | `(package, new_status)` | Right before `package.status` mutates, `wait=True` |
| `MASTER.PACKAGE_STATUS_UPDATE.POST` | `(package, new_status)` | After mutation, after timestamps update |
| `MASTER.WORKER_RECONNECTED` | — | After a master-side restart re-establishes the worker connection |

Listeners run in a thread pool (`ThreadPoolExecutor`, 20 workers). If you mutate shared state, hold a lock. `wait=True` events block the caller until all listeners finish; `wait=False` events return immediately and listener exceptions are logged but not raised.

### `status` plug-ins

```python
from mirror.plugin import status_plugin

def extend_stat_fields(package) -> dict:
    """Optional. Return dict to merge into stat.json under
    statusinfo.plugins['<plugin-name>']."""
    return {"my_metric": compute(package)}

def extend_web_status_fields(package) -> dict:
    """Optional. Same idea for web status JSON."""
    return {"freshness": classify(package.lastsync)}

def plugin():
    return status_plugin(name="my-stats",
                        extend_stat_fields=extend_stat_fields,
                        extend_web_status_fields=extend_web_status_fields)
```

Each plug-in's contribution is **nested under its own key** — `statusinfo.plugins["my-stats"]` and `web_status[pkgid]["plugins"]["my-stats"]` — so two plug-ins can return overlapping field names safely. Hooks run synchronously per status write (not via the event thread pool), in registration order. A hook raising an exception is logged via `mirror.log.warning` and other plug-ins are unaffected.

## API reference

### `mirror.plugin.PluginRecord`

A frozen dataclass produced by the factory helpers. Fields:

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Globally unique across all categories |
| `type` | `Literal["sync", "event", "status"]` | |
| `execute` | `Callable | None` | Required for `sync` |
| `on_sync_done` | `Callable | None` | Optional for `sync` |
| `setup` | `Callable | None` | Required for `event`; optional for the rest |
| `extend_stat_fields` | `Callable | None` | Optional for `status` |
| `extend_web_status_fields` | `Callable | None` | Optional for `status` |

You should not construct `PluginRecord` directly — always use the factory functions.

### Factory functions

```python
mirror.plugin.sync_plugin(name, execute, on_sync_done=None, setup=None) -> PluginRecord
mirror.plugin.event_plugin(name, setup) -> PluginRecord
mirror.plugin.status_plugin(name, extend_stat_fields=None, extend_web_status_fields=None, setup=None) -> PluginRecord
```

Each validates its required arguments and raises `TypeError` on contract violation, so a plug-in author with a typo gets a clear error at import time.

### Lookups

- `mirror.plugin.get_record(name) -> PluginRecord | None` — registered record or None.
- `mirror.plugin.get_config(name) -> dict` — the plug-in's config block from `mirror.conf.plugins[name].config`. Returns `{}` if the operator wrote no config block. Raises `KeyError` if the name is not registered.

## Config schema

The `plugins` value under `settings` is a map of `name -> {enabled: bool, config: dict}`:

```json
"plugins": {
  "echo":         { "enabled": true,  "config": {"prefix": "[echo]"} },
  "slack-notify": { "enabled": true,  "config": {"webhook_url": "https://..."} },
  "lftp":         { "enabled": false }
}
```

A plug-in absent from the map defaults to `enabled: true` with empty config. Setting `enabled: false` for a built-in (`rsync`, `ftpsync`, `lftp`, `bandersnatch`, `local`) prunes it from `mirror.sync.methods`; subsequent package validation rejects packages that still reference that synctype with `ValueError("Sync type not in [...]")`.

The legacy list-of-strings shape (`"plugins": ["/path/to/plugin.py", ...]`) used by older mirror.py versions is detected at load time, logged as a deprecation warning, and ignored. There is no automatic migration — operators must rewrite the config to the dict shape.

## Bootstrap order (why this matters for sync plug-ins)

Plug-in registration happens in two phases:

1. **Phase A — built-ins, at package import time.** `mirror/__init__.py` calls `mirror.plugin.load_builtin_plugins()`, which registers all five canonical synctypes (`rsync`, `ftpsync`, `lftp`, `bandersnatch`, `local`). These are hard-coded in `pyproject.toml` under the `mirror.sync` entry-point group.

2. **Phase B — externals + disable, inside `mirror.config.load()`.** After `Config.load_from_dict` parses the raw config but before `Packages` validates each package's synctype, the loader:
   1. Removes built-ins that the operator disabled in config.
   2. Iterates installed entry points across `mirror.sync`/`mirror.event`/`mirror.status`, filtered to non-built-ins, and registers each (skipping ones the operator disabled).
   3. Calls `setup()` on each newly registered plug-in.

This means when `Package.from_dict` checks `synctype in mirror.sync.methods`, the methods list reflects the union of built-ins (minus disabled) and externals (minus disabled).

`event` and `status` plug-ins do not affect package validation — they only need to be registered before whatever event or status write they hook.

## Naming conventions

- **Distribution name**: prefix with `mirror-plugin-` (e.g. `mirror-plugin-slack-notify`). This is convention only; the loader does not enforce it.
- **Internal `name`**: globally unique across all three categories. The loader keeps a single registry; a sync plug-in named `slack` and an event plug-in named `slack` collide and the second registration raises `ValueError`.

## Trust model

Plug-ins are arbitrary Python code that runs in-process at the daemon's privilege level. There is no sandbox. The discovery channel is whatever wheels are installed in the daemon's site-packages — same trust level as any other dependency.

Recommended:
- Pin plug-in versions in your deployment manifest to avoid silent upgrades.
- Audit plug-in source before installing, especially anything outside your organisation's PyPI mirror.
- Plug-in disable (`enabled: false`) skips `ep.load()` entirely — the file is not even imported. Useful as a kill switch.

## Future: pluggy adoption

The entry-point group names (`mirror.sync`, `mirror.event`, `mirror.status`) were chosen to be compatible with [pluggy](https://pluggy.readthedocs.io). When/if mirror.py adopts pluggy hookspec/hookimpl, already-published plug-ins continue to work — pluggy's `pm.load_setuptools_entrypoints("mirror.<group>")` consumes the same entry-points. Plug-in authors would only need to add `@hookimpl` decorators to their existing functions.

This is currently out-of-scope for the codebase but informs the design.

## Worked example

See [`examples/mirror-plugin-echo/`](../examples/mirror-plugin-echo/) for a complete, installable `event` plug-in package. It logs every package status change to mirror's logger with a configurable prefix.
