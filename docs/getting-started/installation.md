# Installation

## Requirements

- Python 3.10 or later
- Linux (the daemon relies on Unix domain sockets and Linux process management)

## Installing a release

Releases are available on [PyPI](https://pypi.org/project/mirror.py/). For a
daemon host, install the package globally so the `mirror` command is available
to root and systemd provisioning:

```bash
sudo python3 -m pip install mirror.py
sudo mirror --version
```

For `apt-mirror2` support, install the optional extra globally as well:

```bash
sudo python3 -m pip install 'mirror.py[apt-mirror2]'
```

Some distributions mark their system Python as externally managed and reject
global pip installs. Do not override that protection. Create a dedicated
virtual environment instead and expose its `mirror` command while provisioning:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install mirror.py
sudo env "PATH=$PATH" mirror setup
```

Setup records the resolved executable path in the systemd units, so the
virtual environment does not need to remain active when the services start.

## Installing from source

**Development install (editable):**

```bash
git clone https://github.com/sparcs-kaist/mirror.py.git
cd mirror.py
uv sync
source .venv/bin/activate
```

This registers the `mirror` CLI entry point in the virtual environment. Keep that
environment activated to run `mirror` from any directory.

**Standard source install:**

```bash
uv pip install .
```

## CLI entry point

After installation the `mirror` command is available on your `PATH`:

```bash
mirror --version
```

The entry point is defined in `pyproject.toml` as:

```
mirror = "mirror.__main__:main"
```

## Provisioning directories and systemd units

Before running the daemon or worker, run the setup command once to create the
required directories and install the systemd unit files:

```bash
sudo mirror setup
```

Setup checks for `rsync`, `lftp`, and `bandersnatch` even if your selected sync
method does not use all three. Install these before running setup. Setup finds
`mirror` on its current `PATH` and writes its absolute path into both systemd
units. The recommended global installation therefore requires `mirror` to be
on root's `PATH`; verify this with `sudo mirror --version`. For a virtual
environment, keep it on `PATH` when invoking setup, as shown above. If `mirror`
cannot be found, setup aborts before writing files.
Sync subprocesses still need their tools available on the service's `PATH`;
setup does not copy the shell's `PATH` into the units.

Setup also installs Click's Bash completion script at
`/usr/local/share/bash-completion/completions/mirror`. It does not install the
distribution's `bash-completion` package or edit user shell startup files.
Completion requires Bash 4.4 or later with `bash-completion` installed and
enabled. Open a new shell after setup; `mirror t` followed by Tab completes to
`mirror tui`, and `mirror daemon --` followed by Tab twice lists command options.

See [State files](../guide/state-files.md) for the full path layout that
`mirror setup` creates.

## External tools required by each sync method

mirror.py spawns external binaries to perform the actual sync. Install the
tools that correspond to the sync methods you intend to use.

| Sync method | External tool | Notes |
|-------------|--------------|-------|
| `rsync` | `rsync` | Available in all major Linux distributions |
| `ftpsync` | archvsync | Provisioned automatically; optional `git` enables upstream updates |
| `debmirror` | `debmirror` | Install separately; mirrors selected APT suites and architectures |
| `apt-mirror2` | `apt-mirror` | Install the optional `apt-mirror2` extra |
| `lftp` | `lftp` | Mirror via LFTP's mirror command |
| `ubuntu` | `rsync` | Two-stage Ubuntu archive mirroring |
| `jigdo` | `jigdo-mirror` | Required for Debian CD jigdo mirroring |
| `bandersnatch` | `bandersnatch` | Included as a Python dependency; mirrors PyPI |
| `local` | none | Registers existing local data; performs no copying |

`bandersnatch` is listed as a direct Python dependency in `pyproject.toml` and
is installed automatically. Install system tools as needed (for example,
`apt install rsync lftp debmirror`). For a release installation, install the
`apt-mirror2` Python extra globally as shown above. For a source development
environment, use:

```bash
uv pip install -e ".[apt-mirror2]"
```

## Optional dependencies

The `docs` dependency group installs Sphinx and the MyST parser for building
this documentation:

```bash
uv sync --group docs
npm --prefix docs/editor run docs:build
```

Building the configuration editor also requires Node.js 22.12 or later and npm.
See [Contributing](../contributing/index.md#building-documentation) for local preview
and test commands.

The default `dev` group installs pytest and pytest-docker for running the unit
and Docker integration suites:

```bash
uv run pytest tests/
```
