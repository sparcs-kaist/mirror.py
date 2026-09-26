# CLI reference

All commands use the `mirror` entry point. Run `mirror --help` for the command
list and `mirror COMMAND --help` for command-specific usage.

## Global options

### mirror --version

```
mirror --version
```

Print the installed version and exit.

## Service commands

### mirror setup

```
mirror setup
```

Provision a Linux host for the daemon and worker. This command must run as
root. It verifies the required `rsync`, `lftp`, and `bandersnatch` executables,
creates the runtime, state, log, web, and configuration directories, and writes
the `mirror.service` and `mirror-worker.service` systemd units. It also runs
`systemctl daemon-reload` when `systemctl` is available. The `mirror` executable
must be on root's `PATH`; the recommended global installation satisfies this
when `sudo mirror --version` succeeds.

If `/etc/mirror/config.json` does not exist, `setup` creates it from the built-in
default. An existing configuration is preserved.

Setup installs Bash completion at
`/usr/local/share/bash-completion/completions/mirror`. Completion requires Bash
4.4 or later and the distribution's `bash-completion` package to be installed
and enabled. Setup does not install that package or edit per-user shell startup
files. Open a new shell after setup; `mirror t` followed by Tab completes to
`mirror tui`, and completion also covers command options such as
`mirror daemon --config`. Running setup again refreshes the installed completion
script. If completion installation fails, setup prints a warning and continues
without blocking the rest of host provisioning.

### mirror daemon

```
mirror daemon [--config PATH]
```

Run the master daemon. It loads the configuration, starts the master Unix socket
server, maintains a supervised connection to the worker, and checks once per
second whether each package is due for synchronization.

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | `/etc/mirror/config.json` | Main configuration file. |

### mirror worker

```
mirror worker [--config PATH]
```

Run the worker server. The worker receives command RPCs, starts subprocesses
with the configured UID and GID, tracks them independently of the master, and
reports completion to connected master clients.

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | `/etc/mirror/config.json` | Main configuration file. The worker reads logging and socket settings from it. |

### mirror crontab

```
mirror crontab [-u USER] [-c CONFIG]
```

This command is currently a compatibility placeholder and produces no crontab
output.

| Option | Default | Description |
|--------|---------|-------------|
| `-u`, `--user USER` | `root` | Accepted for compatibility. |
| `-c`, `--config PATH` | `/etc/mirror/config.json` | Accepted for compatibility. |

## Daemon control commands

### mirror push

```
mirror push PACKAGEID [--config PATH]
```

Ask the running master to start a push-triggered sync for `PACKAGEID`. The
command forwards `SSH_ORIGINAL_COMMAND` and `SSH_CONNECTION` when present, so it
can be used as the forced command for an upstream push. A request for an already
running package succeeds with `already_running` status.

The master socket is read from `/var/run/mirror/master.sock.path` and otherwise
falls back to the default master socket. The accepted `--config` option is not
currently used for socket resolution.

| Argument / option | Default | Description |
|-------------------|---------|-------------|
| `PACKAGEID` | required | Package ID from the running daemon configuration. |
| `--config PATH` | `/etc/mirror/config.json` | Accepted for compatibility; currently unused. |

### mirror config reload

```
mirror config reload [--socket PATH] [--timeout SECONDS]
```

Ask the running master to reload its configuration. The result reports added,
removed, and modified packages plus any warnings. Settings that cannot be
changed safely at runtime remain at their current values and require a daemon
restart.

| Option | Default | Description |
|--------|---------|-------------|
| `--socket PATH` | Runtime metadata, then the default master socket | Explicit master socket path. |
| `--timeout SECONDS` | `30` | Seconds to wait for the daemon main loop to apply the reload. |

### mirror tui

```
mirror tui [--socket PATH]
```

Open the full-screen status UI. It polls the master once per second, shows
status counts and per-package timing, and follows the selected package's running
log or most recent completed log. Plain and gzip-compressed logs are loaded in
bounded, pageable windows. Columns adapt to the terminal width, and packages
from external sync plug-ins remain visible even when the TUI process has not
loaded that plug-in.

| Option | Default | Description |
|--------|---------|-------------|
| `--socket PATH` | Runtime metadata, then the default master socket | Explicit master socket path. |

Key bindings:

| Key | Action |
|-----|--------|
| `j`, `k`, up/down arrows | Move in the focused package or log pane. |
| `g`, `G`, `Home`, `End` | Jump to the first or last package, or the physical start or end of the focused log. |
| `PageUp`, `PageDown` | Page through the focused log, loading another disk window when needed. |
| `x` | Confirm and start or stop the selected package. |
| `l` | Toggle the log pane. |
| `Tab` | Move focus between the package table and log pane. |
| `s` | Cycle through default, status, last-success age, and package-ID sorting. |
| `/` | Edit the case-insensitive package-ID filter; `Enter` or `Esc` closes the input. |
| `p` | Pause or resume status polling. |
| `r` | Redraw the display. |
| `?` | Toggle the help overlay. |
| `q`, `Ctrl-C` | Exit. |

## Plug-in commands

### mirror plugin config create

```
mirror plugin config create PLUGIN [--config PATH] [--force | --no-force]
```

Load the main configuration, discover enabled external plug-ins, and call the
named plug-in's `create_config` callback. The plug-in owns the output path and
file contents. Existing files are skipped unless `--force` is supplied. Built-in
or external plug-ins without a `create_config` callback are rejected.

