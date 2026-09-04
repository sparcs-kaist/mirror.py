# Contributing

## Development setup

mirror.py requires Python 3.10 or later and runs on Linux only. The project uses `uv` for
package management.

Install the package in editable mode:

```bash
uv pip install -e .
```

Run the unit test suite:

```bash
uv run pytest
```

Run the integration tests (requires Docker):

```bash
uv run pytest -m integration
```

## Keeping sync-method docs in sync

The option pages under `docs/sync-methods/` and `docs/config.md` document the configuration
fields accepted by each sync module. When you change a sync module's options — adding, removing,
or renaming a field — you must update both the relevant `sync-methods/` page and `config.md`
in the same pull request.

## Integration testing

```{include} ../../tests/integration/README.md
:heading-offset: 2
```
