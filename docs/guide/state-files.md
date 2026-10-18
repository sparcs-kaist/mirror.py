# State files

mirror.py separates read-only configuration from mutable runtime state. The
configuration file is never modified after `mirror setup`; all runtime state is
written to separate files under `/var/lib/mirror/` and `/var/www/mirror/`.

---

## Path layout

| Path | Purpose | Writable by daemon? |
|------|---------|---------------------|
| `/etc/mirror/config.json` | Main configuration | **No** — read-only at runtime |
| `/var/lib/mirror/stat.json` | Persistent package state | Yes (atomic rewrite on each status change) |
| `/var/run/mirror/` | Unix sockets (`master.sock`, `worker.sock`) and PID files | Yes |
| `/var/log/mirror/` | Daemon logs; per-package logs under `packages/` | Yes |
| `/var/www/mirror/status.json` | Web-accessible status JSON for the mirror UI | Yes |

### Config invariant

`/etc/mirror/config.json` is read-only during daemon and worker runtime. Only
`mirror setup` ever writes it. Runtime state — sync status, error counts, log
paths, timestamps — lives exclusively in `stat.json`. There is intentionally no
`Config.save()` method; never write to `config.json` from the daemon.

---

## stat.json — persistent package state

`/var/lib/mirror/stat.json` is rewritten atomically every time a package
changes status, with file mode `0600`. Its default structure contains only
runtime state; package settings, including credentials, are not copied into it:

```json
{
    "mirrorname": "<name>",
    "packages": {
        "<packageid>": {
            "lastsync": 0.0,
            "timestamp": 0.0,
            "status": {
                "status": "<STATUS>",
                "statusinfo": { ... }
            }
        }
    }
}
```

`lastsync` is the last sync timestamp in seconds; `timestamp` is the last
status transition timestamp in milliseconds. Fields absent in an older file
use their existing defaults when loaded.

On startup or config reload, older state files are rewritten into this format.
Known runtime fields are preserved, including legacy `statusinfo.lastsync`.
Configuration always comes from `config.json`.

Status plug-ins can extend or transform this payload. They are trusted code
and must avoid including credentials in their contributions or other outputs.

### Upgrading existing installations

Update the master, worker, and CLI/TUI together. The package status RPC format
uses IPC protocol version 3; version 2 clients and servers reject the mismatch
during the handshake. Start the updated worker before the updated master.

`mirror setup` creates `config.json` with mode `0600` and restricts an existing
regular config file to `0600` without changing its contents or owner. It rejects
symlinks and other non-regular config paths. For an existing installation,
either rerun setup or restrict the actual config file explicitly:

```bash
sudo chmod 0600 /etc/mirror/config.json
```

Use the actual path when running with a custom `--config`. Runtime loading
does not change config contents or permissions. The updated daemon writes
`stat.json` with `0600` on startup and every subsequent save. Verify the
permissions after startup:

```bash
sudo stat -c '%a %n' /etc/mirror/config.json /var/lib/mirror/stat.json
```

Both files should report `600`. Existing backups are not rewritten by this
migration. Web `status.json` and separate plug-in outputs remain `0644`.

### Per-package status object

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Current package status: `ACTIVE`, `SYNC`, `ERROR`, or `UNKNOWN`. |

### statusinfo fields

These fields are defined in `Package.StatusInfo` in `mirror/structure/__init__.py`.

| Field | Type | Description |
|-------|------|-------------|
| `lasterrorlog` | string or null | Path of the most recent error log file. `null` initially and after a successful sync. |
| `lastsuccesslog` | string or null | Path of the most recent successful sync log file. `null` if no successful sync has completed. |
| `runninglog` | string or null | Path of the log file for the currently running sync. `null` when not syncing. |
| `errorcount` | integer | Number of consecutive errors since the last successful sync. Reset to 0 on `ACTIVE`. |
| `lastsuccesstime` | float | Unix timestamp (seconds) of the last successful sync completion. `0.0` if never succeeded. |
| `lasterrortime` | float | Unix timestamp (seconds) of the last error. `0.0` initially and after a successful sync. |

---

## status.json — web status

`/var/www/mirror/status.json` is regenerated after each sync completes. It is
intended to be served by a web server so that users and monitoring tools can
inspect mirror freshness. Its shape is:

```json
{
    "lastupdate": <timestamp_ms>,
    "mirrorname": "<name>",
    "lists": ["<packageid>", ...],
    "<packageid>": { ... },
    ...
}
```

### Top-level fields

| Field | Type | Description |
|-------|------|-------------|
| `lastupdate` | float | Millisecond timestamp of when the status file was last written. |
| `mirrorname` | string | The `mirrorname` value from `config.json`. |
| `lists` | array of strings | Ordered list of package IDs included in this status file. |

### Per-package entry fields

Each key in `lists` has a corresponding top-level entry with the following
fields:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Human-readable package name. |
| `id` | string | Package identifier matching the key in `config.json`. |
| `status` | string | Current status: `ACTIVE`, `SYNC`, `ERROR`, or `UNKNOWN`. |
| `synctype` | string or null | Sync method in use (e.g. `rsync`, `ftpsync`). |
| `syncrate` | string | Sync interval as an ISO 8601 duration or a special token (`PUSH`). |
| `syncurl` | string | Upstream source URL. |
| `href` | string | Web-accessible path for this mirror on the local server. |
| `lastsync` | float | Unix timestamp in seconds of the last completed sync. `0` if never synced. |
| `links` | array of objects | Related links, each with `rel` (relation label) and `href` (URL). |
| `lastsuccesstime`, `lasterrortime` | float | Unix timestamps in seconds from `statusinfo`. |
| `lastsuccesslog`, `lasterrorlog` | string or null | Log paths from `statusinfo`. |
| `errorcount` | integer | Consecutive failures since the last success. |

Status plugins can add a `plugins` object to per-package entries or replace the
default payload through a transform hook.

---

## Log file layout

Daemon logs are written under the path configured in `settings.logfolder`
(default `/var/log/mirror/`):

```
/var/log/mirror/
    <year>/<month>/<date>.log           # daemon log
    packages/<year>/<month>/<day>/      # per-package sync logs
        <HH>:<MM>:<SS>.<us>.<pkgid>.log
```

Log files are gzip-compressed on rotation when `gzip: true` is set in the
`logger.fileformat` and `logger.packagefileformat` config sections.
