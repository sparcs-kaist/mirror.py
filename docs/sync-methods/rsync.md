# rsync

Generic incremental file mirror using rsync, with an optional FFTS (file-fetch-timestamp)
dry-run pre-check to skip syncs when the upstream has not changed.

## Options

| Option | Type | Default | Required | Description |
|--------|------|---------|----------|-------------|
| `ffts` | bool | `false` | No | Enable an FFTS dry-run pre-check before syncing. When `true`, mirror fetches the upstream file-timestamp list and skips the sync if nothing has changed. |
| `fftsfile` | string | `""` | Required when `ffts` is `true` | Relative path to the upstream timestamp file. It is appended to both `src` and `dst`; an empty value checks the source and destination roots instead of a timestamp file. |
| `user` | string | `""` | No | rsync username. Sets the `USER` environment variable for the rsync subprocess. |
| `password` | string | `""` | No | rsync password. Sets the `RSYNC_PASSWORD` environment variable for the rsync subprocess. |
| `option_include` | string | `""` | No | Flag characters appended to the default rsync flag string (e.g. `"H"` to add `--hard-links`). |
| `option_exclude` | string | `""` | No | Flag characters removed from the default rsync flag string. |
| `exclude` | list of string | `[]` | No | Extra `--exclude` patterns passed to rsync. |

## Example

This entry is ready to place inside the top-level `packages` object:

```json
"debian-cd": {
  "name": "Debian CD",
  "id": "debian-cd",
  "href": "/debian-cd",
  "synctype": "rsync",
  "syncrate": "PT6H",
  "link": [{ "rel": "HOME", "href": "https://www.debian.org/CD/" }],
  "settings": {
    "hidden": false,
    "src": "rsync://ftp.example.org/debian-cd/",
    "dst": "/srv/mirror/debian-cd",
    "options": {
      "user": "mirror",
      "password": "secret",
      "ffts": true,
      "fftsfile": "ls-lR.gz",
      "option_include": "H",
      "option_exclude": "z",
      "exclude": ["Thumbs.db", ".DS_Store"]
    }
  }
}
```

## Full reference

See the [Configuration reference](../guide/configuration.md) (section 4) for the authoritative narrative.
