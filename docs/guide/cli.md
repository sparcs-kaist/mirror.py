# CLI reference

All commands are invoked through the `mirror` entry point. Run `mirror --help`
for a summary of available commands.

---

## mirror --version

```
mirror --version
```

Print the installed version and exit.

---

## mirror setup

```
mirror setup
```

Provision the runtime directories (`/etc/mirror/`, `/var/lib/mirror/`,
`/var/run/mirror/`, `/var/log/mirror/`, `/var/www/mirror/`) and install the
systemd unit files. Run once as root before starting the daemon or worker.

---

## mirror daemon

```
mirror daemon [--config PATH]
```

Run the master daemon. The daemon loads the configuration file, starts the
master Unix socket server, connects to the worker as a persistent client, and
enters a scheduling loop that triggers syncs according to each package's
`syncrate`.

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | `/etc/mirror/config.json` | Path to the configuration file. |

---

## mirror worker

```
mirror worker [--config PATH]
```

Run the worker server. The worker listens on a Unix domain socket, receives
`execute_command` RPCs from the master, spawns the corresponding sync
subprocess with the configured UID/GID/nice, and sends `job_finished`
notifications back to the master on completion.

| Option | Default | Description |
|--------|---------|-------------|
| `--config PATH` | `/etc/mirror/config.json` | Path to the configuration file. |

---

## mirror crontab

```
mirror crontab -u USER -c CONFIG
```

Generate crontab entries for the `mirror daemon` and `mirror worker` commands
suitable for the given user. Writes to standard output.

| Option | Default | Description |
|--------|---------|-------------|
| `-u`, `--user USER` | `root` | User to run the cron jobs as. |
| `-c`, `--config PATH` | `/etc/mirror/config.json` | Path to the configuration file. |

---

## mirror push

```
mirror push PACKAGEID [--config PATH]
```

Trigger a one-shot push sync of the specified package without waiting for its
next scheduled interval. `PACKAGEID` must match a key in the `packages` section
of the configuration file.

| Argument / Option | Description |
|-------------------|-------------|
| `PACKAGEID` | ID of the package to sync (required). |
| `--config PATH` | Path to the configuration file (default: `/etc/mirror/config.json`). |

---

## mirror tui

```
mirror tui [--socket PATH]
```

Open the real-time mirror status terminal UI. Connects to the running master
daemon via its Unix socket to display live package statuses.

| Option | Default | Description |
|--------|---------|-------------|
| `--socket PATH` | Resolved from runtime metadata | Master socket path. When omitted, the socket path is read from the runtime metadata file written by the daemon at startup. |

---

## mirror config reload

```
mirror config reload [--socket PATH] [--timeout SECONDS]
```

Ask the running master daemon to reload its configuration from disk. The daemon
applies the new config while continuing to serve requests; packages that were
added, removed, or modified are reported in the command output.

| Option | Default | Description |
|--------|---------|-------------|
| `--socket PATH` | Resolved from runtime metadata | Master socket path. |
| `--timeout SECONDS` | `30` | Seconds to wait for the daemon to apply the reload. |

---

## mirror worker-execute ubuntu

```
mirror worker-execute ubuntu --src URL --dst PATH [OPTIONS]
```

Internal/advanced. Run the two-stage Ubuntu archive sync directly, without the
daemon or worker server. Intended for use in standalone cron jobs or scripts.

| Option | Default | Description |
|--------|---------|-------------|
| `--src URL` | required | rsync source URL (e.g. `rsync://kr.archive.ubuntu.com/ubuntu`). |
| `--dst PATH` | required | Local destination directory. |
| `--trace` / `--no-trace` | `--trace` | Write a trace file to `<dst>/project/trace/<hostname>` on success. |
| `--trace-hostname HOSTNAME` | `socket.getfqdn()` | Override the hostname used in the trace filename. |
| `--extra-rsync-arg ARG` | none | Extra argument appended to both rsync stages. Repeatable. |
| `--stage1-exclude PATTERN` | built-in defaults | Exclude pattern for stage 1 (metadata-free pass). Repeatable. Overrides built-in defaults when supplied. |

---

## mirror worker-execute jigdo

```
mirror worker-execute jigdo --src URL --dst PATH --jigdo-file CMD --debian-mirror URL [OPTIONS]
```

Internal/advanced. Run the Debian CD jigdo mirror workflow directly, without
the daemon or worker server. The workflow syncs jigdo templates via rsync,
regenerates ISOs with `jigdo-mirror`, then pulls a final small set of real
ISO images.

| Option | Default | Description |
|--------|---------|-------------|
| `--src URL` | required | rsync source URL for the Debian CD jigdo tree. |
| `--dst PATH` | required | Local destination directory (the `data/` root). |
| `--jigdo-file CMD` | required | Value for the `jigdoFile=` line in `jigdo-mirror.conf`. |
| `--debian-mirror URL` | required | Local Debian package mirror for `debianMirror=` (e.g. `file:/mirror/ftp/debian`). |
| `--hostname HOSTNAME` | `socket.getfqdn()` | Hostname for trace excludes and trace filename. |
| `--timeout SECONDS` | `7200` | `rsync --timeout` value in seconds. |
| `--trace` / `--no-trace` | `--trace` | Write a trace file on success. |
| `--trace-path PATH` | `project/trace` | Subdirectory under `dst` for the trace file. |
| `--trace-hostname HOSTNAME` | mirrors `--hostname` | Override the trace filename hostname. |
| `--template-exclude PATTERN` | `*.iso` | Extra rsync exclude for phase 1 template sync. Repeatable. |
| `--final-include PATTERN` | built-in defaults | rsync include pattern for the final ISO pull. Repeatable. |
| `--extra-rsync-arg ARG` | none | Extra argument appended to both rsync phases. Repeatable. |
| `--rsync-bin PATH` | `rsync` | rsync executable path. |
| `--jigdo-mirror-bin PATH` | `jigdo-mirror` | `jigdo-mirror` executable path. |
