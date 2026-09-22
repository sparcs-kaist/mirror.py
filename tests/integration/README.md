# Integration Tests

End-to-end tests for `mirror.py` running against real rsync, ftpsync (archvsync), lftp, debmirror, and apt-mirror2 upstream fixtures in Docker. Verifies that the master-worker daemon pair correctly performs syncs, recovers from process restarts, and persists state — none of which is provable with the in-process unit suite under `tests/`.

These tests are **deselected by default** (`pyproject.toml` sets `addopts = -m 'not integration'`). Run them explicitly:

```bash
uv sync
uv run pytest -m integration -v
```

The mirror image installs a wheel built **from the current source tree** by a
session-scoped fixture (`built_wheel` in `conftest.py`). This means tests
exercise in-progress changes immediately — no PyPI round-trip needed. The
wheel is placed in `docker/mirror/dist/` (gitignored) and rebuilt only when
the source SHA changes.

First run builds the wheel and five Docker images. Subsequent
runs reuse both the wheel and cached images.

## Container topology

Five containers share the default bridge network defined in `docker-compose.yml`:
`mirror`, `rsync-fixture`, `ftpsync-fixture`, `lftp-fixture`, and `apt-fixture`.
The diagram below shows the original rsync/ftpsync paths; the shared APT fixture
serves both debmirror and apt-mirror2 over HTTP port 8000 and FTP port 2121.

```
┌────────────────────┐    ┌────────────────────┐
│   rsync-fixture    │    │  ftpsync-fixture   │
│  ubuntu + rsyncd   │    │  ubuntu + rsyncd   │
│  module [data]     │    │  module [debian]   │
└─────────▲──────────┘    └──────────▲─────────┘
          │ rsync://                 │ rsync://
          └────────────┬─────────────┘
                       │
         ┌─────────────┴───────────────────────────┐
         │ mirror (ubuntu:22.04, python3.10)       │
         │   systemd (PID 1)                       │
         │     ├─ mirror-worker.service            │
         │     └─ mirror.service                   │
         │   /var/run/mirror/{master,worker}.sock  │
         │                                         │
         │   bind-mounts to host:                  │
         │     /srv/publish    ─► ${TMP}/publish   │
         │     /var/lib/mirror ─► ${TMP}/state     │
         │     /var/log/mirror ─► ${TMP}/log       │
         └─────────────────────────────────────────┘
```

`${TMP}` is a per-session host temp dir (set as the `INTEGRATION_TMP` env var by `conftest.py`). pytest reads `stat.json`, package logs, and the published mirror tree directly from these bind-mounted paths.

Container names are pinned (`container_name: mirror|rsync-fixture|ftpsync-fixture`) so `docker exec mirror …` works regardless of the compose project name.

## Why master and worker are split

Worker spawns the actual sync subprocesses (`rsync`, `ftpsync`) as its own children. Master only schedules and tracks state. When master restarts:

- Worker keeps running.
- Already-running rsync/ftpsync subprocesses keep running (parent is worker, not master).
- New master reconnects to worker via `worker.sock` and resumes receiving `job_finished` notifications.

This is what `test_master_restart.py` proves: capture worker PID, restart master mid-sync, assert worker PID is unchanged and the sync still reaches `ACTIVE`.

## How tests interact with the stack

All test interactions go through the `mirror_stack` fixture (defined in `conftest.py`, implemented in `helpers.py`). No test imports `mirror.*` directly — the test process talks to the containerized version exclusively.

| Action | Mechanism |
|---|---|
| Trigger sync | `mirror_stack.trigger_sync(pkgid)` runs `python -c` inside the mirror container, importing `mirror.socket.master.start_sync(pkgid)` |
| Wait for status | `mirror_stack.wait_for_status(pkgid, "ACTIVE")` polls `${TMP}/state/stat.json` from host |
| Restart process | `mirror_stack.restart_process("master")` runs `systemctl restart mirror.service` via `docker exec` |
| Inspect publish tree | Read `${TMP}/publish/<pkgid>/…` directly from host |
| Swap upstream content | `mirror_stack.swap_rsync_fixture_tree(...)` runs `docker cp` into rsync-fixture |
| Clone failure injection | Temporary git wrapper inside the mirror container; fixture networking remains available |

## Package source

The mirror image installs a locally-built wheel from `docker/mirror/dist/`:

