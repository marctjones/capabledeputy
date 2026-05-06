# Contributing to CapableDeputy

Thanks for your interest. CapableDeputy is in pre-alpha; see [ROADMAP.md](ROADMAP.md) for the current phase and what's open for contribution.

## Development setup

CapableDeputy uses [`uv`](https://docs.astral.sh/uv/) for dependency and environment management.

```bash
uv sync --all-groups
uv run pytest
```

Common commands:

| Command | What it does |
|---|---|
| `uv run pytest` | Run the test suite with coverage |
| `uv run ruff check` | Lint |
| `uv run ruff format` | Format |
| `uv run pyright` | Type-check |
| `uv run capdep --help` | Run the CLI |

## Code style

- `ruff format` and `ruff check` must pass.
- `pyright` must pass.
- Tests required for new functionality. Coverage targets are documented in DESIGN.md §12.

## Comments

Default to no comments. Add one only when the *why* is non-obvious. See DESIGN.md §3 for the security-relevant invariants that must be preserved.

## Commits

Write commit messages that explain *why*, not *what*. The diff already shows the *what*. Keep subjects under 70 characters.

## Reporting security issues

Do not open public issues for security-sensitive findings. Contact the maintainers directly.
