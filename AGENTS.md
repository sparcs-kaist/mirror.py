# Agent Instructions for Mirror.py

Mirror.py is a Linux daemon for maintaining local repository mirrors. Read
[README.md](README.md) for the project overview, supported sync methods,
architecture, source layout, runtime paths, and CLI documentation.

## Workflow and Git

- Follow [CONTRIBUTING.md](CONTRIBUTING.md). Every change requires an issue;
  settle the problem, scope, and development direction in the issue before
  implementation. No separate maintainer approval is required to start work.
- Discuss implementation problems, tests, and review in the PR, and link the
  corresponding issue. Return substantial direction or scope changes to the
  issue and reflect the agreed outcome in the PR. Maintainers decide merges.
- Read relevant code, callers, and tests before editing. Keep changes within
  the agreed scope and preserve unrelated work.
- Reuse the branch dedicated to the current task. If none exists, create a
  `feat/<name>` or `fix/<name>` branch before feature or fix changes.
- Commit and push only when the user explicitly requests them. Use
  Conventional Commit subjects and `--no-gpg-sign` when committing because
  interactive GPG signing is unavailable. Do not force-push, push directly to
  `main` or `master`, or merge without explicit permission.

## Development and Verification

- Follow the Python, docstring, logging, and dependency conventions in the
  [development guide](docs/contributing/index.md#python-coding-style).
- Python >= 3.10, Linux only. Use `uv` with its default settings.
- Prefer the standard library and existing dependencies. Do not introduce
  dependencies that require separately deployed or managed services, such as
  Redis or MySQL, unless the requirement and adoption have been explicitly
  agreed in the issue. Reuse the existing filesystem, JSON state, and Unix
  sockets when they meet the requirement.
- Install development dependencies: `uv sync`.
- Run default tests: `uv run pytest` (excludes integration tests).
- Run integration tests: `uv run pytest -m integration -v` (requires Docker
  and Compose). Run default and integration suites sequentially.
- Install documentation dependencies: `uv sync --group docs`.
- Build documentation and editor assets: `npm --prefix docs/editor run docs:build`
  (requires Node >= 22.12). With assets already built, prose-only site changes
  can use `uv run --group docs sphinx-build -b html -W --keep-going docs docs/_build/html`.
- Run checks appropriate to the change. Cover behavior changes and regressions
  with relevant tests; use existing tests for readability-only code changes.
  Validate formatting and links for standalone Markdown changes. Report checks,
  results, and reasons for checks not run. See the
  [verification guide](CONTRIBUTING.md#verify-your-change).

## Architecture and Persistence Constraints

- The master owns scheduling and package state. The worker owns long-lived
  sync subprocesses, which must survive master restarts. Preserve socket
  authentication, authorization, completion notifications, and concurrency
  protections.
- `/etc/mirror/config.json` is read-only during daemon and worker runtime.
  Only initial provisioning through `mirror setup` writes it. Never write
  runtime changes through `mirror.confPath` or introduce `Config.save()`.
- Runtime status, error counts, log paths, and timestamps belong in `stat.json`.
  Extend `Package.StatusInfo` or the appropriate state dataclass, preserve
  loading through `from_dict()`, and emit fields through `Package.to_dict()`
  for `save_stat_data()`. Update `generate_and_save_web_status()` when web
  clients need the field. Preserve atomic writes and state locking.

## Sync Module Development

- Read the relevant module and use `mirror/sync/rsync.py`, `ftpsync.py`, and
  `lftp.py` as references. Follow the
  [sync development guide](docs/contributing/index.md#developing-sync-methods)
  for module organization and plugin registration.
- Separate command construction from execution. Builders return arguments
  and environment settings without network, filesystem, or worker side effects.
  Validate user-controlled values and pass command arguments separately.
- Use `execute(package, logger, trigger="auto")` to prepare and delegate jobs
  through `mirror.socket.worker.execute_command()`. Preserve the package job
  ID, configured UID/GID, environment, and log path.
- Run long-lived subprocesses through the worker. Discovery requiring the
  worker's identity or environment belongs there too.
- Report failures before delegation through `mirror.sync.on_sync_done()`;
  let worker completion drive the normal result lifecycle after delegation.
- Keep credentials out of logs and preserve signature verification, path
  validation, and metadata size and time limits.
- Give generated tool configurations an explicit temporary-directory owner.
  Keep them accessible to the worker until the subprocess finishes. Clean up
  on success, failure, and handled termination.
- For sync behavior changes, cover command building and worker delegation with
  unit tests and real synchronization with relevant E2E integration tests.

### RC Release Bump

When asked to bump the project to a new release candidate version, perform only the release-version update unless the user explicitly requests more. Unless the user says otherwise, follow this order:

1. Run the full test suite first, including the default non-integration tests and the integration tests, for example `uv run pytest` and `uv run pytest -m integration -v`.
2. After the tests pass, update the version in the project metadata and source version files, including `pyproject.toml`, `mirror/__init__.py`, and `uv.lock` when applicable. Modify only the version-related code.
3. Verify the updated version, for example with `uv run python -c "import mirror; print(mirror.__version__)"`.
4. Show the changed files and ask the user to create the commit. The user makes the release commit themselves; do not commit the version bump. Suggest a Conventional Commit message, for example `chore: prepare 1.0.0rc15 release`.
5. After the user confirms the commit is done, create the matching git tag, for example `v1.0.0rc15`, and push.