```dockerfile
COPY dist/mirror_py-*.whl /tmp/
RUN for wheel in /tmp/mirror_py-*.whl; do python3 -m pip install --no-cache-dir "$wheel[apt-mirror2]"; done
```

`conftest.py:built_wheel` (session-scoped) runs `uv build --wheel` against the
current source tree and places the artifact in `dist/`. The build is gated by
a SHA of `mirror/**` plus `pyproject.toml`, so unchanged source skips rebuild.

Public releases are independent: a `v*` tag push triggers
`.github/workflows/pypi.yaml`, which uses PyPI Trusted Publisher (OIDC) to
upload the artifact. The integration suite does NOT pull from PyPI — local
testing always uses the working-tree build.

## Per-test isolation

Each test starts with a fresh `mirror_stack`:

1. Stop both master and worker services, including active sync subprocesses.
2. Clear publish/state and package-log contents inside the container, preserving bind-mount directories.
3. Start worker, wait for its socket, then start master and wait for readiness.
4. Wait for fresh state and for initial automatic syncs to settle before yielding.

Tests that change configuration, fixture content, or executable wrappers restore
those changes in `finally` blocks. Run suites sequentially: container names and
fixture resources are shared.

## Test scenarios

| File | Scenario |
|---|---|
| `test_preflight.py` | Runs archvsync's `bin/ftpsync sync:all` directly inside the mirror container against the fixture tree, isolating fixture-layout validation from mirror.py orchestration |
| `test_e2e_rsync.py` | Basic rsync; FFTS short-circuit when upstream unchanged; full sync when FFTS file changed |
| `test_e2e_debmirror.py` | Signed HTTP option discovery, explicit subsets, updates and cleanup, and failure recovery |
| `test_e2e_apt_mirror2.py` | Multiple signed flat repositories, source indexes, automatic cleanup, hash and key failures, and FTP discovery |
| `test_e2e_ftpsync.py` | Basic ftpsync; offline fallback exercises the embedded base64 archvsync (`mirror/sync/_ftpsync_script.py`) by forcing git clone failure while keeping fixture networking available |
| `test_master_restart.py` | Master restart during a 200MB sync does not kill worker subprocess (PID stable); master reconnects and sync completes |
| `test_worker_restart.py` | Worker restart recovery; master gracefully handles worker unavailability |
| `test_config_reload.py` | Restart, CLI, and SIGHUP reload; validation, concurrent requests, and removal of idle or actively syncing packages |
| `test_error_retry.py` | Failed package retries after `errorcontinuetime` and increments errorcount |
| `test_state_persistence.py` | `lastsync` survives master+worker restart |
| `test_log_rotation.py` | Per-package log file is gzip-compressed after sync completes |

## Fixtures

### apt-fixture (`docker/apt-fixture/`)

One Python HTTP/FTP server exposes a shared signed Debian archive at
`http://apt-fixture:8000/debian` and `ftp://apt-fixture:2121/debian`.
Static `v1/debian/` and `v2/debian/` trees contain
multiple distributions, components, and architectures, plus binary and source
indexes and signed `InRelease` and `Release` metadata. The `allonly`
distribution intentionally has no `InRelease`, exercising the signed
`Release.gpg` fallback. The `stable` alias duplicates `bookworm` metadata.
Only public keyrings are stored; private keys are not included.
Compose mounts the Debian keyring read-only at `/etc/mirror/debmirror-test.gpg`.
The flat repositories retain their separate keys, mounted at
`/etc/mirror/apt-mirror2-ubuntu2404.gpg` and
`/etc/mirror/apt-mirror2-ubuntu2604.gpg`. Both tools use the same Debian tree;
the mirror image installs debmirror and apt-mirror2 for real subprocess tests.

`test_e2e_debmirror.py` exercises master socket requests, worker subprocess
execution, completion notifications, persisted status and package logs:

- Omitted `dist`, `section`, and `arch` discover all binary targets, deduplicate
  the suite alias, and leave source packages disabled by default.
- Updating to v2 discovers a new distribution, component, and architecture,
  downloads their payloads, preserves a pool file still referenced by another
  distribution, and removes the obsolete v1 payload.
- Explicit list selections restrict the mirror and `source: true` downloads
  source payloads.
- Tampered signed metadata and denied or incomplete directory listings fail
  without deleting the existing mirror. An explicit all-only distribution
  bypasses listing and exercises debmirror's `--arch none` behavior.

