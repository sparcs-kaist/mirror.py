# local

No-op sync method for serving an existing local directory. The daemon verifies
that `dst` exists on disk and marks the package as active without performing any
remote fetch. Use this when data is already present locally and only needs to be
registered with the daemon for status tracking and web-status reporting.

## Options

This method reads no per-package options. Set `"options": {}` in the package config.

| Option | Type | Default | Required | Description |
|--------|------|---------|----------|-------------|
| (none) | — | — | — | This method reads no per-package options. |

## Example

```json
{
  "pkgid": "local-data",
  "synctype": "local",
  "syncrate": "PT1H",
  "settings": {
    "src": "",
    "dst": "/srv/mirror/local-data",
    "options": {}
  }
}
```

## Full reference

See the [Configuration reference](../guide/configuration.md) (section 4) for the authoritative narrative.
