# Contributing

Contributions to mirror.py start with an issue. Use the issue to agree on the
problem, scope, and development direction, then use a pull request to discuss
implementation and review the change. This process applies to every change,
including bug fixes, features, and documentation updates.

## Start with an issue

Search [existing issues](https://github.com/sparcs-kaist/mirror.py/issues) before
opening a new one. If an issue already covers your change, continue the
conversation there. Every pull request must link to a corresponding issue.

Include the information relevant to your proposal:

- **Bug reports:** mirror.py version, execution environment, reproduction steps,
  expected and actual behavior, and relevant logs with credentials and other
  sensitive information removed.
- **Features and improvements:** the problem to solve, use cases, and the
  proposed scope of the change.
- **Documentation changes:** what is incorrect or missing, the affected
  documentation, and the proposed correction.

You do not need to settle the implementation before opening an issue. Use the
issue discussion to determine the scope and development direction. Keep
communication respectful and support technical discussions with concrete
examples and reproducible information.

## Agree on the direction before implementation

Begin implementation once the issue discussion has settled the problem, scope,
and development direction. Separate explicit maintainer approval is not
required to start work; maintainers decide whether to merge the resulting PR.

Keep most discussion about scope and development direction in the issue. Use
the PR for problems discovered during development, implementation details,
testing, and code review. If an implementation problem requires a substantial
change of direction or scope, return to the issue, resolve the discussion
there, and record the outcome in the PR.

## Set up the development environment

mirror.py runs on Linux and requires Python 3.10 or later. Use `uv` to install
the package and development dependencies from your checkout:

```bash
uv sync
```

For documentation work, include the documentation dependency group:

```bash
uv sync --group docs
```

See the [development guide](docs/contributing/index.md) for detailed setup,
documentation and configuration editor builds, and project architecture.

## Follow the coding conventions

Read the relevant implementation, callers, and tests before editing. Keep the
change focused on the scope discussed in the issue and follow existing module
ownership and style.

- Write reusable functions with one responsibility, action-based names,
  explicit inputs, and minimal side effects.
- Annotate function parameters and return values. Keep every `def` signature on
  one line, regardless of length, and otherwise follow PEP 8 except for its
  line-length guidance.
- Use English identifiers, comments, documentation, and docstrings. Document
  functions used outside their module with `Args:` and `Returns:` sections.
- Preserve input validation, credential protection, concurrency safeguards,
  and the master-worker lifecycle.
- Use existing logging and terminal-output facilities. Do not use emojis in
  code, logs, CLI or UI messages, or commit messages.
- Prefer the standard library and existing dependencies. Discuss new
  third-party dependencies in the issue before adding them.
- Update affected documentation alongside behavior or configuration changes.
- Use Conventional Commit subjects, such as
  `fix: preserve worker job state after master restart`.

The [development guide](docs/contributing/index.md) contains the full Python,
sync-method, state persistence, and logging conventions.

## Verify your change

Run checks appropriate to the change and report the commands and results in
the PR. Add or update tests for behavior changes, including relevant regression
cases and important edge cases. Sync behavior changes require relevant
integration tests as well as unit coverage for command building and worker
delegation.

Run the default test suite with:

```bash
uv run pytest
```

The default run excludes integration tests. Run the relevant integration tests
with Docker and Compose available, for example:

```bash
uv run pytest -m integration -v
```

Run the default and integration suites sequentially. See the
[integration testing guide](tests/integration/README.md) for host requirements
and fixture behavior.

For documentation-only changes, validate the documentation. Build the Sphinx
site when changing site content; see the
[documentation build instructions](docs/contributing/index.md#building-documentation).
For standalone Markdown files, check formatting and links. If you cannot run a
required check, state which check was not run and why in the PR.

## Submit a pull request

Include the following in the PR description:

- A link to the corresponding issue.
- The purpose of the change and a summary of the implementation.
- Verification commands and results, including reasons for checks not run.
- Relevant documentation updates and any known limitations.

Keep implementation questions, development problems, test results, and code
review discussion in the PR. Reflect any revised direction agreed in the issue
in the PR description. Respond to review feedback and keep the description
current as the implementation changes. Maintainers make the final merge
decision.
