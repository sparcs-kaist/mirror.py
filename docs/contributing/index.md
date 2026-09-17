# Contributing

## Development setup

mirror.py requires Python 3.10 or later and runs on Linux only. The project uses `uv` for
package management.

Install the package in editable mode:

```bash
uv sync --group docs
```

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

Documentation is deployed through Cloudflare Workers Builds, not GitHub Actions.
The root `wrangler.toml` serves the generated Sphinx site as static assets without
a Worker script. Its custom build command builds both the editor and the HTML
documentation before deployment.

In Cloudflare, connect this repository to a Worker named `mirror-py-docs` and use:

| Setting | Value |
|---------|-------|
| Root directory | Repository root |
| Build command | `python3 -m pip install uv` |
| Deploy command | `npx wrangler deploy` |
| Production branch | `main` (or `feat/docs-sphinx` while testing this branch) |
| Build variable | `NODE_VERSION=22` |
| Build variable | `PYTHON_VERSION=3.13` |

The build command installs `uv`, which is required by the documentation build.
Wrangler runs `npm ci`, builds the editor, and builds Sphinx through its custom
build hook; do not repeat these steps in the dashboard build command.
The Worker name in Cloudflare must match `name` in `wrangler.toml`.

For a local deployment, install Node.js 22.12 or later, Python 3.10 or later, and
`uv`, then run from the repository root:

```bash
npx wrangler login
npx wrangler deploy
```

To validate the build and deployment configuration without publishing:

```bash
npx wrangler deploy --dry-run
```

This deploys to Workers, not to an existing Cloudflare Pages project. Existing
Pages domains are not migrated automatically. Connect the desired domain to the
Worker in Cloudflare when ready to switch traffic.

See the [Cloudflare static assets guide](https://developers.cloudflare.com/workers/static-assets/get-started/)
and [Workers Builds configuration](https://developers.cloudflare.com/workers/ci-cd/builds/configuration/).

## Keeping sync-method docs in sync

The option pages under `docs/sync-methods/` and `config.md` document the configuration
fields accepted by each sync module. When you change a sync module's options — adding, removing,
or renaming a field — you must update both the relevant `sync-methods/` page and `config.md`
in the same pull request.

## Integration testing

```{include} ../../tests/integration/README.md
:heading-offset: 2
```
