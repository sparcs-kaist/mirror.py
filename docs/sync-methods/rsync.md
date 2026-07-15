# rsync

Generic incremental file mirror using rsync, with an optional FFTS (file-fetch-timestamp)
dry-run pre-check to skip syncs when the upstream has not changed.

## Options

| Option | Type | Default | Required | Description |
|--------|------|---------|----------|-------------|
| `ffts` | bool | `false` | No | Enable an FFTS dry-run pre-check before syncing. When `true`, mirror fetches the upstream file-timestamp list and skips the sync if nothing has changed. |
| `fftsfile` | string | `""` | Required when `ffts` is `true` | Path or URL to the upstream file-timestamp list. Operationally required when `ffts` is enabled; the code defaults to an empty string and does not raise an error if absent, so omitting it silently disables the FFTS check. |
| `user` | string | `""` | No | rsync username. Sets the `USER` environment variable for the rsync subprocess. |
| `password` | string | `""` | No | rsync password. Sets the `RSYNC_PASSWORD` environment variable for the rsync subprocess. |
| `option_include` | string | `""` | No | Flag characters appended to the default rsync flag string (e.g. `"H"` to add `--hard-links`). |
| `option_exclude` | string | `""` | No | Flag characters removed from the default rsync flag string. |
| `exclude` | list of string | `[]` | No | Extra `--exclude` patterns passed to rsync. |

## Example

```json
{
  "pkgid": "debian-cd",
  "synctype": "rsync",
  "syncrate": "PT6H",
  "settings": {
    "src": "rsync://ftp.example.org/debian-cd/",
    "dst": "/srv/mirror/debian-cd",
    "options": {
      "user": "mirror",
      "password": "secret",
      "ffts": true,
      "fftsfile": "rsync://ftp.example.org/debian-cd/ls-lR.gz",
      "option_include": "H",
      "option_exclude": "z",
      "exclude": ["Thumbs.db", ".DS_Store"]
    }
  }
}
```

## Full reference

See the [Configuration reference](../guide/configuration.md) (section 4) for the authoritative narrative.
