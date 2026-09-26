# mirror.py

A Linux daemon for maintaining local mirrors of remote package repositories.
Schedule synchronization, track each repository, and inspect logs through one
master-worker service.

[Getting started](https://mirror-py.sparcs.org/getting-started/quickstart.html) ·
[Configuration](https://mirror-py.sparcs.org/guide/configuration.html) ·
[CLI reference](https://mirror-py.sparcs.org/guide/cli.html) ·
[Documentation](https://mirror-py.sparcs.org/)

## Features

- **Scheduled and push-triggered syncs.** Configure intervals per repository,
  retry failed jobs, or trigger a sync from the command line.
- **Separate scheduling and execution.** The master manages schedules and state;
  the worker runs subprocesses with configured UID/GID and keeps active jobs
  running when the master restarts.
- **Status and logs.** Inspect packages with the terminal UI, keep per-run logs
  with gzip compression, and publish status as JSON.
- **Configuration reloads.** Apply supported configuration changes to the running
  daemon without restarting it.
- **Standalone execution.** Run a single sync in the foreground without starting
  the daemon and worker services.
- **Extensible plugins.** Add sync methods, event handlers, and status fields
  through Python entry points.

## Supported sync methods

| Method | Purpose | Backend |
| --- | --- | --- |
| [rsync](https://mirror-py.sparcs.org/sync-methods/rsync.html) | Incremental file mirroring, with an optional upstream timestamp check | `rsync` |
| [ftpsync](https://mirror-py.sparcs.org/sync-methods/ftpsync.html) | Debian archive mirroring | Debian archvsync |
| [lftp](https://mirror-py.sparcs.org/sync-methods/lftp.html) | FTP mirroring with include/exclude filters | `lftp` |
| [bandersnatch](https://mirror-py.sparcs.org/sync-methods/bandersnatch.html) | PyPI package mirroring | `bandersnatch` |
| [local](https://mirror-py.sparcs.org/sync-methods/local.html) | Register an existing directory without copying files | No external tool |
| [ubuntu](https://mirror-py.sparcs.org/sync-methods/ubuntu.html) | Two-stage Ubuntu archive mirroring | `rsync` |
| [jigdo](https://mirror-py.sparcs.org/sync-methods/jigdo.html) | Reconstruct Debian CD/DVD images from jigdo metadata and a package mirror | `jigdo-mirror`, `jigdo-file`, and supporting tools |
| [debmirror](https://mirror-py.sparcs.org/sync-methods/debmirror.html) | Mirror a Debian-style archive with signed metadata discovery | `debmirror` |
| [apt-mirror2](https://mirror-py.sparcs.org/sync-methods/apt-mirror2.html) | Mirror multiple regular or flat APT repositories under one scheduled job | Python `apt-mirror` via the optional extra |

See each method's documentation for prerequisites, configuration examples,
selection rules, and signature verification requirements.

## Installation

Requires **Linux** and **Python 3.10 or later**.

### Install from PyPI

Install [mirror.py](https://pypi.org/project/mirror.py/) globally so the
`mirror` command is available to root and systemd provisioning:

```bash
sudo python3 -m pip install mirror.py
sudo mirror --version
```

For `apt-mirror2` support, install the optional extra globally as well:

```bash
sudo python3 -m pip install 'mirror.py[apt-mirror2]'
```

Some distributions mark their system Python as externally managed and reject
global pip installs. In that case, use the virtual-environment alternative in
the [installation guide](https://mirror-py.sparcs.org/getting-started/installation.html)
instead of overriding that protection.

### Install from source

For development or features not yet released on PyPI, use `uv`:

```bash
git clone https://github.com/sparcs-kaist/mirror.py.git
cd mirror.py
uv sync
source .venv/bin/activate
mirror --version
```

To include `apt-mirror2` in a source installation, run
`uv sync --extra apt-mirror2`. This README describes the source tree; an
installed release may differ. See the
[installation guide](https://mirror-py.sparcs.org/getting-started/installation.html)
for more details.

### System dependencies

Install the system tools required by your chosen sync methods. `mirror setup`
checks for **all three** of `rsync`, `lftp`, and `bandersnatch`.
Bandersnatch is installed with the Python package. For example, on Debian or
Ubuntu, install the other two with:

```bash
sudo apt install rsync lftp
```

Signed APT repositories require the relevant verification tools and trusted
keyrings described in the [debmirror](https://mirror-py.sparcs.org/sync-methods/debmirror.html) and
[apt-mirror2](https://mirror-py.sparcs.org/sync-methods/apt-mirror2.html) guides.

## Quickstart

### 1. Provision the host

The commands below assume the recommended global installation. For a source or
virtual-environment installation, activate the environment and run every root
CLI command through it, for example `sudo env "PATH=$PATH" mirror setup`. Use
the same prefix for manual `worker`, `daemon`, `tui`, and `config reload` calls.

```bash
sudo mirror setup
```

Setup creates the configuration, state, socket, log, and web directories, and
installs `mirror.service` and `mirror-worker.service`. It creates
`/etc/mirror/config.json` only when that file does not already exist. It also
installs Bash completion at
`/usr/local/share/bash-completion/completions/mirror`. Completion requires Bash
4.4 or later and the distribution's `bash-completion` package to be installed
and enabled; setup does not install that package or edit per-user shell files.
Open a new shell, then type `mirror t` and press Tab to complete `mirror tui`.

### 2. Configure a repository

Edit `/etc/mirror/config.json`. Set the mirror identity, maintainer details,
local timezone, and the non-root `settings.uid` / `settings.gid` used by sync
subprocesses. Replace the empty `packages` object with entries for your mirrors.
For example, this is a `packages` value for an rsync mirror:

```json
{
  "repository": {
    "id": "repository",
    "name": "Example repository",
    "href": "/repository",
    "synctype": "rsync",
    "syncrate": "PT6H",
    "link": [],
    "settings": {
      "hidden": false,
      "src": "rsync://upstream.example.org/repository/",
      "dst": "/srv/mirror/repository",
      "options": {}
    }
  }
}
```

Replace the placeholder upstream with your actual source. `PT6H` means every
six hours. Create the destination directory and grant the configured UID/GID
write access before starting the services. Sync methods can delete obsolete
files, so use a directory dedicated to the mirror.

The [quickstart guide](https://mirror-py.sparcs.org/getting-started/quickstart.html) provides a full
configuration. See the [configuration reference](config.md) and
[example configuration](config-example.json) for more options. The
[browser configuration editor](https://mirror-py.sparcs.org/guide/config-editor.html) can also generate
configuration files; it does not connect to or configure the daemon directly.

### 3. Start the worker and master

Use the systemd units installed by `mirror setup`. Enable both services at
boot, start them, and check their status:

```bash
sudo systemctl enable --now mirror-worker.service mirror.service
sudo systemctl status mirror-worker.service mirror.service
```

Both services read `/etc/mirror/config.json`. The master schedules syncs and
communicates with the worker over Unix sockets. See
[installation](https://mirror-py.sparcs.org/getting-started/installation.html)
for deployment details.

### 4. Inspect and control syncs

```bash
sudo mirror tui
sudo mirror config reload
```

The TUI shows package status and logs. `push` requests an immediate sync;
`config reload` applies supported changes after you edit the configuration.
Settings that require a restart are reported as warnings.

For one-off jobs, use `mirror standalone SYNCTYPE`. See the
[CLI reference](https://mirror-py.sparcs.org/guide/cli.html) for arguments, options, and TUI key bindings.

## Configuration, state, and serving files

| Default path | Contents |
| --- | --- |
| `/etc/mirror/config.json` | Operator-managed configuration; read-only during daemon and worker runtime |
| `/var/lib/mirror/stat.json` | Persisted package status and sync history fields |
| `/var/run/mirror/` | Unix sockets and runtime metadata |
| `/var/log/mirror/` | Daemon and per-package logs |
| `/var/www/mirror/status.json` | Generated web status |

Repository files are stored in each package's `settings.dst`. Configure your
own HTTP, FTP, or rsync server to publish those files and, if needed, the status
JSON. mirror.py handles synchronization and status generation; serving the
mirror is a separate deployment step.

See [state files](https://mirror-py.sparcs.org/guide/state-files.html),
[architecture](https://mirror-py.sparcs.org/architecture/overview.html), and
[troubleshooting](https://mirror-py.sparcs.org/guide/troubleshooting.html) for operational details.

## Development

```bash
uv sync
uv run pytest
uv run pytest -m integration -v
```

The default pytest run excludes integration tests. The integration suite needs
Docker with Compose and builds containers using the current source tree. Run
the suites sequentially; see the [integration guide](tests/integration/README.md)
for host requirements and fixture behavior.

For documentation and configuration editor builds, see
[Contributing](https://mirror-py.sparcs.org/contributing/index.html). For extensions, see the
[plugin author guide](https://mirror-py.sparcs.org/plugins/index.html) and the
[example plugin](examples/mirror-plugin-echo/).

Report bugs and feature requests through
[GitHub Issues](https://github.com/sparcs-kaist/mirror.py/issues).

## License

[Apache License 2.0](LICENSE). Maintained by [SPARCS](https://sparcs.org/) at KAIST.

Contact: [ftp@ftp.kaist.ac.kr](mailto:ftp@ftp.kaist.ac.kr).
