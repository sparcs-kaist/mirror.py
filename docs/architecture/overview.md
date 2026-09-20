# Architecture

mirror.py maintains local copies of remote package repositories on Linux. Its
normal service mode separates scheduling and state management from subprocess
execution. It also offers foreground commands for one-off synchronization.

## Runtime modes

```
                         master.sock
mirror tui/push/reload ───────────────▶ MasterServer
                                          │
mirror daemon                              │ schedules and tracks
  ├─ MasterServer                          ▼
  └─ WorkerClientSupervisor ─────────▶ sync plug-in
              ▲                           │ execute_command RPC
              │ job_finished              ▼
              └────────────────────── WorkerServer ──▶ subprocess
                                      mirror worker

mirror standalone ──▶ sync plug-in ──▶ foreground subprocess

mirror worker-execute ubuntu/jigdo ──▶ specialized foreground workflow
```

The master owns configuration, scheduling, package state, per-run logging, and
the client-facing RPC API. The worker owns subprocess lifetimes. If the master
restarts, a running subprocess remains under the worker. A supervised
`WorkerClient` reconnects with exponential backoff, and the worker retries
completion notifications for finished jobs.

`mirror standalone` activates the same sync plug-ins without either socket
server. Calls that would normally delegate to the worker instead use the
foreground process runner, and completion is returned to the CLI without
persisting daemon status files.

The `worker-execute` commands are another foreground path. They expose the
specialized Ubuntu and jigdo workflows used as subprocess entry points by those
daemon sync methods. The apt-mirror2 and debmirror methods use private Python
wrapper entry points instead, allowing repository discovery and native tool
execution to happen under the worker's identity and environment.

## Master-worker flow

1. The daemon loads the main configuration and runtime state, registers enabled
   plug-ins, initializes logging, and starts `MasterServer`.
2. Starting the master also starts `WorkerClientSupervisor`, which maintains a
   persistent connection to `worker.sock`.
3. Once per second, the daemon skips disabled and running packages, then checks
   `lastsync`, `syncrate`, and the error retry interval to decide what is due.
4. `mirror.sync.start()` records the running log and `SYNC` status, then invokes
   the selected sync plug-in in a daemon thread.
5. The plug-in validates settings, builds its command, and calls
   `mirror.socket.worker.execute_command()`. The worker starts the process with
   the configured UID, GID, environment, and log destination.
6. When the process exits, the worker broadcasts `job_finished`. The master
   calls `mirror.sync.on_sync_done()`, runs the plug-in completion hook when
   present, finalizes the log, changes the status to `ACTIVE` or `ERROR`, and
   persists state.

The daemon also reconciles package state with worker jobs. It repairs a package
whose worker job is running but whose status is stale, and eventually marks a
stale `SYNC` package as `ERROR` when the worker no longer has its job. An
optional global maximum runtime watchdog stops overlong worker jobs.

## Client-facing master RPC

The master socket serves the TUI and command-line control clients. Its handlers
provide health and runtime information, package listing, manual start and stop,
push-triggered synchronization, and configuration reload. The daemon writes the
active master socket path to `/var/run/mirror/master.sock.path`; clients prefer
that metadata when `--socket` is omitted.

The TUI keeps one `MasterClient` connection, polls package state once per
second, and performs manual start/stop RPCs outside the UI event loop. Its log
reader accepts only regular non-symlink files under the configured package log
root, supports plain and gzip files, and keeps bounded disk-backed windows while
paging large logs.

## Socket protocol

Master and worker IPC uses length-prefixed JSON frames over Unix domain
sockets. Every connection completes a three-step handshake—server information,
client information, then confirmation—before application messages are handled.
The framing supports request/response RPCs and asynchronous notifications on
the same persistent connection.

The two sockets have distinct roles:

- `master.sock` accepts control and status RPCs from CLI clients.
- `worker.sock` accepts process-management RPCs from the master and sends
  `job_finished` notifications back over connected clients.

## Plug-in lifecycle

The plug-in framework is active and has two loading phases:

1. Package import registers the built-in sync plug-ins so package validation
   knows every built-in method.
2. Configuration loading applies built-in enable/disable settings and discovers
   third-party entry points from `mirror.sync`, `mirror.event`, and
   `mirror.status`.

External plug-ins declare a `(major, minor)` API version. Incompatible major
versions and plug-ins that require a newer minor version are skipped. Sync
plug-ins supply execution hooks, event plug-ins register event handlers, and
status plug-ins can extend or transform status payloads or write additional
status outputs.

Per-plug-in JSON configuration is stored beside the main configuration and is
read lazily. A plug-in may expose a `create_config` callback, invoked explicitly
with `mirror plugin config create`; normal daemon startup never creates or
rewrites plug-in configuration.

## Module map

| Module | Responsibility |
|--------|----------------|
| `mirror/__main__.py` | Click entry point and top-level command registration. |
| `mirror/command/` | Service, control, TUI, plug-in, standalone, and worker-workflow commands. |
| `mirror/config/` | Main JSON loading, safe runtime reload, state persistence, and web status output. |
| `mirror/structure/` | Configuration, package, settings, and status data structures. |
| `mirror/socket/` | Framed Unix socket protocol plus master and worker clients and servers. |
| `mirror/sync/` | Built-in sync plug-ins, scheduling state, and completion handling. |
| `mirror/worker/` | Foreground and background subprocess lifecycle, log merging, and pruning. |
| `mirror/plugin/` | Built-in registration, external entry-point loading, API compatibility, and status hooks. |
| `mirror/event/` | Priority-based event publication and subscription. |
| `mirror/logger/` | Daemon and package logging, rotation, compression, and ownership. |
| `mirror/toolbox/` | Duration parsing, command lookup, and other shared utilities. |

## Persistence and paths

| Path | Purpose | Written during daemon runtime? |
|------|---------|--------------------------------|
| `/etc/mirror/config.json` | Operator-supplied main configuration | No |
| `/var/lib/mirror/stat.json` | Persistent package runtime state | Yes, by atomic replacement |
| `/var/run/mirror/` | PID, socket, and active socket-path metadata | Yes |
| `/var/log/mirror/` | Daemon and per-package logs | Yes |
| `/var/www/mirror/status.json` | Web-facing package status | Yes, by atomic replacement |

The main configuration is read-only after provisioning. `mirror setup` creates
it only when it is absent. Runtime status, error counts, log paths, and
timestamps belong in `stat.json`; web-facing state belongs in `status.json`.
Plug-in status outputs are separate files owned by their plug-ins.

## Built-in sync methods

| Method | Execution model |
|--------|-----------------|
| `rsync` | Validates rsync options, optionally checks FFTS metadata, then delegates rsync to the worker. |
| `ftpsync` | Creates a temporary archvsync environment, preferring a git clone and falling back to the bundled archive, then delegates `ftpsync` to the worker. |
| `lftp` | Builds a validated lftp mirror script and delegates it to the worker. |
| `bandersnatch` | Runs the PyPI mirror command through the worker. |
| `local` | Verifies that the authoritative local destination exists; no subprocess is needed. |
| `ubuntu` | Delegates the `worker-execute ubuntu` two-stage rsync workflow. |
| `jigdo` | Delegates the `worker-execute jigdo` template sync, image reconstruction, final ISO pull, and trace workflow. |
| `debmirror` | Builds a native debmirror command, discovers omitted distributions, sections, and architectures inside the worker wrapper, and runs with isolated debmirror configuration. |
| `apt-mirror2` | Resolves one or more APT repositories inside the worker wrapper, generates a temporary apt-mirror2 configuration, and runs the optional `apt-mirror==16` implementation. |
