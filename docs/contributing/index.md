# Contributing

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
