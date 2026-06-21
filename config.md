# Mirror.py Configuration Guide

This document describes how to write `config.json` for each sync method, and
ends with an **AI Assistant Protocol** you can hand to an AI so it collects the
right information and emits a ready-to-use config block on its own.

> `config.json` is **read-only at runtime**. Only `mirror setup` ever writes it.
> Runtime state (status, error counts, timestamps) lives in `stat.json`, not here.

---

## 1. Top-level structure

```json
{
    "mirrorname": "KAIST FTP",
    "hostname": "ftp.kaist.ac.kr",
    "settings": { ... },
    "packages": {
        "<package-id>": { ... },
        "<package-id>": { ... }
    }
}
```

| Key          | Type   | Description                                              |
|--------------|--------|----------------------------------------------------------|
| `mirrorname` | string | Human-readable mirror name (shown in the web UI).        |
| `hostname`   | string | This mirror's own FQDN. Used as the ftpsync trace host.  |
| `settings`   | object | Global daemon settings (see §2).                         |
| `packages`   | object | Map of `package-id` to a package definition (see §3).    |

---

## 2. Global `settings`

```json
"settings": {
    "logfolder": "/mirror/logs",
    "webroot": "/var/www/mirror",
    "statusfile": "/var/www/mirror/status.json",
    "statfile": "/var/lib/mirror/stat.json",
    "uid": 0,
    "gid": 0,
    "localtimezone": "Asia/Seoul",
    "errorcontinuetime": 60,
    "max_runtime": "PT12H",
    "maintainer": { "name": "Roul", "email": "roul@ftp.kaist.ac.kr" },
    "logger": { ... },
    "ftpsync": { ... },
    "plugins": {},
    "socket": { "mode": "0770" }
}
```

| Key                 | Type   | Notes                                                                       |
|---------------------|--------|-----------------------------------------------------------------------------|
| `logfolder`         | path   | Base directory for daemon logs.                                             |
| `webroot`           | path   | Web root for the status UI.                                                 |
| `statusfile`        | path   | Web status JSON output path.                                                |
| `statfile`          | path   | Persistent package state (`stat.json`).                                     |
| `uid` / `gid`       | int    | UID/GID that sync subprocesses drop to. `0` (root) triggers a warning — set non-root. |
| `localtimezone`     | string | IANA timezone name.                                                         |
| `errorcontinuetime` | int    | Seconds to wait before retrying after an error.                             |
| `max_runtime`       | string | ISO 8601 duration; a sync running longer is killed. Below `PT6H` warns; `PT12H`+ recommended. |
| `maintainer`        | object | `{ "name", "email" }`.                                                      |
| `logger`            | object | Log levels, formats, and file rotation (see `config-example.json`).         |
| `ftpsync`           | object | Global ftpsync defaults (see §4.2).                                         |
| `plugins`           | object | Enable-only plugin map: `{ "<name>": {"enabled": bool} }`. Use `{}` when unused. Per-plugin config lives in `<name>.json` next to `config.json`. |
| `socket`            | object | Master control socket ownership/permissions (see §2.2). Optional.           |

The `logger` block is rarely changed; copy it verbatim from `config-example.json`.

### 2.1 Global `settings.ftpsync`

Defaults applied to **every** ftpsync package; per-package `options` override
each key individually.

```json
"ftpsync": {
    "maintainer": "KAIST FTP Maintainers <ftp@ftp.kaist.ac.kr>",
    "sponsor": "KAIST <https://ftp.kaist.ac.kr> SPARCS <https://sparcs.org>",
    "country": "KR",
    "location": "Daejeon",
    "throughput": "10Gb",
    "include": "",
    "exclude": ""
}
```

### 2.2 Global `settings.socket`

Ownership and permissions of the master control socket (`master.sock`), bound
once at daemon startup. The whole block is optional.

```json
"socket": {
    "uid": 0,
    "gid": 0,
    "mode": "0770"
}
```

