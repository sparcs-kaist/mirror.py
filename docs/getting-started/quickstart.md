# Quickstart

This walkthrough gets a single package syncing in under ten minutes.

## 1. Provision the runtime directories

Complete the [installation steps](installation.md), including the required tools,
then run the setup command once as root. It creates `/etc/mirror/`, `/var/lib/mirror/`,
`/var/run/mirror/`, `/var/log/mirror/`, and `/var/www/mirror/`, and installs the
systemd unit files.

The commands below assume the recommended global installation. For a source or
virtual-environment installation, activate the environment and run every root
CLI command through it, for example `sudo env "PATH=$PATH" mirror setup`. Use
the same prefix for manual `worker`, `daemon`, and `tui` calls below.

```bash
sudo mirror setup
```

Setup also installs Bash completion at
`/usr/local/share/bash-completion/completions/mirror`. It requires Bash 4.4 or
later and an installed, enabled `bash-completion` package; setup does not
install that package or edit shell startup files. Open a new shell after setup,
then type `mirror t` and press Tab to complete `mirror tui`.

## 2. Create the configuration file

Edit the `/etc/mirror/config.json` created by setup. The example below mirrors Rocky Linux via
rsync every 10 minutes.

```json
{
    "mirrorname": "My Mirror",
    "hostname": "mirror.example.com",
    "settings": {
        "logfolder": "/var/log/mirror",
        "webroot": "/var/www/mirror",
        "statusfile": "/var/www/mirror/status.json",
        "statfile": "/var/lib/mirror/stat.json",
        "uid": 1000,
        "gid": 1000,
        "localtimezone": "UTC",
        "errorcontinuetime": 60,
        "max_runtime": "PT12H",
        "maintainer": {
            "name": "Your Name",
            "email": "you@example.com"
        },
        "logger": {
            "level": "INFO",
            "packagelevel": "ERROR",
            "format": "[%(asctime)s] %(levelname)s # %(message)s",
            "packageformat": "[%(asctime)s][{package}] %(levelname)s # %(message)s",
            "fileformat": {
                "base": "/var/log/mirror",
                "folder": "{year}/{month}",
                "filename": "{year}-{month}-{day}.log",
                "gzip": true
            },
            "packagefileformat": {
                "base": "/var/log/mirror/packages",
                "folder": "{year}/{month}/{day}",
                "filename": "{hour}:{minute}:{second}.{microsecond}.{packageid}.log",
                "gzip": true
            }
        },
        "plugins": {}
    },
    "packages": {
        "rocky-linux": {
            "name": "Rocky Linux",
            "id": "rocky-linux",
            "href": "/pub/rocky",
            "synctype": "rsync",
            "syncrate": "PT10M",
            "link": [
                {
                    "rel": "HOME",
                    "href": "https://rockylinux.org/"
                }
            ],
            "settings": {
                "hidden": false,
                "src": "rsync://msync.rockylinux.org/rocky-linux",
                "dst": "/srv/ftp/rocky-linux",
                "options": {
                    "user": "",
                    "password": ""
                }
            }
        }
    }
}
```

Key fields to change for your environment:

- `uid` / `gid` — the user and group that sync subprocesses run as. Avoid 0 (root).
- `settings.dst` — the local directory where the mirror is stored.
- `settings.src` — the upstream rsync URL.
- `syncrate` — ISO 8601 duration, for example `PT10M` (10 minutes) or `PT6H` (6 hours).

Create the destination directory and grant the configured UID/GID write access
before starting the worker. The daemon and worker run as root in this example;
only sync subprocesses drop to the configured UID/GID.

For full configuration details see [Configuration](../guide/configuration.md) and the
[Sync methods](../sync-methods/index.md) section.

## 3. Start the worker

The worker server spawns and monitors the actual sync subprocesses. Start it in
one terminal (or as a systemd unit):

```bash
sudo mirror worker
```

By default it reads `/etc/mirror/config.json`. Pass `--config` to use a
different path.

## 4. Start the daemon

The master daemon schedules syncs and delegates them to the worker. Start it in
a second terminal:

```bash
sudo mirror daemon
```

The daemon connects to the worker via Unix domain sockets under
`/var/run/mirror/`.

## 5. Check status

**Log files** are written under `/var/log/mirror/`:

- Daemon log: `/var/log/mirror/<year>/<month>/<date>.log`
- Per-package logs: `/var/log/mirror/packages/<year>/<month>/<day>/`

**Web status** is written to `/var/www/mirror/status.json` after each sync
completes. Serve that directory with any HTTP server to expose package status
to users.

**TUI**: Run the real-time status terminal UI:

```bash
sudo mirror tui
```

## Next steps

- [Configuration](../guide/configuration.md) — full reference for `config.json`
- [CLI reference](../guide/cli.md) — all available commands
- [State files](../guide/state-files.md) — what `stat.json` and `status.json` contain
