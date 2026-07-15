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
uv pip install -e .
```

This registers the `mirror` CLI entry point so you can run `mirror` from any
directory while working on the source.

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
mirror setup
```

See [State files](../guide/state-files.md) for the full path layout that
`mirror setup` creates.

## External tools required by each sync method

mirror.py spawns external binaries to perform the actual sync. Install the
tools that correspond to the sync methods you intend to use.

| Sync method | External tool | Notes |
|-------------|--------------|-------|
| `rsync` | `rsync` | Available in all major Linux distributions |
| `ftpsync` | `ftpsync` / archvsync | Debian archvsync suite; required for Debian FTP mirroring |
| `lftp` | `lftp` | Mirror via LFTP's mirror command |
| `jigdo` | `jigdo-mirror` | Required for Debian CD jigdo mirroring |
| `bandersnatch` | `bandersnatch` | Included as a Python dependency; mirrors PyPI |
| `local` | none | Copies within the local filesystem; no external tool needed |

`bandersnatch` is listed as a direct Python dependency in `pyproject.toml` and
is installed automatically. All other tools must be installed separately via
your system package manager (for example, `apt install rsync lftp`).

## Optional dependencies

The `docs` dependency group installs Sphinx and the MyST parser for building
this documentation:

```bash
uv pip install -e ".[docs]"
```

The `dev` group installs pytest for running the test suite:

```bash
uv pip install -e ".[dev]"
uv run pytest tests/
```
