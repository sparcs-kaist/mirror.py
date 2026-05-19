# Agent Instructions

## RC Release Bump

When asked to bump the project to a new release candidate version, perform only the release-version update unless the user explicitly requests more.

1. Update the version in the project metadata and source version files, including `pyproject.toml`, `mirror/__init__.py`, and `uv.lock` when applicable.
2. Verify the updated version, for example with `uv run python -c "import mirror; print(mirror.__version__)"`.
3. Show the changed files and ask the user to confirm before creating a commit.
4. Before creating the commit, run the full test suite, including the default non-integration tests and the integration tests, for example `uv run pytest` and `uv run pytest -m integration -v`.
5. After the tests pass and the user confirms, create a Conventional Commit for the release bump, for example `chore: prepare 1.0.0rc15 release`.
6. After the commit succeeds, create the matching git tag, for example `v1.0.0rc15`.
7. Do not push commits or tags unless the user explicitly asks.
