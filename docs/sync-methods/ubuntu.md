# ubuntu

Two-stage Ubuntu archive mirror using rsync. Stage 1 excludes index files
(`Packages*`, `Sources*`, `Release*`, `InRelease`) so that package files land
before indexes are updated, preventing clients from seeing a temporarily
inconsistent state. Stage 2 syncs the full tree including indexes.

## Options

| Option | Type | Default | Required | Description |
|--------|------|---------|----------|-------------|
| `trace` | bool | `true` | No | Write a trace file at `<dst>/project/trace/<hostname>` on successful sync. |
| `extra_rsync_args` | list of string | `[]` | No | Extra rsync flags appended to both stage 1 and stage 2 invocations. |
| `stage1_excludes` | list of string | `["Packages*", "Sources*", "Release*", "InRelease"]` | No | Patterns excluded during stage 1. Replaces the entire default list when provided. |
| `user` | string | `""` | No | rsync username. Sets the `USER` environment variable for the rsync subprocess. |
| `password` | string | `""` | No | rsync password. Sets the `RSYNC_PASSWORD` environment variable for the rsync subprocess. |

## Example

```json
{
  "pkgid": "ubuntu-jammy",
  "synctype": "ubuntu",
  "syncrate": "PT6H",
  "settings": {
    "src": "rsync://archive.ubuntu.com/ubuntu/",
    "dst": "/srv/mirror/ubuntu",
    "options": {
      "user": "mirror",
      "password": "secret",
      "trace": true,
      "extra_rsync_args": ["--bwlimit=50000"],
      "stage1_excludes": ["Packages*", "Sources*", "Release*", "InRelease"]
    }
  }
}
```

## Full reference

See the [Configuration reference](../guide/configuration.md) (section 4) for the authoritative narrative.
