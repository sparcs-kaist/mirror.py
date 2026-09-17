# Sync methods

Each package in mirror.py has a `synctype` field in its configuration that selects which
sync method runs for that package. This section is the operator-facing quick reference for
the options each method reads from the per-package `options` object. The authoritative full
narrative, including global configuration keys such as `settings.ftpsync`, lives in the
[Configuration page](../guide/configuration.md) (section 4).

## Method comparison

| Method | Use case | External tool required | Reads per-package options? |
|--------|----------|------------------------|---------------------------|
| [rsync](rsync.md) | Generic incremental mirror, optional FFTS pre-check | `rsync` | Yes |
| [ftpsync](ftpsync.md) | Debian archvsync-based FTP mirroring | `rsync`; mirror.py provisions the archvsync script | Yes |
| [debmirror](debmirror.md) | Debian-style APT archive with discovery and filters | `debmirror` (`rsync` for rsync transport) | Yes |
| [apt-mirror2](apt-mirror2.md) | One or more regular or flat APT repositories | `apt-mirror==16`; `gpg`/`gpgv` for signed repositories | Yes |
| [lftp](lftp.md) | Mirror via lftp with regex excludes | `lftp` | Yes |
| [ubuntu](ubuntu.md) | Two-stage Ubuntu archive mirror | `rsync` | Yes |
| [jigdo](jigdo.md) | Debian CD/DVD image assembly | `rsync` + `jigdo-mirror` | Yes |
| [bandersnatch](bandersnatch.md) | PyPI mirror | `bandersnatch` | No (uses bandersnatch's own config) |
| [local](local.md) | No remote fetch; serve an existing local directory | none | No |

## How to choose

- Use **rsync** for most generic mirrors. Add `ffts: true` when the upstream publishes a
  file-timestamp list to avoid unnecessary transfers.
- Use **ftpsync** for official Debian archive mirrors; it handles Debian-specific trace and
  architecture filtering requirements via the archvsync toolchain.
- Use **debmirror** for a selected Debian-style archive when you need automatic discovery,
  package filters, multiple transports, or explicit signature verification.
- Use **apt-mirror2** to group multiple APT repositories below one destination, including
  flat vendor repositories that do not use the usual `dists/` layout.
- Use **lftp** when the upstream is an FTP server and you need fine-grained regex-based
  exclusions.
- Use **ubuntu** for Ubuntu archive mirrors; it performs a two-stage rsync that avoids
  temporarily inconsistent index files.
- Use **jigdo** to assemble Debian CD/DVD ISO images from jigdo template files.
- Use **bandersnatch** for PyPI mirrors; all mirroring policy is configured in
  bandersnatch's own config file, not in mirror.py's package config.
- Use **local** when the data already exists on disk and only needs to be registered with
  the daemon for status tracking.
