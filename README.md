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