| Key    | Type   | Default  | Notes                                                                       |
|--------|--------|----------|-----------------------------------------------------------------------------|
| `uid`  | int    | unset    | `chown` owner of `master.sock`. Omit (or no block) to leave ownership unchanged. |
| `gid`  | int    | unset    | `chown` group of `master.sock`. Omit to leave unchanged.                    |
| `mode` | string | `"0600"` | Octal **string** (e.g. `"0770"`), not an int. Controls who may connect to the daemon socket. |

> Omit the block for the secure default: mode `0600`, no `chown`.
> **Restart-only.** The socket is bound at startup and never re-created on
> reload, so changing `settings.socket` takes effect only after a daemon
> restart. A live reload keeps the running value and emits the warning
> `socket change requires daemon restart (kept current value)`.

---

## 3. Package definition (common fields)

Every package — regardless of sync method — shares this shape:

```json
"<package-id>": {
    "name": "Display Name",
    "id": "<package-id>",
    "href": "/path-on-web",
    "synctype": "rsync",
    "syncrate": "PT1H",
    "link": [
        { "rel": "HOME", "href": "https://example.org/" }
    ],
    "settings": {
        "hidden": false,
        "src": "rsync://upstream/module",
        "dst": "/srv/ftp/example",
        "options": { ... }
    }
}
```

| Field               | Type    | Description                                                                 |
|---------------------|---------|-----------------------------------------------------------------------------|
| `name`              | string  | Display name shown in the UI (this is the visible label).                   |
| `id`                | string  | Internal identifier. Must equal the map key. Must not start with `_` or collide with reserved names (`get`, `items`, `keys`, `values`, `to_dict`). |
| `href`              | string  | Web path for this mirror.                                                   |
| `synctype`          | string  | One of: `rsync`, `ftpsync`, `lftp`, `bandersnatch`, `ubuntu`, `jigdo`, `local`. |
| `syncrate`          | string  | Sync interval (see §3.1).                                                   |
| `link`              | array   | List of `{ "rel", "href" }` reference links (e.g. upstream HOME).           |
| `settings.hidden`   | bool    | Hide the package from the web UI when `true`.                               |
| `settings.src`      | string  | Upstream source. Format depends on `synctype` (see §4).                     |
| `settings.dst`      | string  | Local destination directory.                                                |
| `settings.options`  | object  | Method-specific options (see §4). `{}` when none.                           |

### 3.1 `syncrate` values

| Value             | Meaning                                                            |
|-------------------|--------------------------------------------------------------------|
| ISO 8601 duration | Interval between syncs. e.g. `PT10M` (10 min), `PT1H` (1 h), `PT6H`, `P1D` (1 day), `P1DT2H` (1 day 2 h). |
| `"PUSH"`          | No timed schedule; sync is triggered externally (push-based). Common for Debian ftpsync. |
| `""`              | Disabled / never auto-syncs (used by `local`).                     |

---

## 4. Per-method `settings`

Each method only reads the keys listed below. Unknown keys in `options` are ignored.

### 4.1 `rsync`

Incremental rsync with an optional FFTS (fast file-time-list) pre-check that
skips the full sync when the upstream timestamp file is unchanged.

- `src`: rsync URL **without** trailing slash, e.g. `rsync://ftp.gwdg.de/pub/linux/archlinux`
  (the module appends `/` automatically).
- `dst`: local directory.

