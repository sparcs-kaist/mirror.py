# Mirror.py

A Python daemon that mirrors remote repositories to a local server using various sync protocols (rsync, ftpsync, lftp, bandersnatch).

## Project Goal

Provide a reliable, extensible system for maintaining local mirrors of remote package repositories (e.g., Debian, PyPI). Runs as a master-worker daemon pair on Linux, with scheduled syncs, per-package logging, and web-accessible status reporting.

## Architecture

Master-worker model with Unix domain socket IPC:

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

## Module Map

| Module | Responsibility |
|--------|---------------|
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

## Socket IPC Protocol

- Length-prefixed JSON over Unix domain sockets
- 3-step handshake: server info → client info → confirmation
- Bidirectional: RPC (command/response) + async notifications (job_finished)
- Master holds persistent WorkerClient connection to receive notifications

## Sync Flow

1. Master detects package needs sync (based on `syncrate` ISO 8601 duration)
2. `mirror.sync.start(package)` launches sync in a daemon thread
3. Sync module (e.g., rsync) builds command and calls `mirror.socket.worker.execute_command()`
4. Worker spawns subprocess, Master receives `job_finished` notification on completion
5. Package status updated: ACTIVE (success) or ERROR (failure)

## Active Sync Methods

- **rsync**: Incremental sync with optional FFTS pre-check
- **ftpsync**: Debian archvsync-based FTP mirroring

## Path Layout

| Path | Purpose | Writable by daemon? |
|------|---------|---------------------|
| `/etc/mirror/config.json` | Main configuration | **No — read-only at runtime** |
| `/var/lib/mirror/stat.json` | Persistent package state | Yes (atomic rewrite on status change) |
| `/var/run/mirror/` | Sockets (master.sock, worker.sock), PID files | Yes |
| `/var/log/mirror/` | Daemon logs, per-package logs under `packages/` | Yes |
| `/var/www/mirror/status.json` | Web status JSON for the UI | Yes |

### Config invariant

`/etc/mirror/config.json` is **read-only** during daemon and worker runtime.
Only `mirror setup` ever writes it (initial provisioning). Runtime state — sync
status, error counts, log paths, timestamps — lives exclusively in `stat.json`.
When adding a new persisted field:

- DO: extend `Package.StatusInfo` (or another stat-side dataclass) and emit it via
  `Package.to_dict()` so `save_stat_data()` picks it up automatically.
- DO: surface it in `generate_and_save_web_status()` if the UI needs it.
- DON'T: never call `mirror.confPath.write_text(...)` or otherwise mutate the
  user-supplied config.json. There is intentionally no `Config.save()`.

## Development

- Python >= 3.10, Linux only
- Package manager: `uv`
- Dependencies: click, prompt_toolkit, bandersnatch (optional)
- Tests: `uv run pytest tests/`
- Install for dev: `uv pip install -e .`

## Commands

```bash
mirror setup                      # Provision directories and systemd units
mirror daemon [--config PATH]     # Run master daemon
mirror worker [--config PATH]     # Run worker server
mirror crontab -u USER -c CONFIG  # Generate crontab entries
```

## Agent Instructions

### Codebase Changes

- When modifying the codebase at the user's request, commit directly with `--no-gpg-sign` (interactive GPG signing is unavailable in this environment).
- By default, create a dedicated branch for any feature or fix request before making changes. Follow the `feat/<name>` or `fix/<name>` naming convention.

### RC Release Bump

When asked to bump the project to a new release candidate version, perform only the release-version update unless the user explicitly requests more. Unless the user says otherwise, follow this order:

1. Run the full test suite first, including the default non-integration tests and the integration tests, for example `uv run pytest` and `uv run pytest -m integration -v`.
2. After the tests pass, update the version in the project metadata and source version files, including `pyproject.toml`, `mirror/__init__.py`, and `uv.lock` when applicable. Modify only the version-related code.
3. Verify the updated version, for example with `uv run python -c "import mirror; print(mirror.__version__)"`.
4. Show the changed files and ask the user to create the commit. The user makes the release commit themselves; do not commit the version bump. Suggest a Conventional Commit message, for example `chore: prepare 1.0.0rc15 release`.
5. After the user confirms the commit is done, create the matching git tag, for example `v1.0.0rc15`, and push.
