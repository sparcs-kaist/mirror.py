# ftpsync

Debian archvsync-based FTP mirroring. Generates a `ftpsync.conf` file from the package
options and invokes the `ftpsync` shell script, which handles Debian-specific trace files,
architecture filtering, and the push-triggered mirroring protocol.

## Options

| Option | Type | Default | Required | Description |
|--------|------|---------|----------|-------------|
| `path` | string | (none) | Conditional | REQUIRED when `src` is a bare hostname (e.g. `ftp.example.org`); a `ValueError` is raised at sync time if omitted. OPTIONAL when `src` is an `rsync://` URL, where it overrides the path component of the URL. |
| `hub` | string | `"false"` | No | Passed through to the `HUB=` shell variable in `ftpsync.conf`. Use `"true"` or `"false"`. |
| `user` | string | (none) | No | `RSYNC_USER` in `ftpsync.conf`. Emitted only when BOTH `user` and `password` are present. |
| `password` | string | (none) | No | `RSYNC_PASSWORD` in `ftpsync.conf`. Emitted only when BOTH `user` and `password` are present. |
| `email` | string | (none) | No | `MAILTO` for ftpsync notifications. |
| `maintainer` | string | global `settings.ftpsync.maintainer` | No | Overrides global `INFO_MAINTAINER`. |
| `sponsor` | string | global `settings.ftpsync.sponsor` | No | Overrides global `INFO_SPONSOR`. |
| `country` | string | global `settings.ftpsync.country` | No | Overrides global `INFO_COUNTRY`. |
| `location` | string | global `settings.ftpsync.location` | No | Overrides global `INFO_LOCATION`. |
| `throughput` | string | global `settings.ftpsync.throughput` | No | Overrides global `INFO_THROUGHPUT`. |
| `arch_include` | string | global `settings.ftpsync.include` | No | `ARCH_INCLUDE` in `ftpsync.conf`. Architectures to include (space-separated). |
| `arch_exclude` | string | global `settings.ftpsync.exclude` | No | `ARCH_EXCLUDE` in `ftpsync.conf`. Architectures to exclude (space-separated). |
| `logdir` | path | global log folder | No | Override the directory where ftpsync writes its own log files. |

> **Note on `include`/`exclude` vs `arch_include`/`arch_exclude`:**
> The bare `include` and `exclude` keys exist only in the GLOBAL `settings.ftpsync` block
> of `config.json`. Per-package architecture overrides must use the `arch_include` and
> `arch_exclude` keys inside the package `options` object.

## Example

```json
{
  "pkgid": "debian-amd64",
  "synctype": "ftpsync",
  "syncrate": "PT6H",
  "settings": {
    "src": "rsync://ftp.example.org/debian/",
    "dst": "/srv/mirror/debian",
    "options": {
      "user": "mirror",
      "password": "secret",
      "hub": "false",
      "email": "admin@example.org",
      "maintainer": "Example Mirror Operators",
      "country": "KR",
      "arch_include": "amd64 arm64",
      "arch_exclude": "mips mipsel"
    }
  }
}
```

## Full reference

See the [Configuration reference](../guide/configuration.md) (section 4) for the authoritative narrative.
