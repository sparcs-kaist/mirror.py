# debmirror

Mirror a Debian-style APT archive with the `debmirror` command. mirror.py maps
package settings to command-line arguments, discovers omitted distributions,
components, and architectures on every run, and delegates the complete job to
the worker.

Install `debmirror` on the worker host. Rsync transport and rsync-based
discovery also require `rsync`. Each worker run supplies an empty private
debmirror config, so system and user `debmirror.conf` files cannot silently
change the generated command.

`settings.src` normally supplies the transport, host, and archive root, for
example `https://deb.debian.org/debian`. Supported transports are `http`,
`https`, `ftp`, `rsync`, and `file`. A `file:` source must use an absolute local
path and an empty or `localhost` authority. `settings.dst` must be an absolute
path.

## Repository selection and verification

| Option | Type | Default | Required | Description |
|--------|------|---------|----------|-------------|
| `method` | string | URL scheme | No | Override the transport with `ftp`, `http`, `https`, `rsync`, or `file`. |
| `host` | string | URL authority | No | Override the upstream host. Not accepted for the `file` method. |
| `root` | string | URL path | No | Override the archive root. The `file` method requires an absolute path. |
| `dist` | string or list of strings | discovered | No | Distributions to mirror. Comma-separated strings are accepted. |
| `section` | string or list of strings | discovered | No | Archive components to mirror. Comma-separated strings are accepted. |
| `arch` | string or list of strings | discovered | No | Binary architectures to mirror. Comma-separated strings are accepted. |
| `source` | bool | `false` | No | Include source packages. |
| `check_gpg` | bool | `true` | No | Enable Release signature verification. |
| `keyring` | absolute path or list of paths | (none) | Required operationally when GPG checks are enabled | Pass trusted keyrings to debmirror. mirror.py warns when none is configured or a configured file is missing. |
| `ignore_release_gpg` | bool | `false` | No | Pass `--ignore-release-gpg` while GPG checking is enabled. |
| `ignore_missing_release` | bool | `false` | No | Pass `--ignore-missing-release`. |

When any of `dist`, `section`, or `arch` is omitted, mirror.py resolves that
selection from repository listings and Release metadata. Discovery prefers
`InRelease`, only falls back when it is absent, verifies metadata before using
it, deduplicates distribution aliases with identical Release content, and
refuses an automatically discovered distribution set that would remove an
existing distribution. Specify `dist` explicitly to narrow a mirror.

## Content, transport, and cleanup options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `cleanup` | string | `"postcleanup"` | One of `postcleanup`, `precleanup`, or `nocleanup`. |
| `diff` | string | unset | Debian package diff behavior: `use`, `mirror`, or `none`. |
| `rsync_extra` | string or list of strings | unset | Extra rsync trees selected from `doc`, `indices`, `tools`, `trace`, or `none`. |
| `i18n` | bool | `false` | Include Translation files. |
| `getcontents` | bool | `false` | Download Contents files. |
| `di_dist` | string or list of strings | unset | Debian Installer distributions. |
| `di_arch` | string or list of strings | unset | Debian Installer architectures. |
| `proxy` | string | unset | Proxy passed to debmirror. Whitespace is rejected. |
| `passive` | bool | `false` | Use passive FTP. |
| `user` | string | unset | FTP or rsync username. HTTP, HTTPS, and file credentials are ignored with a warning. |
| `password` | string | unset | FTP password or `RSYNC_PASSWORD`. It is redacted from command logs. |
| `exclude` | string or list of strings | unset | Repeatable package exclusion regex. |
| `include` | string or list of strings | unset | Repeatable package inclusion regex. |
| `exclude_deb_section` | string or list of strings | unset | Repeatable Debian section exclusion. |
| `limit_priority` | string or list of strings | unset | Repeatable priority limit. |
| `rsync_options` | string | unset | Native rsync option string used by debmirror and discovery. |
| `timeout` | positive int | debmirror default; `300` for discovery | Native timeout in seconds. Discovery lasts at least 300 seconds overall and bounds each operation by this value. |
| `allow_dist_rename` | bool | `false` | Pass `--allow-dist-rename`. |
| `omit_suite_symlinks` | bool | `false` | Pass `--omit-suite-symlinks`. |

## Example

This entry is ready to place inside the top-level `packages` object:

```json
"debian-bookworm": {
  "name": "Debian Bookworm",
  "id": "debian-bookworm",
  "href": "/debian",
  "synctype": "debmirror",
  "syncrate": "PT6H",
  "link": [{ "rel": "HOME", "href": "https://www.debian.org/" }],
  "settings": {
    "hidden": false,
    "src": "https://deb.debian.org/debian",
    "dst": "/srv/mirror/debian",
    "options": {
      "dist": ["bookworm", "bookworm-updates"],
      "section": ["main", "contrib", "non-free", "non-free-firmware"],
      "arch": ["amd64", "arm64"],
      "source": false,
      "check_gpg": true,
      "keyring": "/usr/share/keyrings/debian-archive-keyring.gpg",
      "cleanup": "postcleanup",
      "rsync_extra": "none"
    }
  }
}
```

Use a separate package and destination for Debian Security because it has a
different repository root and distribution names.