Only `/debian` and its listing controls are restored after each debmirror test,
including failures. Flat repository contents are left intact.
The small `debmirror-test` package also auto-syncs when the shared stack resets
for other integration tests. Its hourly schedule avoids background updates
during the explicitly triggered debmirror scenarios.

Run only these scenarios with:

```bash
uv run pytest -m integration tests/integration/test_e2e_debmirror.py -v
```

They are also included in the full `uv run pytest -m integration -v` suite.

The same fixture also serves `/ubuntu2404/`, `/ubuntu2604/`, and `/sourceonly/`.
The two CUDA-shaped flat repositories use different keys. Their static v1 and
v2 trees cover updates, retained files, automatic cleanup, source indexes, and
a source-only repository. apt-mirror2 tests reset only these three directories;
they never replace the shared data root or Debian archive. Its FTP test uses
the shared Debian tree, including `allonly` for signed `Release.gpg` fallback.

`test_e2e_apt_mirror2.py` runs the real apt-mirror2 v16 process through the
master and worker. It also verifies repository-specific key isolation, package
hash failures, cleanup suppression after errors, and recovery.

### rsync-fixture (`docker/rsync-fixture/data/`)
Minimal tree exposed via rsyncd's `[data]` module:
- `fullfiletimelist-test` — FFTS metadata. Stable across reads, so re-syncs short-circuit.
- `README` — single-line marker
- `dists/test/Release` — Debian-style release file
- `pool/main/p/pkg/pkg_1.0.deb` — zero-byte placeholder

### ftpsync-fixture (`docker/ftpsync-fixture/data/`)
Minimal Debian-style archive exposed via rsyncd's `[debian]` module:
- `Project/trace/master` — required by archvsync stage 1
- `dists/test/Release` + zero-byte `Release.gpg`
- `pool/main/p/pkg/pkg_1.0.deb` — zero-byte
- `ls-lR.gz` — zero-byte

### `fixtures/tree_v2/`
Alternate content used by `test_ffts_changed_triggers_full_sync`:
- Modified `fullfiletimelist-test` adds `NEW_FILE` entry → FFTS dry-run reports change → full sync.
- `NEW_FILE` itself appears in publish tree after sync.

The test restores tree v1 in a `finally` block to keep later tests deterministic.

## Configuration (`docker/mirror/config.json`)

Six packages baked into the image:

| Package | synctype | src | syncrate | Purpose |
|---|---|---|---|---|
| `rsync-test` | rsync | `rsync://rsync-fixture/data` | `PT5S` | Auto-syncing rsync target with FFTS enabled |
| `ftpsync-test` | ftpsync | `ftpsync-fixture` (bare hostname) + path `debian` | `PT1H` | Manually triggered ftpsync target |
| `lftp-test` | lftp | `ftp://lftp-fixture/data` | `PT1H` | FTP target |
| `debmirror-test` | debmirror | `http://apt-fixture:8000/debian` | `PT1H` | Signed Debian archive target |
| `apt-mirror2-test` | apt-mirror2 | two flat HTTP repositories | `PT1H` | Multi-source signed flat archive target |
| `error-test` | rsync | `rsync://rsync-fixture/nonexistent` | `PT5S` | Always fails to verify error-retry behavior |

`errorcontinuetime` is set to `10` seconds for fast retry observation.

## Known caveats

- `syncrate: "PUSH"` disables automatic scheduling. Tests trigger manual-only packages explicitly.
- Preflight is an independent tool-level check; no pytest dependency-ordering plugin is required.
- The offline-fallback test restores its temporary git wrapper even when assertions fail.
- rsyncd in fixture containers runs as `uid = root` for simplicity; this is acceptable for a sealed test container but is not a production pattern.

## Layout reference

```
tests/integration/
├── conftest.py              # session/per-test fixtures, INTEGRATION_TMP
├── helpers.py               # MirrorStack class — all docker/host interactions
├── docker-compose.yml       # 5 services with pinned container_name
├── docker/
│   ├── rsync-fixture/       # Dockerfile + rsyncd.conf + data/
│   ├── ftpsync-fixture/     # Dockerfile + rsyncd.conf + data/
│   ├── apt-fixture/         # Shared HTTP/FTP server + signed flat and Debian repositories
│   └── mirror/             # Dockerfile + config.json + dist/ (gitignored); systemd services from mirror setup
├── fixtures/
│   └── tree_v2/             # Alternate rsync content for FFTS-changed test
└── test_*.py                # Integration scenarios
```
