# mirror-plugin-echo

Minimal worked example of a mirror.py **event** plug-in. Logs every package status
transition through the mirror logger, with a configurable prefix.

For the full plug-in author guide, see
[`docs/PLUGINS.md`](../../docs/PLUGINS.md) at the mirror.py repo root.

## What it demonstrates

- Declaring a plug-in via PEP 621 `[project.entry-points]` (the `mirror.event`
  group)
- Implementing the `event` plug-in contract: a `setup()` that registers a
  listener via `mirror.event.on(...)`
- Reading per-plug-in configuration through `mirror.plugin.get_config(NAME)`
- The `plugin()` factory returning a typed `PluginRecord` via
  `event_plugin(...)`

## Install

From the repo root:

```bash
uv pip install -e ./examples/mirror-plugin-echo
# or
pip install -e ./examples/mirror-plugin-echo
```

Verify the entry point registered:

```bash
python -c "
from importlib.metadata import entry_points
print([(ep.name, ep.value) for ep in entry_points(group='mirror.event')])
"
# Expected: [('echo', 'mirror_plugin_echo:plugin')]
```

## Configure (optional)

Edit `/etc/mirror/config.json` and add an entry under `settings.plugins`:

```json
{
  "settings": {
    "plugins": {
      "echo": {"enabled": true}
    }
  }
}
```

Per-plugin config lives in a separate file next to `config.json`. To set a
custom prefix, create `/etc/mirror/echo.json`:

```json
{"prefix": "[hello-from-echo]"}
```

If you skip both, the plug-in still loads (default `enabled: true`) and the
prefix defaults to `[echo]`.

To disable without uninstalling: set `"echo": {"enabled": false}`.

## Run

Restart the mirror daemon:

```bash
sudo systemctl restart mirror
```

Tail the master log and watch for echo lines on every package state change:

```bash
tail -f /var/log/mirror/master.stdout.log | grep echo
```

Example output:

```
[2026-05-06 10:00:00,000] INFO # [hello-from-echo] rsync-test -> SYNC
[2026-05-06 10:00:05,123] INFO # [hello-from-echo] rsync-test -> ACTIVE
```

## Uninstall

```bash
uv pip uninstall mirror-plugin-echo
```

The plug-in disappears at the next daemon restart.
