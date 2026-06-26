# jigdo

Debian CD/DVD image assembly using rsync and `jigdo-mirror`. The sync runs in three
phases: phase 1 fetches jigdo template files (excluding ISOs), phase 2 runs
`jigdo-mirror` to assemble ISO images from a Debian package mirror, and phase 3
syncs the final ISO files.

## Options

| Option | Type | Default | Required | Description |
|--------|------|---------|----------|-------------|
| `jigdo_file` | string | (none) | Yes | rsync URL or path to the jigdo-file index used in phase 1. A `KeyError` is raised at sync time if absent. |
| `debian_mirror` | string | (none) | Yes | Debian package mirror URL passed to `jigdo-mirror` for ISO assembly. A `KeyError` is raised at sync time if absent. |
| `timeout` | int | `7200` | No | rsync `--timeout` value in seconds, applied to all rsync phases. |
| `trace` | bool | `true` | No | Write a trace file on successful sync. |
| `trace_path` | string | `"project/trace"` | No | Relative subdirectory under `dst` where the trace file is written. |
| `template_excludes` | list of string | `["*.iso"]` | No | Phase 1 exclude patterns. Prevents ISO files from being downloaded before assembly. |
| `final_includes` | list of string | `["*businesscard*.iso", "*netinst*.iso", "i386/**.iso"]` | No | Phase 3 include patterns that select which assembled ISOs to sync. |
| `extra_rsync_args` | list of string | `[]` | No | Extra rsync flags appended to both phase 1 and phase 3 rsync invocations. |
| `rsync_bin` | string | `"rsync"` | No | Path or name of the rsync binary. |
| `jigdo_mirror_bin` | string | `"jigdo-mirror"` | No | Path or name of the `jigdo-mirror` binary. |
| `hostname` | string | mirror hostname | No | Override the hostname used in rsync exclude patterns. Defaults to the global mirror hostname, then falls back to `socket.getfqdn()`. |
| `user` | string | `""` | No | rsync username. Sets the `USER` environment variable for rsync subprocesses. |
| `password` | string | `""` | No | rsync password. Sets the `RSYNC_PASSWORD` environment variable for rsync subprocesses. |

## Example

```json
{
  "pkgid": "debian-iso-amd64",
  "synctype": "jigdo",
  "syncrate": "P1D",
  "settings": {
    "src": "rsync://cdimage.debian.org/debian-cd/current/",
    "dst": "/srv/mirror/debian-cd",
    "options": {
      "jigdo_file": "rsync://cdimage.debian.org/debian-cd/current/amd64/jigdo-cd/",
      "debian_mirror": "http://deb.debian.org/debian",
      "user": "mirror",
      "password": "secret",
      "timeout": 7200,
      "trace": true,
      "trace_path": "project/trace",
      "template_excludes": ["*.iso"],
      "final_includes": ["*businesscard*.iso", "*netinst*.iso", "i386/**.iso"],
      "extra_rsync_args": ["--bwlimit=100000"],
      "rsync_bin": "rsync",
      "jigdo_mirror_bin": "jigdo-mirror"
    }
  }
}
```

## Full reference

See the [Configuration reference](../guide/configuration.md) (section 4) for the authoritative narrative.