| Option     | Type   | Default | Description                                                              |
|------------|--------|---------|--------------------------------------------------------------------------|
| `ffts`     | bool   | `false` | Enable the FFTS dry-run pre-check before the full sync.                  |
| `fftsfile` | string | `""`    | Upstream timestamp file checked by FFTS (e.g. `lastupdate`, `fullfiletimelist-rocky`). Required when `ffts` is `true`. |
| `user`           | string       | `""`    | rsync username (sets `USER` env). Leave empty for anonymous.            |
| `password`       | string       | `""`    | rsync password (sets `RSYNC_PASSWORD` env).                            |
| `option_include` | string       | `""`    | Flag characters to **add** to the default flag string. Each char must be in the whitelist `vrlptDSHaznhPxWENcimub`; no `-`, spaces, or control chars. Chars already present are skipped. e.g. `"p"` re-adds permission preservation. |
| `option_exclude` | string       | `""`    | Flag characters to **remove** from the default flag string. No `-`, spaces, or control chars. e.g. `"H"` drops hard-link preservation. |
| `exclude`        | list[string] | `[]`    | Extra `--exclude=<pattern>` rules, appended after the built-in `--exclude=*.~tmp~`. Items must be strings without control characters. |

> Note: the rsync module reads `user`/`password`. A key named `username` is **not** read.
>
> Flag string: the default is `-vrltDSH` (since 1.2; previously `-vrlptDSH`,
> i.e. `-p` was dropped, so upstream file modes are no longer mirrored by
> default). `option_include` / `option_exclude` adjust this string —
> `option_include` is applied first (appends missing chars), then
> `option_exclude` removes chars. To restore permission mirroring, set
> `"option_include": "p"`.

```json
"archlinux": {
    "name": "ArchLinux",
    "id": "archlinux",
    "href": "/ArchLinux",
    "synctype": "rsync",
    "syncrate": "PT1H",
    "link": [ { "rel": "HOME", "href": "https://archlinux.org/" } ],
    "settings": {
        "hidden": false,
        "src": "rsync://ftp.gwdg.de/pub/linux/archlinux",
        "dst": "/srv/ftp/ArchLinux",
        "options": { "ffts": true, "fftsfile": "lastupdate" }
    }
}
```

### 4.2 `ftpsync`

Debian archvsync-based mirroring. Builds an `ftpsync.conf` from the package and
the global `settings.ftpsync` defaults.

- `src`: either a full rsync URL (`rsync://host/module`) **or** a bare host
  combined with the `path` option.
- `dst`: local directory (becomes `TO=`).
- `syncrate`: usually `"PUSH"` for Debian.

| Option        | Type   | Description                                                                |
|---------------|--------|----------------------------------------------------------------------------|
| `path`        | string | rsync module path. Required when `src` is a bare host; overrides the URL path otherwise. |
| `hub`         | bool   | Sets `HUB=`. Default `false`.                                              |
| `user`        | string | `RSYNC_USER`. Only emitted when both `user` and `password` are present.    |
| `password`    | string | `RSYNC_PASSWORD`.                                                          |
| `email`       | string | `MAILTO=` for ftpsync notifications.                                      |
| `maintainer`  | string | Overrides global `INFO_MAINTAINER`.                                        |
| `sponsor`     | string | Overrides global `INFO_SPONSOR`.                                          |
| `country`     | string | Overrides global `INFO_COUNTRY`.                                          |
| `location`    | string | Overrides global `INFO_LOCATION`.                                         |
| `throughput`  | string | Overrides global `INFO_THROUGHPUT`.                                       |
| `arch_include`| string | `ARCH_INCLUDE` (defaults to global `include`).                            |
| `arch_exclude`| string | `ARCH_EXCLUDE` (defaults to global `exclude`).                            |
| `logdir`      | path   | Override ftpsync log directory (defaults to global `logfolder`).          |

```json
"debian": {
    "name": "Debian",
    "id": "debian",
    "href": "/debian",
    "synctype": "ftpsync",
    "syncrate": "PUSH",
    "link": [ { "rel": "HOME", "href": "http://debian.org" } ],
    "settings": {
        "hidden": false,
        "src": "rsync://syncproxy2.wna.debian.org/debian",
        "dst": "/srv/ftp/debian",
        "options": { "hub": false }
    }
}
```

### 4.3 `lftp`

