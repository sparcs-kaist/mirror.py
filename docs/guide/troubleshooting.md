# Troubleshooting

## Where logs live

**Daemon log** — its path is built from `settings.logger.fileformat` using
`base`, `folder`, and `filename`. With the configuration created by setup:

```
/var/log/mirror/<year>/<month>/<year>-<month>-<day>.log
```

When gzip compression is enabled, rotated files are stored as `.log.gz`.

**Per-package logs** — each sync run produces a separate file using
`settings.logger.packagefileformat`. Setup's default filename starts with the
package ID:

```
/var/log/mirror/packages/<year>/<month>/<day>/<pkgid>.<HH>:<MM>:<SS>.<us>.log
```

The quickstart example uses a different package filename template. Always use
the paths configured on your host. `settings.logfolder` controls ftpsync tool
logs rather than these daemon and per-package logs.

The paths of the most recent error and success logs for each package are stored
in `stat.json` under `packages.<pkgid>.status.statusinfo.lasterrorlog` and
`packages.<pkgid>.status.statusinfo.lastsuccesslog`.

---

## Checking package status

**Web status file** — `/var/www/mirror/status.json` is updated when package
status changes, including sync start and completion. Inspect it directly or
serve it with a web server:

```bash
cat /var/www/mirror/status.json | python3 -m json.tool
```

Look at the `status` field for each package: `ACTIVE` means the last sync
succeeded; `ERROR` means it failed; `SYNC` means a sync is currently running.

**TUI** — run the built-in terminal status interface to see live package states:

```bash
mirror tui
```

**stat.json** — `/var/lib/mirror/stat.json` holds the authoritative runtime
state. Check `statusinfo.errorcount` for the number of consecutive failures and
`statusinfo.lasterrorlog` for the path to the most recent error log.

---

## Common issues

### Package stays in ERROR status

1. Open the most recent error log (`statusinfo.lasterrorlog` in `stat.json`).
2. Look for the error message from the sync tool (rsync exit code, ftpsync
   error, etc.).
3. Check that the upstream source (`settings.src` in `config.json`) is
   reachable from the server.
4. Verify that the destination directory (`settings.dst`) exists and is
   writable by the UID/GID configured in `settings.uid` / `settings.gid`.

The daemon will continue retrying after the number of seconds specified in
`settings.errorcontinuetime`.

### Package is stuck in SYNC

A sync subprocess may have hung or been killed without notifying the worker.

1. Check whether the sync process is still running:
   ```bash
   ps aux | grep mirror
   ```
2. Inspect the running log file (`statusinfo.runninglog` in `stat.json`) for
   recent output.
3. If the process is no longer running but the status has not been updated,
   check the daemon log for reconciliation messages. Persisted `SYNC` states
   survive restart; when the worker reports no matching job, the daemon waits
   for its mismatch/setup grace periods and transitions the package to `ERROR`.

### Permission or UID/GID problems

The worker spawns sync subprocesses as the UID and GID set in
`settings.uid` / `settings.gid`. Ensure:

- The destination directory is owned or writable by that user and group.
- Per-package log paths configured in `settings.logger.packagefileformat`, and
  ftpsync tool logs under `settings.logfolder`, are writable by that user and group.
- The daemon can write to the path configured in `settings.logger.fileformat`.
- The daemon process itself has permission to write to `/var/lib/mirror/` and
  `/var/www/mirror/`.

A warning is logged at startup if `uid` or `gid` is 0 (root). Running syncs as
root is not recommended.

### Socket path issues

The master and worker communicate over Unix domain sockets under
`/var/run/mirror/`. If the daemon or worker cannot connect:

1. Verify that `/var/run/mirror/` exists and is writable by the daemon user:
   ```bash
   ls -la /var/run/mirror/
   ```
2. Check for stale socket files from a previous run and remove them if the
   process is no longer running.
3. If you use a custom socket directory via `settings.socket_path`,
   `mirror config reload` and `mirror tui` normally discover it from the
   daemon's runtime metadata. Use `--socket PATH` pointing to `master.sock`
   to override that path when needed, for example if the metadata is unavailable.
   Configure a directory for both daemons; a shared value ending in `.sock`
   makes the master and worker attempt to use the same socket file.

### Missing external sync tool binary

If a sync method fails immediately with an error about a missing executable:

1. Confirm the binary is installed and on the system `PATH`:
   ```bash
   which rsync
   which lftp
   which debmirror
   which jigdo-mirror
   ```
2. Install the missing tool via your system package manager.
3. For `bandersnatch`, it is a Python dependency and should be installed
   automatically. In a source checkout, run `uv sync` in the environment used
   by the daemon and worker.

`ftpsync` is prepared in a temporary directory for each job; it does not need
to be on `PATH`. Check the package log for provisioning failures and ensure
`rsync` is installed. `apt-mirror2` uses the Python `apt_mirror` module rather
than a system `apt-mirror` executable. Install the `apt-mirror2` extra in the
Python environment used by the daemon and worker; the daemon checks its
availability before delegation. Signature verification also needs `gpgv`.

---

## Reloading configuration

If you change `config.json` while the daemon is running, send a reload request
without restarting:

```bash
mirror config reload
```

The daemon applies the new configuration and reports which packages were added,
removed, or modified. Packages that were not changed continue syncing without
interruption.

Socket path changes require a daemon restart. Reload keeps the current
`settings.socket_path` and reports a warning; it does not move the running
socket. Without `--socket`, client commands use the recorded runtime path.

Pass `--timeout` to extend the wait time for large configs:

```bash
mirror config reload --timeout 60
```
