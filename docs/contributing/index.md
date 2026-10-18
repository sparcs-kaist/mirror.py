# Contributing

Use this guide when changing mirror.py: set up the development environment,
follow the Python conventions below, preserve the master-worker and persistence
contracts, and verify the behavior affected by your change. These conventions
apply to new and modified code; keep unrelated code unchanged.

## Development setup

mirror.py requires Python 3.10 or later and runs on Linux only. The project uses `uv` for
package management.

Install the package in editable mode:

```bash
uv sync --group docs
```

The default `dev` dependency group includes pytest and pytest-docker. It replaces
the former `.[dev]` extra; use `uv sync` for development without documentation
dependencies. Integration images install the local wheel and its `apt-mirror2`
extra, so Python tool versions follow project metadata rather than separate pins.

Dependency updates retain Python 3.10 and Node 22.12 compatibility. Bandersnatch
uses environment markers: 6.5.0 on Python 3.10, 6.6.0 on Python 3.11, and 8.0.0
on Python 3.12 and later. Sphinx 8.1.3 and MyST Parser 4.0.1 remain pinned for
Python 3.10 support. Resolve Python and npm lockfiles when updating dependencies,
then run the unit, integration, editor, and documentation checks below.
Runtime setuptools is limited to 81.x only on Python 3.10 because bandersnatch
6.5 imports `pkg_resources`, which setuptools 82 removed. Isolated package builds
use the current setuptools build backend independently.

Run the unit test suite:

```bash
uv run pytest
```

Run the integration tests (requires Docker):

```bash
uv run pytest -m integration
```

Run the default and integration suites sequentially because they can share
temporary resources. The default run excludes tests marked `integration`.

## Change scope and design

Read the relevant implementation, callers, and tests before editing. Define
the expected result and the smallest check that can prove it. For multi-step
changes, use a short plan with a verification step for each outcome.

- Modify only files and lines needed for the requested behavior. Match the
  surrounding style without reformatting or refactoring adjacent code.
- Prefer the existing responsible module, helper, type, or dependency. Add a
  file or abstraction only when the behavior or architecture requires it.
- Avoid speculative options, extension points, and handling for impossible
  cases. Refactor when ownership is unclear, not merely to reduce line count.
- Remove imports, variables, and functions made unused by your change. Report
  unrelated dead code rather than removing it.
- Resolve ambiguity before changing public behavior, security, persisted data,
  or another outcome that is difficult to reverse. State reasonable assumptions
  for routine implementation choices.
- Prefer the standard library and installed dependencies. Obtain agreement
  before introducing a new third-party package.

## Python coding style

### Functions and module structure

Write reusable functions with one responsibility. Name helpers with an action
and subject, such as `parse_date_string` or `build_sync_command`. Pass inputs
explicitly, return results explicitly, and keep side effects at the layer that
owns them. Extract meaningful logic into named functions that can be used
independently without hidden external state.

Keep worker functions short. A coordinator may remain longer when that keeps
the execution sequence clear; do not split it into artificial helpers. Use
comments to explain meaningful stages where needed. Prefer flat modules and
functions when classes do not provide necessary state or behavior. Preserve
the project's existing dataclasses and module ownership.

Avoid new global dependencies. If a function uses global state, declare the
global at the beginning of the function and keep its ownership explicit.

### Formatting and annotations

- Follow PEP 8 except for its line-length guidance. Keep every `def` signature
  on one line, regardless of length, including its type annotations.
- Annotate every function's parameters and return value. Use `-> None` for
  functions that do not return a value, and keep syntax compatible with Python
  3.10.
- Use descriptive English identifiers. Prefer explicit steps over nested
  conditional expressions or dense comprehensions.
- Write comments in English. Favor self-explanatory code; when explanation is
  needed, put a block comment above the relevant code and explain the constraint
  or reason. Avoid decorative headings and comments that repeat the code.
