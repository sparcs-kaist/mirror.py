# Mirror.PY

Mirror.PY is a simple python daemon that mirrors a directory to another directory. It is designed to be used with a web server to serve the mirrored directory.

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
