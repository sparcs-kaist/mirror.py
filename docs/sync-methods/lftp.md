# lftp

Mirror an anonymous FTP site using lftp's `mirror` command, with support for
fine-grained regex-based exclusions via lftp's `-x` and `-X` patterns.

`src` must be an `ftp://` URL without credentials, a query, a fragment, or
percent-encoded characters. mirror.py always configures the anonymous user and
derives the anonymous password from the source hostname.

## Options

| Option | Type | Default | Required | Description |
|--------|------|---------|----------|-------------|
| `list_options` | string | (none) | No | Only `"-a"` is accepted. Passes the `-a` flag to the lftp `ls` command to include hidden files. Any other value raises a `ValueError`. |
| `scan_all_first` | bool | `false` | No | Pass `--scan-all-first` to `lftp mirror`, which scans the entire remote tree before transferring. |
| `exclude_x` | list of string | `["\\.in\\..*\\."]` | No | Regex patterns passed as `-x` (exclude matching filenames). Supplying either `exclude_x` or `exclude_X` enables custom-exclude mode; omitted lists retain their defaults. |
| `exclude_X` | list of string | `["\\.(mirror\|notar)", "lost+found"]` | No | Regex patterns passed as `-X` (exclude matching paths). Supplying either regex option switches both regex lists to the validated configured/default values. |
| `exclude` | list of string | `[]` | No | Shell-glob `--exclude` patterns passed to `lftp mirror`. |
| `max_retries` | int (1-100) | `3` | No | Sets lftp's `net:max-retries`. |
| `net_timeout` | int (1-3600) | `60` | No | Sets lftp's `net:timeout` in seconds. |

## Example

This entry is ready to place inside the top-level `packages` object:

```json
"example-ftp": {
  "name": "Example FTP",
  "id": "example-ftp",
  "href": "/example-ftp",
  "synctype": "lftp",
  "syncrate": "PT12H",
  "link": [{ "rel": "HOME", "href": "https://ftp.example.org/" }],
  "settings": {
    "hidden": false,
    "src": "ftp://ftp.example.org/pub/data/",
    "dst": "/srv/mirror/example-ftp",
    "options": {
      "list_options": "-a",
      "scan_all_first": false,
      "exclude_x": ["\\.in\\..*\\."],
      "exclude_X": ["\\.(mirror|notar)", "lost+found"],
      "exclude": ["*.tmp", "*.bak"],
      "max_retries": 5,
      "net_timeout": 120
    }
  }
}
```

## Full reference

See the [Configuration reference](../guide/configuration.md) (section 4) for the authoritative narrative.
