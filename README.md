# Mirror.PY

Mirror.PY is a simple python daemon that mirrors a directory to another directory. It is designed to be used with a web server to serve the mirrored directory.

## Debian repositories with debmirror

The `debmirror` sync method uses the repository root in `settings.src` and the
local mirror root in `settings.dst`. No separate debmirror configuration file
is required. Install the `debmirror` executable and provide the repository's
trusted public keyring when signature verification is enabled (the default).
All implementation lives in `mirror/sync/debmirror.py`. Each worker run creates
an empty config in a private temporary directory to prevent loading system or
user debmirror configuration, then removes it when debmirror exits or the run
is terminated normally.

The `dist`, `section`, and `arch` options each accept a string, a comma-separated
string, or a list of strings. An omitted option is discovered on every sync:

- `dist`: distributions directly below the repository's `dists/` directory,
  including updates and backports. Directory listing must be available;
  otherwise specify `dist` explicitly.
- `section`: all `Components` in the selected distributions' Release metadata.
- `arch`: all binary `Architectures`, including architecture-independent
  packages. This does not enable source packages.

`source` defaults to `false`; set it to `true` to also download source packages.
Explicit selections and package filters still restrict what is downloaded.
Empty or null selections are invalid; omit the key to enable discovery.

For example, these settings discover all binary distributions, components, and
architectures, using a public keyring installed at the specified path:

```json
{
  "src": "http://download.proxmox.com/debian/pve/",
  "dst": "/srv/ftp/pve",
  "options": {
    "keyring": "/path/to/proxmox-keyring.gpg",
    "rsync_extra": "none"
  }
}
```

To restrict that mirror, add selections such as `"dist": ["bookworm"]`,
`"section": ["pve-no-subscription"]`, or `"arch": ["amd64"]` independently.
Discovery prefers `InRelease`, falling back to `Release` only when it is absent.
Signature, metadata, or listing errors fail the sync instead of falling back to
debmirror's built-in selections. Automatic discovery deduplicates aliases with
identical Release contents. The installed debmirror must still support the
repository layout; this feature does not add support for InRelease-only archives.

Automatic distribution discovery refuses to remove an existing distribution
when it disappears from the listing, protecting the mirror from partial
directory responses. To intentionally narrow the mirror, specify `dist`.
Discovery limits each metadata response to 16 MiB and the candidate list to
1,024 distributions. Its total time limit is 300 seconds or the configured
`timeout`, whichever is greater; individual requests also use `timeout`.

## Multiple APT repositories with apt-mirror2

The `apt-mirror2` sync method groups several HTTP, HTTPS, or FTP repositories
under one package, schedule, log, and status. Both regular Debian repositories
and flat repositories are supported. Install the optional dependency in the
same Python environment as mirror.py, plus GnuPG (`gpg` and `gpgv`) for signed
repositories:

```bash
pip install 'mirror.py[apt-mirror2]'
# From a source checkout:
uv sync --extra apt-mirror2
```

This extra pins the Python apt-mirror2 implementation (`apt-mirror==16`). The
worker invokes `python -m apt_mirror`, not the unrelated Perl `apt-mirror` binary.
Other sync methods do not require this extra.

`settings.src` is a display URL; `settings.dst` is the absolute local root.
The required, non-empty `options.config` list supplies actual source URLs and
non-overlapping relative destination directories:

```json
{
  "src": "https://repo.example/cuda/",
  "dst": "/srv/ftp/cuda",
  "options": {
    "source": false,
    "nthreads": 8,
    "limit_rate": "20m",
    "config": [
      {
        "src": "https://repo.example/cuda/ubuntu2404/x86_64/",
        "dst": "ubuntu2404/x86_64/",
        "dist": ["./"],
        "keyring": ["/usr/share/keyrings/cuda-ubuntu2404.gpg"]
      },
      {
        "src": "https://repo.example/cuda/ubuntu2604/x86_64/",
        "dst": "ubuntu2604/x86_64/",
        "dist": ["./"],
        "keyring": ["/usr/share/keyrings/cuda-ubuntu2604.gpg"]
      }
    ]
  }
}
```

The CUDA URLs and keyring filenames in `config-example.json` are illustrative;
check repository availability and install the appropriate keys before use.
Files are stored directly below each mapped destination without an extra
hostname directory. Duplicate source URLs and overlapping destinations are
rejected. Destinations must be non-empty subdirectories, not `.` or absolute
paths, and must stay inside `settings.dst` after resolving symlinks.
Existing symlinks within a destination path and credentials in source URLs
are rejected.

### Selections and verification

Each item accepts `dist`, `section`, and `arch` as strings, comma-separated
strings, or lists. Empty selections are invalid. For regular repositories,
omitted selections are discovered from `dists/` listings and Release metadata.
Automatic selection refuses to remove existing distributions missing from a
new listing; specify `dist` explicitly to intentionally narrow the mirror.

For flat repositories, use `dist: ["./"]` for indexes at the source URL or
paths such as `["x64/", "all/"]` for explicit subdirectories. When `dist` is
omitted, Release checksums for direct `Packages` indexes identify a flat
repository; otherwise discovery looks under `dists/`. Source-only flat indexes
also identify a flat repository when `source` is enabled. Subdirectories are
not searched recursively. Flat indexes include all binary architectures;
explicit `arch` or `section` values are rejected because apt-mirror2 does not
apply those filters to flat repositories.