Anonymous FTP mirroring via `lftp`. **`src` must be an `ftp://` URL** with no
credentials, query, or fragment (validated strictly).

| Option          | Type        | Default                          | Description                                              |
|-----------------|-------------|----------------------------------|----------------------------------------------------------|
| `list_options`  | string      | unset                            | Only `"-a"` is accepted (lists hidden files).            |
| `scan_all_first`| bool        | `false`                          | Pass `--scan-all-first` to `lftp mirror`.                |
| `exclude_x`     | list[string]| `["\\.in\\..*\\."]`              | `-x` regex excludes. Setting this enables custom-exclude mode. |
| `exclude_X`     | list[string]| `["\\.(mirror|notar)", "lost+found"]` | `-X` regex excludes.                                 |
| `exclude`       | list[string]| `[]`                             | `--exclude` patterns.                                    |
| `max_retries`   | int (1–100) | `3`                              | `net:max-retries`.                                       |
| `net_timeout`   | int (1–3600)| `60`                             | `net:timeout` seconds.                                   |

```json
"example-ftp": {
    "name": "Example",
    "id": "example-ftp",
    "href": "/example",
    "synctype": "lftp",
    "syncrate": "PT6H",
    "link": [],
    "settings": {
        "hidden": false,
        "src": "ftp://ftp.example.org/pub/example",
        "dst": "/srv/ftp/example",
        "options": { "max_retries": 5, "net_timeout": 120 }
    }
}
```

### 4.4 `ubuntu`

Two-stage rsync (metadata-last) tuned for Ubuntu archives, writing a trace file
on success.

- `src`: rsync URL. `dst`: local directory.

| Option            | Type        | Default                                      | Description                                  |
|-------------------|-------------|----------------------------------------------|----------------------------------------------|
| `trace`           | bool        | `true`                                       | Write `<dst>/project/trace/<host>` on success. |
| `extra_rsync_args`| list[string]| `[]`                                         | Extra rsync flags.                           |
| `stage1_excludes` | list[string]| `["Packages*","Sources*","Release*","InRelease"]` | Patterns excluded in stage 1.           |
| `user`            | string      | `""`                                         | rsync username.                              |
| `password`        | string      | `""`                                         | rsync password.                              |

```json
"ubuntu": {
    "name": "Ubuntu", "id": "ubuntu", "href": "/ubuntu",
    "synctype": "ubuntu", "syncrate": "PT6H",
    "link": [ { "rel": "HOME", "href": "http://www.ubuntu.com" } ],
    "settings": {
        "hidden": false,
        "src": "rsync://archive.ubuntu.com/ubuntu",
        "dst": "/srv/ftp/ubuntu",
        "options": {}
    }
}
```

### 4.5 `jigdo`

Three-phase Debian CD mirror: template rsync, local ISO regeneration via
`jigdo-mirror`, and a final size-only rsync.

- `src`: rsync URL. `dst`: local directory.
- **Required options**: `jigdo_file`, `debian_mirror`.

| Option             | Type        | Default            | Description                                        |
|--------------------|-------------|--------------------|----------------------------------------------------|
| `jigdo_file`       | string      | **required**       | rsync URL/path to the jigdo-file index.            |
| `debian_mirror`    | string      | **required**       | Debian package mirror URL used to assemble ISOs.   |
| `timeout`          | int         | `7200`             | rsync `--timeout` (seconds).                       |
| `trace`            | bool        | `true`             | Write trace file on success.                       |
| `trace_path`       | string      | `project/trace`    | Relative subdir under `dst` for the trace file.    |
| `template_excludes`| list[string]| `["*.iso"]`        | Phase 1 exclude patterns.                          |
| `final_includes`   | list[string]| `["*businesscard*.iso","*netinst*.iso","i386/**.iso"]` | Phase 3 include patterns.      |
| `extra_rsync_args` | list[string]| `[]`               | Extra rsync flags for both rsync phases.           |
| `rsync_bin`        | string      | `rsync`            | rsync binary path/name.                            |
| `jigdo_mirror_bin` | string      | `jigdo-mirror`     | jigdo-mirror binary path/name.                     |
| `hostname`         | string      | mirror hostname    | Override hostname used in exclude patterns.        |
| `user` / `password`| string      | `""`               | rsync credentials.                                 |

