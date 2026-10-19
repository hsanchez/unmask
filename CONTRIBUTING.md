# Contributing to Unmask

## Getting Started

Install Python 3.14+, [uv](https://docs.astral.sh/uv/), Git, and Node.js 24+
(for frontend tests). Fork the repository, clone your fork, and run:

```bash
uv sync --locked
uv run prek install
uv run unmask scan examples/regexes.yaml --taxonomy examples/taxonomy.yaml
```

The local examples and test suite require no provider credentials or dataset
downloads. Install a provider extra only when working on live model integration;
see [provider setup](README.md#provider-setup).

## Required Checks

Run these before submitting a pull request:

```bash
uv sync --locked
uv run prek run --all-files
uv run pytest -W error
node --test tests/frontend/*.test.cjs
uv build
```

Prek runs Ruff formatting/lint checks, mypy, and basedpyright. CI runs these
checks on Linux and macOS and smoke-tests the built wheel. Tests use mocked
providers; keep paid or credential-dependent checks separate.

## Reporting Issues

- Search existing issues before opening a new one.
- Include a minimal reproducer, expected and actual behavior, Python/Unmask
  versions, and operating system.
- Remove credentials and private policy, conversation, and incident data from
  examples and logs. Do not post sensitive security reports in public issues.

## Submitting Pull Requests

1. Create a branch from `main`: `git checkout -b your-feature`
2. Make your changes and add tests where applicable.
3. Run the required checks and update relevant documentation.
4. Open a PR against `main` with a concise imperative title, a description of
   the change, and the checks you ran.

End the PR description with:

```text
Release Notes:

- Added ..., Fixed ..., or Improved ...
```

Use `- N/A` for documentation-only or internal changes without user-facing impact.

## Coding Standards

- Use two-space indentation and full type annotations; follow existing patterns.
- Keep changes focused — one concern per PR.
- Add tests covering behavior, edge cases, and failure paths.
- Reuse existing analysis and solver code rather than duplicating it.
- Discuss new dependencies and substantial API changes before implementing them.

## Optional Agent Tooling

DotAgents is pinned to a public Git revision in `pyproject.toml` and `uv.lock`.
The scripts under `scripts/` are optional development aids, not prerequisites
for running checks. They require [Nushell](https://www.nushell.sh/); GitHub-related
helpers also require an authenticated [GitHub CLI](https://cli.github.com/).

## License

Contributions are submitted under the project's [Apache 2.0 license](LICENSE).
Retain applicable third-party attribution and keep external dataset terms
separate from the project's software license.

## Questions

For general questions, open an issue in this repository. Search existing issues
first and provide enough context for maintainers to reproduce the problem.
