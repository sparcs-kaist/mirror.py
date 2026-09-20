# local

No-op sync method for serving an existing local path. The daemon verifies that
`dst` exists and marks the package as active without performing any
remote fetch. Use this when data is already present locally and only needs to be
registered with the daemon for status tracking and web-status reporting.

## Options

This method reads no per-package options. Set `"options": {}` in the package config.

| Option | Type | Default | Required | Description |
|--------|------|---------|----------|-------------|
| (none) | — | — | — | This method reads no per-package options. |

## Example

This entry is ready to place inside the top-level `packages` object:

```json
"local-data": {
  "name": "Local data",
  "id": "local-data",
  "href": "/local-data",
  "synctype": "local",
  "syncrate": "",
  "link": [],
  "settings": {
    "hidden": false,
    "src": "",
    "dst": "/srv/mirror/local-data",
    "options": {}
  }
}
```

## Full reference

See the [Configuration reference](../guide/configuration.md) (section 4) for the authoritative narrative.