| Argument / option | Default | Description |
|-------------------|---------|-------------|
| `PLUGIN` | required | Registered plug-in name. |
| `--config PATH` | `/etc/mirror/config.json` | Main configuration used to discover plug-ins and resolve their settings. |
| `--force`, `--no-force` | `--no-force` | Allow the plug-in to overwrite its existing config file. |

## Standalone synchronization

### mirror standalone

```
mirror standalone SYNCTYPE [OPTIONS]
```

Run one sync in the foreground without the master daemon or worker server. The
command constructs an ad hoc package, dispatches it through the normal sync
plug-in, and executes delegated subprocesses in the current process. It returns
zero on success and the subprocess return code, or `1`, on failure. Standalone
runs do not write daemon `stat.json` or web status data.

Built-in values of `SYNCTYPE` are `rsync`, `ftpsync`, `lftp`, `bandersnatch`,
`local`, `ubuntu`, `jigdo`, `debmirror`, and `apt-mirror2`. The standalone
command currently validates against the built-in registry before loading
`--config`, so it does not discover external sync plug-ins.

| Argument / option | Default | Description |
|-------------------|---------|-------------|
| `SYNCTYPE` | required | Registered sync plug-in name. |
| `--src VALUE` | empty | Sync source URL or path. |
| `--dst VALUE` | empty | Local destination directory. |
| `-o`, `--option KEY=VALUE` | none | Set a sync option. Repeat `key[]=value` to build a list. Boolean strings and integers are converted to their JSON scalar types. |
| `--options-json JSON` | none | JSON object merged after `-o`; its keys take precedence. |
| `--uid INTEGER` | Current UID | User ID used by delegated subprocesses. |
| `--gid INTEGER` | Current GID | Group ID used by delegated subprocesses. |
| `--nice INTEGER` | `0` | Accepted niceness value. The current command does not propagate this value to the ad hoc package. |
| `--config PATH` | none | Load global settings such as hostname and ftpsync settings from an existing main configuration instead of using temporary defaults. It does not discover external plug-ins. |
| `--id PACKAGEID` | `standalone` | Internal package/job ID. |
| `--state-dir PATH` | Writable `/var/lib/mirror`, otherwise a temporary directory | State and temporary workspace, notably for `ftpsync`. The path is created when explicitly supplied. |

For example:

```
mirror standalone rsync \
  --src rsync://mirror.example/repository \
  --dst /srv/mirror/repository \
  -o exclude[]=project/trace \
  -o ffts=false
```

## Worker-side workflow commands

The `worker-execute` group contains specialized foreground workflows used by
the `ubuntu` and `jigdo` sync plug-ins. They do not connect to the daemon or
worker socket.

`apt-mirror2` and `debmirror` also perform repository discovery in the worker's
environment, but they use private Python wrapper entry points created by their
sync modules. They are not `mirror worker-execute` subcommands.

### mirror worker-execute ubuntu

```
mirror worker-execute ubuntu --src URL --dst PATH [OPTIONS]
```

Run the two-stage Ubuntu archive workflow: first copy data while excluding
archive metadata, then copy metadata and apply deletions.

| Option | Default | Description |
|--------|---------|-------------|
| `--src URL` | required | Rsync source URL or path. |
| `--dst PATH` | required | Local destination directory. |
| `--trace`, `--no-trace` | `--trace` | Write `<dst>/project/trace/<hostname>` after success. |
| `--trace-hostname HOSTNAME` | `socket.getfqdn()` | Trace filename hostname. |
| `--extra-rsync-arg ARG` | none | Argument appended to both rsync stages; repeatable. |
| `--stage1-exclude PATTERN` | Built-in metadata patterns | Stage-one exclude; repeatable. Supplying any value replaces all built-in defaults. |

### mirror worker-execute jigdo

```
mirror worker-execute jigdo --src URL --dst PATH \
  --jigdo-file CMD --debian-mirror URL [OPTIONS]
```

Run the Debian CD workflow: sync jigdo templates, reconstruct images with
`jigdo-mirror`, fetch the configured final ISO subset, and optionally write a
trace file.

| Option | Default | Description |
|--------|---------|-------------|
| `--src URL` | required | Rsync source URL or path for the Debian CD tree. |
| `--dst PATH` | required | Local `data/` root. |
| `--jigdo-file CMD` | required | Value written to `jigdoFile=` in `jigdo-mirror.conf`. |
| `--debian-mirror URL` | required | Value written to `debianMirror=`; normally a local Debian package mirror. |
| `--hostname HOSTNAME` | `socket.getfqdn()` | Hostname used by excludes and, unless overridden, the trace filename. |
| `--timeout SECONDS` | `7200` | Rsync timeout. |
| `--trace`, `--no-trace` | `--trace` | Write a trace file after success. |
| `--trace-path PATH` | `project/trace` | Trace directory relative to `dst`. |
| `--trace-hostname HOSTNAME` | Value of `--hostname` | Override the trace filename hostname. |
| `--template-exclude PATTERN` | `*.iso` | Phase-one exclude; repeatable. Supplying any value replaces the default. |
| `--final-include PATTERN` | Built-in businesscard, netinst, and i386 patterns | Final ISO include; repeatable. Supplying any value replaces all defaults. |
| `--extra-rsync-arg ARG` | none | Argument appended to both rsync phases; repeatable. |
| `--rsync-bin PATH` | `rsync` | Rsync executable. |
| `--jigdo-mirror-bin PATH` | `jigdo-mirror` | Jigdo mirror executable. |
