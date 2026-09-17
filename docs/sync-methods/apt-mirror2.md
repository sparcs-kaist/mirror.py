# apt-mirror2

Mirror one or more APT repositories as a single mirror.py package using the
Python apt-mirror2 implementation. Each configured repository maps to a distinct
relative directory below the package destination. Regular Debian-style and flat
repositories are supported over HTTP, HTTPS, and anonymous FTP.

Install the optional dependency in the same Python environment as mirror.py:

```bash
uv sync --extra apt-mirror2
# Or in an existing source-checkout environment:
uv pip install -e '.[apt-mirror2]'
```

The extra pins `apt-mirror==16`. Signed repositories also require `gpg` and
`gpgv`. The worker runs the Python module with a generated private config; it
does not invoke the unrelated Perl `apt-mirror` command or modify mirror.py's
configuration file.

## Package options

`settings.src` is used for display only. The actual upstream URLs are the `src`
values in `options.config`. `settings.dst` must be a non-root absolute path
without whitespace, `$`, or `#`.

| Option | Type | Default | Required | Description |
|--------|------|---------|----------|-------------|
| `config` | list of objects | (none) | Yes | Non-empty repository list. Each item needs `src` and `dst`. Source URLs must be unique and destination paths must not overlap. |
| `source` | bool | `false` | No | Default for downloading source indexes and packages. An item can override it. |
| `nthreads` | positive int | `8` | No | Download concurrency shared by the package. |
| `limit_rate` | positive int or string | unlimited | No | Aggregate bytes-per-second limit. Strings may use a `k` or `m` suffix, such as `"20m"`. |

Unknown package and repository options are rejected.

## Repository item options

| Option | Type | Default | Required | Description |
|--------|------|---------|----------|-------------|
| `src` | string | (none) | Yes | Repository root using `http`, `https`, or `ftp`. URL credentials, queries, fragments, whitespace, `$`, `#`, and path traversal are rejected. |
| `dst` | string | (none) | Yes | Safe, non-empty relative directory below `settings.dst`. Absolute paths, `.`, `..`, backslashes, and overlapping paths are rejected. |
| `dist` | string or list of strings | discovered | No | Distribution names for a regular repository, or flat paths ending in `/`, such as `"./"`. Comma-separated strings are accepted. |
| `section` | string or list of strings | discovered | No | Components to mirror for a regular repository. Not accepted for flat repositories. |
| `arch` | string or list of strings | discovered | No | Binary architectures for a regular repository. Use `source`, not an architecture named `source`, for source packages. Not accepted for flat repositories. |
| `source` | bool | package `source` | No | Include source indexes and packages for this item. |
| `check_gpg` | bool | `true` | No | Verify repository metadata during discovery and native mirroring. Set to `false` explicitly for an unsigned repository. |
| `keyring` | absolute path or list of paths | (none) | Required when `check_gpg` is `true` | Trusted local keyrings for this item. Keys are not downloaded or shared between items. |

Omitted selections are discovered for every run. Regular repositories use the
`dists/` listing and Release metadata. Flat repositories can be selected
explicitly with paths ending in `/`; otherwise a root Release file containing
direct `Packages` or enabled `Sources` indexes identifies a flat repository.
The apt-mirror2 v16 backend still requires Release metadata even when signature
verification is disabled.

Discovery prefers `InRelease` and falls back to `Release` plus `Release.gpg`
only when `InRelease` is missing. Metadata and listings are bounded by size,
entry count, and time. Automatic discovery also refuses to remove an existing
distribution that disappears from a later listing; set `dist` explicitly when
you intend to narrow a mirror.

The worker rejects symlinks and escapes in repository destination paths. It
creates a private temporary directory below `settings.dst` for generated config
and working files, then removes it after success, failure, or handled
termination. Successful repositories are cleaned automatically with native
deletion count and size safety ratios of `0.4`.

## Example

This entry is ready to place inside the top-level `packages` object:

```json
"cuda": {
  "name": "CUDA",
  "id": "cuda",
  "href": "/cuda",
  "synctype": "apt-mirror2",
  "syncrate": "PT6H",
  "link": [{ "rel": "HOME", "href": "https://developer.nvidia.com/cuda-toolkit" }],
  "settings": {
    "hidden": false,
    "src": "https://developer.download.nvidia.com/compute/cuda/repos/",
    "dst": "/srv/mirror/cuda",
    "options": {
      "source": false,
      "nthreads": 8,
      "limit_rate": "20m",
      "config": [
        {
          "src": "https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/",
          "dst": "ubuntu2404/x86_64",
          "dist": ["./"],
          "keyring": ["/usr/share/keyrings/cuda-ubuntu2404.gpg"]
        }
      ]
    }
  }
}
```

Repository availability and keyring names in this example are illustrative.
Install the upstream signing key locally before enabling the package.