### 4.6 `bandersnatch`

PyPI mirroring. The module simply runs `bandersnatch mirror`; the actual mirror
behavior is driven by **bandersnatch's own config file**, not by `settings`
here. `src`/`dst`/`options` are not consumed by this method (still provide
`src`/`dst` for documentation/UI consistency).

| Option | Type | Description          |
|--------|------|----------------------|
| —      | —    | No options are read. |

### 4.7 `local`

This server is the authoritative master for the data; there is no upstream sync.
The method only verifies `dst` exists and marks the package ACTIVE.

- `src`: ignored (use `""`).
- `dst`: must already exist on disk.
- `syncrate`: typically `""`.

| Option | Type | Description          |
|--------|------|----------------------|
| —      | —    | No options are read. |

---

## 5. AI Assistant Protocol

> Paste this section (or the whole document) to an AI assistant. It tells the
> assistant exactly what to ask for and how to produce a config block.

**Role**: You generate a `mirror.py` package config block from information the
user provides. Follow these steps:

1. **Identify the sync method.** Map the user's intent to one `synctype`:
   - rsync URL + "skip when unchanged" / timestamp file -> `rsync` (set `ffts`/`fftsfile`).
   - Debian, push-based -> `ftpsync` (`syncrate: "PUSH"`).
   - Ubuntu archive -> `ubuntu`.
   - Debian CD/ISO with jigdo -> `jigdo`.
   - Anonymous `ftp://` source -> `lftp`.
   - PyPI -> `bandersnatch`.
   - This host is the origin / no upstream -> `local`.

2. **Collect the required inputs** for that method:
   - **Always**: display `name`, `id` (kebab-case, equals the map key), `href`,
     `src`, `dst`, `syncrate`, and any reference `link`s.
   - **rsync**: whether FFTS is used and the `fftsfile` name; credentials if any.
   - **ftpsync**: rsync URL or (host + `path`); `hub`; any `INFO_*` overrides.
   - **lftp**: confirm `src` is an `ftp://` URL; retry/timeout/excludes if any.
   - **ubuntu**: trace on/off; extra excludes if any.
   - **jigdo**: `jigdo_file` and `debian_mirror` (both required).
   - **bandersnatch**: note the separate bandersnatch config file is required.
   - **local**: confirm `dst` exists; `src` empty.

3. **Apply naming conventions exactly as the user states them.** If the user
   specifies a display spelling (e.g. "represent it as ArchLinux"), use that
   spelling for `name`, `href`, and `dst` as appropriate, while keeping `id`
   in kebab-case.

4. **Pick a sane `syncrate`** if the user did not give one: `PT1H` for most
   rsync/lftp mirrors, `"PUSH"` for Debian ftpsync, `""` for `local`. State the
   assumption.

5. **Validate before output**:
   - `id` equals the map key, does not start with `_`, and is not a reserved
     name (`get`, `items`, `keys`, `values`, `to_dict`).
   - `src` strips any trailing slash for rsync.
   - Only options listed for the chosen method appear in `options`.
   - Use `user`/`password` (not `username`) for credentials.

6. **Output** the package block as a JSON snippet ready to paste under
   `"packages"`. If the user is creating a full standalone file, wrap it with
   the top-level structure and copy the `settings` block from an existing
   config. After the JSON, briefly list any assumptions you made.

7. **Do not invent options.** If the user asks for behavior no option supports,
   say so instead of fabricating a key.

**Minimal input the AI should ask for if missing**: sync method (or enough to
infer it), `src`, `dst`, display name, and desired `syncrate`.
