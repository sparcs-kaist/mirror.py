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