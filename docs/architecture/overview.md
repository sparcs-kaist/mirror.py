# Architecture

mirror.py is a Linux daemon that maintains local mirrors of remote package repositories. It
uses a master-worker model where two long-running processes collaborate over a Unix domain socket:
the master schedules syncs and tracks state, while the worker executes sync subprocesses.

## Master-worker model

```
CLI (click)
  └─ mirror daemon    → MasterServer (listens on master.sock)
  └─ mirror worker    → WorkerServer (listens on worker.sock)

Master daemon:
  1. Loads config, starts MasterServer
  2. Connects to Worker as WorkerClient (persistent session)
  3. 1-second loop: checks each package's sync timing
  4. Delegates sync to Worker via socket RPC

Worker server:
  1. Receives execute_command RPC
  2. Spawns subprocess (rsync/ftpsync/etc.) with UID/GID/nice
  3. Broadcasts job_finished notification to Master
  4. Master calls mirror.sync.on_sync_done() to update status
```

Keeping the two processes separate means that if the master crashes or is restarted, in-flight
sync subprocesses keep running under the worker. The master reconnects to the worker via
`worker.sock` and resumes receiving `job_finished` notifications without interrupting the sync.

## Socket IPC protocol

Communication between the master and worker uses length-prefixed JSON messages sent over Unix
domain sockets. The protocol is:

- **Transport**: length-prefixed JSON frames over Unix domain sockets.
- **Handshake**: 3-step — server info, client info, confirmation — before any RPC traffic.
- **Bidirectional**: supports both request/response RPC (e.g., `execute_command`) and async
  push notifications (e.g., `job_finished` sent from worker to master when a subprocess exits).
- **Persistent connection**: the master holds a single long-lived `WorkerClient` connection so
  it can receive `job_finished` notifications without polling.

## Sync flow

1. The master detects that a package needs syncing, based on comparing `time.time()` against
   `lastsync + syncrate` (where `syncrate` is an ISO 8601 duration parsed to seconds).
2. `mirror.sync.start(package)` launches the sync in a daemon thread.
3. The sync module (e.g., `mirror.sync.rsync`) builds the command and calls
   `mirror.socket.worker.execute_command()` over the persistent socket connection.
4. The worker spawns the subprocess (rsync, ftpsync, etc.) with the configured UID, GID, and
   nice value. When the subprocess exits, the worker sends a `job_finished` notification back
   to the master.
5. The master receives the notification and calls `mirror.sync.on_sync_done(pkgid, success,
   returncode)`, which updates the package status to `ACTIVE` (success) or `ERROR` (failure)
   and persists the new state to `stat.json`.

## Module map

| Module | Responsibility |
|--------|----------------|
| `mirror/__main__.py` | CLI entry point (click commands: setup, daemon, worker, crontab) |
| `mirror/command/` | Command implementations for daemon, worker, setup |
| `mirror/config/` | JSON config loading, package state persistence |
| `mirror/structure/` | Dataclasses: Package, Config, PackageSettings, StatusInfo |
| `mirror/socket/` | Unix socket IPC (protocol, base server/client, master, worker) |
| `mirror/sync/` | Sync method executors (rsync, ftpsync, lftp, bandersnatch) |
| `mirror/worker/` | Subprocess lifecycle management (create, track, prune) |
| `mirror/event/` | Priority-based pub/sub event system |
| `mirror/logger/` | Time-based log rotation, per-package log files, gzip compression |
| `mirror/toolbox/` | Utilities (ISO 8601 duration parser, permission checks) |
| `mirror/plugin/` | Dynamic plugin loading (currently disabled) |

## Path layout

| Path | Purpose | Writable by daemon? |
|------|---------|---------------------|
| `/etc/mirror/config.json` | Main configuration | **No — read-only at runtime** |
| `/var/lib/mirror/stat.json` | Persistent package state | Yes (atomic rewrite on status change) |
| `/var/run/mirror/` | Sockets (master.sock, worker.sock), PID files | Yes |
| `/var/log/mirror/` | Daemon logs, per-package logs under `packages/` | Yes |
| `/var/www/mirror/status.json` | Web status JSON for the UI | Yes |

### Config invariant

`/etc/mirror/config.json` is read-only during daemon and worker runtime. Only `mirror setup`
ever writes it (initial provisioning). Runtime state — sync status, error counts, log paths,
timestamps — lives exclusively in `stat.json`.

When adding a new persisted field:

- DO: extend `Package.StatusInfo` (or another stat-side dataclass) and emit it via
  `Package.to_dict()` so `save_stat_data()` picks it up automatically.
- DO: surface it in `generate_and_save_web_status()` if the UI needs it.
- DON'T: never call `mirror.confPath.write_text(...)` or otherwise mutate the
  user-supplied config.json. There is intentionally no `Config.save()`.

## Active sync methods

- **rsync**: Incremental sync with optional FFTS (Full File Time Stamp) pre-check. The FFTS
  check fetches a metadata file from the upstream to determine whether a full sync is needed,
  short-circuiting the rsync transfer when the upstream has not changed.
- **ftpsync**: Debian archvsync-based FTP mirroring. Uses the bundled archvsync script
  (`mirror/sync/_ftpsync_script.py`) so no external archvsync installation is required.
