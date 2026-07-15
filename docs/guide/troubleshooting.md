# Troubleshooting

## Where logs live

**Daemon log** — written to the path configured in `settings.logfolder`. With
the default layout:

```
/var/log/mirror/<year>/<month>/<date>.log
```

When gzip compression is enabled, rotated files are stored as `.log.gz`.

**Per-package logs** — each sync run produces a separate log file:

```
/var/log/mirror/packages/<year>/<month>/<day>/<HH>:<MM>:<SS>.<us>.<pkgid>.log
```

The paths of the most recent error and success logs for each package are stored
in `stat.json` under `statusinfo.lasterrorlog` and `statusinfo.lastsuccesslog`.

---

## Checking package status

**Web status file** — `/var/www/mirror/status.json` is updated after every sync
completes. Inspect it directly or serve it with a web server:

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
   restart the daemon and worker. On restart, packages in `SYNC` state are
   reset to `UNKNOWN`.

### Permission or UID/GID problems

The worker spawns sync subprocesses as the UID and GID set in
`settings.uid` / `settings.gid`. Ensure:

- The destination directory is owned or writable by that user and group.
- The log directory (`settings.logfolder`) is writable by that user and group.
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
3. If you use a custom socket path via `settings.socket`, ensure the
   `mirror config reload` and `mirror tui` commands use the same path via
   `--socket`.

### Missing external sync tool binary

If a sync method fails immediately with an error about a missing executable:

1. Confirm the binary is installed and on the system `PATH`:
   ```bash
   which rsync
   which ftpsync
   which lftp
   which jigdo-mirror
   ```
2. Install the missing tool via your system package manager.
3. For `bandersnatch`, it is a Python dependency and should be installed
   automatically; run `uv pip install -e .` to ensure it is present.

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

If the socket path was changed in the config, the reload command uses the
previously recorded socket path (from the runtime metadata file) so it can
still reach the running daemon. After the reload, subsequent commands will use
the new path.

Pass `--timeout` to extend the wait time for large configs:

```bash
mirror config reload --timeout 60
```
