# Integration Tests

End-to-end tests for `mirror.py` running against real `rsync` and `ftpsync` (archvsync) servers in Docker. Verifies that the master-worker daemon pair correctly performs syncs, recovers from process restarts, and persists state — none of which is provable with the in-process unit suite under `tests/`.

These tests are **deselected by default** (`pyproject.toml` sets `addopts = -m 'not integration'`). Run them explicitly:

```bash
uv pip install -e ".[dev]"
uv run pytest -m integration -v
```

First run builds a wheel and three Docker images (~1–2 min). Subsequent runs reuse cached images.

## Container topology

Three containers on the default bridge network defined in `docker-compose.yml`:

```
┌────────────────────┐    ┌────────────────────┐
│   rsync-fixture    │    │  ftpsync-fixture   │
│  alpine + rsyncd   │    │  alpine + rsyncd   │
│  module [data]     │    │  module [debian]   │
└─────────▲──────────┘    └──────────▲─────────┘
          │ rsync://                 │ rsync://
          └────────────┬─────────────┘
                       │
         ┌─────────────┴───────────────────────────┐
         │ mirror (python:3.13-slim)               │
         │   supervisord (PID 1)                   │
         │     ├─ worker  (priority 1)             │
         │     └─ master  (priority 2, startsecs=2)│
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
| Restart process | `mirror_stack.restart_process("master")` runs `supervisorctl restart master` via `docker exec` |
| Inspect publish tree | Read `${TMP}/publish/<pkgid>/…` directly from host |
| Swap upstream content | `mirror_stack.swap_rsync_fixture_tree(...)` runs `docker cp` into rsync-fixture |
| Network isolation | `subprocess.run(["docker", "network", "disconnect", …])` on host (offline fallback test) |

## Wheel build

`conftest.py:built_wheel` (session-scoped):

1. Rewrites `pyproject.toml` and `mirror/__init__.py` version to `1.0.0-rc.test`.
2. Runs `uv build --wheel --out-dir tests/integration/docker/mirror/dist/`.
3. Restores both files in a `try/finally` (restoration runs even on build failure).
4. Subsequent runs check a SHA marker and skip rebuild if source is unchanged.

`tests/integration/docker/mirror/dist/` is gitignored. The mirror Dockerfile `COPY dist/mirror_py-*.whl /tmp/` and `pip install`s it.

## Per-test isolation

Each test starts with a fresh `mirror_stack`:

1. Clear *contents* of the host bind-mount dirs (`publish/`, `state/`, `log/`). The directories themselves are preserved so the docker mount points stay attached.
2. `supervisorctl restart master`. Worker keeps running. This forces master to reload `config.json` and start with empty in-memory package state, isolating tests from prior runs.
3. Wait for master to be `RUNNING` again before yielding.

Worker stays up across tests by design: tests that need worker restart explicitly do `mirror_stack.restart_process("worker")`.

## Test scenarios (14 total)

| File | Scenario |
|---|---|
| `test_preflight.py` | Runs archvsync's `bin/ftpsync sync:all` directly inside the mirror container against the fixture tree, isolating fixture-layout validation from mirror.py orchestration |
| `test_e2e_rsync.py` | Basic rsync; FFTS short-circuit when upstream unchanged; full sync when FFTS file changed |
| `test_e2e_ftpsync.py` | Basic ftpsync; offline fallback exercises the embedded base64 archvsync (`mirror/sync/_ftpsync_script.py`) by disconnecting mirror from the docker network |
| `test_master_restart.py` | Master restart during a 200MB sync does not kill worker subprocess (PID stable); master reconnects and sync completes |
| `test_worker_restart.py` | Worker restart recovery; master gracefully handles worker unavailability |
| `test_config_reload.py` | Add/remove package via config edit + `supervisorctl restart master` (daemon does not implement SIGHUP) |
| `test_error_retry.py` | Failed package retries after `errorcontinuetime` and increments errorcount |
| `test_state_persistence.py` | `lastsync` survives master+worker restart |
| `test_log_rotation.py` | Per-package log file is gzip-compressed after sync completes |

## Fixtures

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

Three packages baked into the image:

| Package | synctype | src | syncrate | Purpose |
|---|---|---|---|---|
| `rsync-test` | rsync | `rsync://rsync-fixture/data` | `PT5S` | Auto-syncing rsync target with FFTS enabled |
| `ftpsync-test` | ftpsync | `ftpsync-fixture` (bare hostname) + path `debian` | `PT1H` | Manually triggered ftpsync target |
| `error-test` | rsync | `rsync://rsync-fixture/nonexistent` | `PT5S` | Always fails to verify error-retry behavior |

`errorcontinuetime` is set to `10` seconds for fast retry observation.

## Known caveats

- `syncrate: "PUSH"` parses to `-1`, which the daemon's auto-trigger condition (`time.time() - lastsync > syncrate`) treats as "always due" rather than "manual only". The ftpsync package therefore uses `PT1H` plus explicit triggering instead of `PUSH`.
- `pytest-dependency` is not installed; the preflight gate uses a module-level cache in `test_e2e_ftpsync.py` rather than declarative test dependencies.
- The offline-fallback test manipulates the docker network from the host; it is marked `xfail` if the network operation fails (e.g., on environments where docker is not the test runner's default).
- rsyncd in fixture containers runs as `uid = root` for simplicity; this is acceptable for a sealed test container but is not a production pattern.

## Layout reference

```
tests/integration/
├── conftest.py              # session/per-test fixtures, wheel build, INTEGRATION_TMP
├── helpers.py               # MirrorStack class — all docker/host interactions
├── docker-compose.yml       # 3 services with pinned container_name
├── docker/
│   ├── rsync-fixture/       # Dockerfile + rsyncd.conf + data/
│   ├── ftpsync-fixture/     # Dockerfile + rsyncd.conf + data/
│   └── mirror/              # Dockerfile + supervisord.conf + config.json + dist/ (gitignored)
├── fixtures/
│   └── tree_v2/             # Alternate rsync content for FFTS-changed test
└── test_*.py                # 9 test files, 14 tests total
```
