# Installation

## Requirements

- Python 3.10 or later
- Linux (the daemon relies on Unix domain sockets and Linux process management)

## Installing from source

mirror.py is not published to PyPI. Install it directly from the repository
using [uv](https://github.com/astral-sh/uv).

**Development install (editable):**

```bash
git clone https://github.com/sparcs-kaist/mirror.py.git
cd mirror.py
uv venv
source .venv/bin/activate
uv pip install -e .
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
sudo env "PATH=$PATH" mirror setup
```

Setup checks for `rsync`, `lftp`, and `bandersnatch` even if your selected sync
method does not use all three. Install these before running setup. The generated
systemd units also need an executable path that resolves to your installed `mirror`
command; for a virtual environment, set `ExecStart` to its absolute path.

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
| `jigdo` | `jigdo-mirror` | Required for Debian CD jigdo mirroring |
| `bandersnatch` | `bandersnatch` | Included as a Python dependency; mirrors PyPI |
| `local` | none | Registers existing local data; performs no copying |

`bandersnatch` is listed as a direct Python dependency in `pyproject.toml` and
is installed automatically. Install system tools as needed (for example,
`apt install rsync lftp debmirror`). For `apt-mirror2`, install the Python extra:

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

The default `dev` group installs pytest for running the unit test suite:

```bash
uv run pytest tests/
```