- Keep imports consistent with the module's neighbors. Sync modules start with
  imports rather than a module docstring and place `mirror` imports before
  standard-library imports.
- Do not use emojis in identifiers, comments, logs, error messages, CLI or UI
  output, or commit messages.

### Docstrings

Every function used outside its module needs a full English docstring with a
one-line description, every argument, and the return value. Types belong in the
annotated signature. Internal helpers may use a one-line docstring. Use `Args:`
and `Returns:` sections consistently across the project, including sync modules.

For example, a boundary validator can be documented as follows:

```python
def validate_retry_count(value: int) -> int:
    """Validate the configured retry count.

    Args:
        value: Requested number of retries.

    Returns:
        The validated retry count.

    Raises:
        ValueError: If the value is not an integer or is negative.
    """
    if type(value) is not int or value < 0:
        raise ValueError("Retry count must be a non-negative integer")
    return value
```

### Validation and error handling

Validate untrusted input at system boundaries: configuration loading, socket
requests, remote metadata, and external-tool command construction. Preserve
existing authentication, authorization, path checks, signature verification,
metadata size and time limits, and concurrency protections.

Raise specific exceptions with clear messages. Never swallow exceptions
silently. Catch failures at the layer that can report or recover from them,
and preserve the existing sync completion and cleanup behavior.

Pass external commands as argument lists rather than interpolated shell
strings. Tools that interpret their own command or configuration language
still need validation and escaping for that language. Keep credentials out of
logs and error messages.

### Logging and terminal output

Use the existing `mirror.logger` facilities and per-package loggers. Preserve
their formatting, rotation, and compression rather than introducing separate
handlers or bare `print()` calls. Use the existing Click or `prompt_toolkit`
output path for CLI interactions, with a plain-text fallback for styled output.

Use `uv` for Python package management. When selecting Python explicitly,
specify a version, not an interpreter path. Leave other `uv` settings at their
defaults, including its cache location.

### Dependencies and separate services

Prefer the standard library and existing dependencies. Do not introduce
dependencies that require separately deployed or managed services, such as
Redis or MySQL, unless the requirement and adoption have been explicitly
agreed in the issue before implementation. This applies both to direct service
integrations and to libraries that require such services.

Use the existing filesystem, JSON state, and Unix sockets when they meet the
requirement. Evaluate dependencies by the services they require and the
operational burden they introduce, rather than by the number of imports.

## Architecture and state ownership

The master schedules syncs and owns package state. The worker owns sync
subprocesses, allowing them to continue when the master restarts. Read the
[architecture overview](../architecture/overview.md) before changing this flow.

| Area | Responsible module |
| --- | --- |
| CLI dispatch and command implementations | `mirror/__main__.py`, `mirror/command/` |
| Configuration loading and state persistence | `mirror/config/`, `mirror/structure/` |
| Unix socket RPC and completion notifications | `mirror/socket/` |
| Sync preparation and lifecycle | `mirror/sync/` |
| Subprocess creation, tracking, and pruning | `mirror/worker/` |
| Events, logging, and plugin registration | `mirror/event/`, `mirror/logger/`, `mirror/plugin/` |

`/etc/mirror/config.json` is operator-managed and read-only during daemon and
worker runtime. Only initial provisioning through `mirror setup` writes it.
Runtime status, error counts, log paths, and timestamps belong in `stat.json`;
there is intentionally no `Config.save()`.

When adding a persisted status field, extend `Package.StatusInfo` or the
appropriate state dataclass, preserve loading through `from_dict()`, and expose
the field through `Package.to_dict()` so `save_stat_data()` persists it. If web
clients need it, also update `generate_and_save_web_status()`. Preserve atomic
state writes and locking. See [state files](../guide/state-files.md).

## Developing sync methods

Read `mirror/sync/rsync.py`, `ftpsync.py`, and `lftp.py` before changing a sync
method. Keep method-specific discovery and helpers in `mirror/sync/<method>.py`
unless the architecture requires sharing them. Place constants and data types
near the top, keep `setup` and `execute` easy to find, and group related helpers.