`source` defaults to `false` and can be overridden per item. `check_gpg`
defaults to `true` per item and requires a local `keyring` path or list of paths.
Only that item's keys are trusted; system keyrings and other items' keys are
not used. Keys are not downloaded automatically. Discovery verifies metadata
before using it, and apt-mirror2 verifies it again before mirroring. Missing or
invalid signatures fail the sync. Unsigned repositories require explicit
`check_gpg: false`. The pinned apt-mirror2 v16 still requires Release metadata,
even when signature verification is disabled; repositories containing only
Packages without Release metadata are rejected before native execution.

Discovery prefers InRelease, falling back to Release and Release.gpg only when
InRelease is absent (HTTP 404/410). For FTP, only an InRelease RETR response of
550 permits a fallback attempt; the replacement metadata and required signature
must still succeed. A present but invalid signature never triggers fallback.
Discovery limits metadata to 16 MiB, distributions to 1,024, listing links to
4,096, each network operation to 30 seconds, and each item's discovery to 300
seconds. HTTPS downgrade redirects are rejected.

### Downloads and cleanup

`nthreads` defaults to 8 and controls concurrency for the whole package.
`limit_rate` is an optional aggregate bytes-per-second limit: a positive integer
or a string with a `k` or `m` suffix. When a limit is supplied, native slow-rate
protection is disabled so it does not conflict with the chosen cap. Without a
limit, downloads are uncapped and native slow-rate protection remains enabled.
The native limiter uses a 60-second bucket, so short bursts can exceed the
configured per-second rate.

Native file hash verification remains enabled. Files considered unchanged by
apt-mirror2 are not necessarily rehashed on each run. Cleanup is automatic for
each successful repository, with native deletion-count and deletion-size
protection ratios of 0.4. Exceeding a ratio skips cleanup and emits a warning;
it does not turn native success into failure. Failed repositories skip metadata
publication and cleanup. One failed repository makes the package `ERROR`, but
other repositories may complete: there is no package-wide rollback. Discovery
failure stops the job before native mirroring begins.

The worker creates a private temporary directory under the destination root
for its config and working files, and removes it on success, failure, or handled
termination. Runtime never writes the user's mirror.py configuration. Raw
apt-mirror2 configuration, external config files, and other native options are
not exposed by this adapter.

## Plug-ins

mirror.py supports pip-installable plug-ins via Python entry points. There are
three plug-in categories:

- **sync** — implement a new synctype (alongside the built-in `rsync`,
  `ftpsync`, `lftp`, `bandersnatch`, `local`).
- **event** — subscribe to mirror events to drive notifications, custom logs,
  external integrations.
- **status** — contribute extra fields into `stat.json` and the web status JSON.

See [`docs/PLUGINS.md`](docs/PLUGINS.md) for the author guide and API
reference, and [`examples/mirror-plugin-echo/`](examples/mirror-plugin-echo/)
for a runnable worked example.

## Debian CD jigdo sync

The `jigdo` sync method mirrors the Debian CD metadata from the official
`rsync://cdimage.debian.org/debian-cd/` tree and reconstructs selected images
from a Debian package mirror. It requires `rsync`, `jigdo-file`,
`jigdo-mirror`, `grep`, `wget`, and `gzip`.

The default image filter matches CD and DVD images 1 through 3 for `amd64`,
`i386`, `sparc`, and `source`, then excludes `kfreebsd`:

```text
include: .*i386-(CD|DVD)-[1-3].iso.*|.*amd64-(CD|DVD)-[1-3].iso.*|.*sparc-(CD|DVD)-[1-3].iso.*|.*source-(CD|DVD)-[1-3].iso.*
exclude: .*kfreebsd.*
```

These defaults deliberately narrow the previous behavior, which selected all
images. Both filters are POSIX extended regular expressions. The include
filter is applied first and the exclude filter removes matches afterward.

Run a standalone sync with:

```bash
mirror worker-execute jigdo \
  --src rsync://cdimage.debian.org/debian-cd/ \
  --dst /srv/mirror/debian-cd \
  --jigdo-file 'jigdo-file' \
  --debian-mirror https://ftp.kaist.ac.kr/debian/
```

`--jigdo-include` and `--jigdo-exclude` override the filters and may each be
specified once. `--jigdo-file` accepts the command and its arguments as a
literal value; shell variables are not expanded. A production mirror can use
a nearby local package mirror such as `file:/srv/mirror/debian` for
`--debian-mirror`.

For daemon operation, configure a package in `config.json`:

```json
{
  "packages": {
    "debian-cd": {
      "id": "debian-cd",
      "name": "Debian CD",
      "href": "/debian-cd",
      "synctype": "jigdo",
      "syncrate": "PT6H",
      "link": [
        {
          "rel": "HOME",
          "href": "https://www.debian.org/CD/"
        }
      ],
      "settings": {
        "hidden": false,
        "src": "rsync://cdimage.debian.org/debian-cd/",
        "dst": "/srv/mirror/debian-cd",
        "options": {
          "jigdo_file": "jigdo-file",
          "debian_mirror": "file:/srv/mirror/debian",
          "jigdo_include": ".*amd64-(CD|DVD)-[1-3].iso.*",
          "jigdo_exclude": ".*kfreebsd.*"
        }
      }
    }
  }
}
```

The destination keeps the official Debian CD layout, including the same
version, architecture, and ISO-set directories and the `current` symlink. For
example, generated images are stored as
`<dst>/<version>/amd64/iso-dvd/debian-<version>-amd64-DVD-1.iso` through
`DVD-3.iso` when all three match the available jigdo metadata.
