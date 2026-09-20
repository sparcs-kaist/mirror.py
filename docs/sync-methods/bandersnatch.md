# bandersnatch

PyPI mirror using the `bandersnatch` tool. The mirror.py daemon invokes
`bandersnatch mirror` as a subprocess; all mirroring policy (package filters,
storage backend, upstream URL) is controlled by bandersnatch's own configuration
file. mirror.py supplies no per-package options of its own.

## Options

This method reads no per-package options from the mirror.py package configuration.
Set `"options": {}` in the package config. All behavior is driven by the
bandersnatch configuration file (typically `/etc/bandersnatch.conf`).

| Option | Type | Default | Required | Description |
|--------|------|---------|----------|-------------|
| (none) | — | — | — | This method reads no per-package options. |

## Example

This entry is ready to place inside the top-level `packages` object:

```json
"pypi": {
  "name": "PyPI",
  "id": "pypi",
  "href": "/pypi",
  "synctype": "bandersnatch",
  "syncrate": "PT1H",
  "link": [{ "rel": "HOME", "href": "https://pypi.org/" }],
  "settings": {
    "hidden": false,
    "src": "https://pypi.org",
    "dst": "/srv/mirror/pypi",
    "options": {}
  }
}
```

## Full reference

See the [Configuration reference](../guide/configuration.md) (section 4) for the authoritative narrative.