1. Separate command building from execution. A builder returns arguments and
   environment settings without network, filesystem, or worker side effects.
2. Prepare jobs through `execute(package, logger, trigger="auto")` and delegate
   long-running subprocesses to `mirror.socket.worker.execute_command()`.
   Preserve the package job ID, configured UID/GID, environment, and log path.
   Discovery requiring the worker's identity or environment belongs there too.
3. Report failures before delegation through `mirror.sync.on_sync_done()`.
   After delegation, let worker completion drive the normal result lifecycle.
4. Give generated tool configurations an explicit temporary-directory owner.
   Keep them available and accessible to the worker until execution finishes,
   then clean up on success, failure, and handled termination.
5. Expose the method through `plugin()` using `mirror.plugin.sync_plugin` and
   register built-in methods in the `mirror.sync` entry points in
   `pyproject.toml`. See the [plugin author guide](../plugins/index.md).

## Verification and review

Start with the smallest test that exercises the changed behavior, for example
`uv run pytest tests/test_sync_worker_delegation.py` for worker delegation.
Broaden testing when the affected scope, a failure, or an unresolved risk
requires it. Add tests for behavior changes, regressions, trust boundaries, and
important edge cases. Do not add tests that merely restate a refactor or a
reversible, low-impact edit.

For sync behavior changes, cover command building and worker delegation with
unit tests and real synchronization with the relevant integration tests. For
readability-only code changes, use existing tests to demonstrate that behavior
is preserved. For prose-only documentation changes, build the documentation.

Before submitting a change, inspect the diff for scope, unused code introduced
by the change, credential exposure, and missing documentation. Describe the
resulting behavior, the reason for the change, checks run, and any remaining
limitations. Use Conventional Commit subjects, for example
`fix: preserve worker job state after master restart`.

## Building documentation

The configuration editor requires Node.js 22.12 or later and npm. From a clean
checkout, install its locked dependencies, build its local assets, and build
the Sphinx site with one command:

```bash
npm --prefix docs/editor run docs:build
```

The generated editor assets are ignored by Git. An HTML build fails with a clear
message if they are absent. After the initial build, rerun `npm --prefix
docs/editor run build` when editing frontend sources; prose-only changes can
use the normal `uv run --group docs sphinx-build -b html -W --keep-going docs
docs/_build/html` command.

Serve the site locally instead of opening `file://` URLs, because Monaco uses
Web Workers:

```bash
python -m http.server 8000 --directory docs/_build/html
```

Open `http://localhost:8000/guide/config-editor.html`. Editor assets and workers
are bundled with the site; no CDN or external schema service is used.

Run editor unit and browser tests:

```bash
npm --prefix docs/editor test
uv run pytest tests/test_editor_contract.py
cd docs/editor
npx playwright install chromium firefox
npm run test:browser
```

The editor schema is shared by the forms and Monaco. Python loaders and sync
validation remain authoritative. When changing configuration fields, update
the editor schema and its contract fixtures as well as the reference docs.

## Deploying documentation

Configure Cloudflare Pages with the repository root as the root directory:

| Setting | Value |
|---------|-------|
| Build command | `python3 -m pip install uv && npm --prefix docs/editor run docs:build` |
| Build output directory | `docs/_build/html` |
| Build variable | `NODE_VERSION=22` |
| Build variable | `PYTHON_VERSION=3.13` |

The build command installs `uv` and the editor's locked npm dependencies, then
builds the editor and Sphinx HTML. Cloudflare publishes the output directory.

## Keeping sync-method docs in sync

The option pages under `docs/sync-methods/` and `config.md` document the configuration
fields accepted by each sync module. When you change a sync module's options — adding, removing,
or renaming a field — you must update both the relevant `sync-methods/` page and `config.md`
in the same pull request.

## Integration testing

```{include} ../../tests/integration/README.md
:heading-offset: 2
```
