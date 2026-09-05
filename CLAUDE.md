<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:
`specs/003-labeling-framework/plan.md`
<!-- SPECKIT END -->

## Project status & roadmap

The current release line is **v0.58.0**, shipped with scoped capabilities.
The active work is stabilization: **#319**, GUI acceptance **#331–334**,
and credential-gated integrations **#325/#328**. See
`docs/stabilization-plan.md` and `docs/release-checklist-mvp.md`.
The canonical, up-to-date roadmap is `ROADMAP.md`; per-version detail is in
`CHANGELOG.md`; sequencing rationale is in `docs/implementation-plan.md`.
Authoritative issue/milestone status lives in the GitHub tracker.

## Working conventions

- CI runs the FULL `uv run pyright` (including test files), `uv run ruff check`,
  and `ruff format --check` — local `uv run pytest` catches none of these. Run
  all three over the whole tree before every push.
- Run the live-daemon lifecycle and spectator tests in the default suite.
  Lifecycle tests use explicit empty upstream configuration and isolated sockets;
  never point tests at user integrations or the default daemon PID.
- A new module must be added to the per-module 100% coverage ratchet baseline
  (`scripts/coverage_ratchet.py` + `coverage-ratchet.json`).
- Merge style: `gh pr merge <n> --merge --delete-branch` after green CI.

## Local models

Model management is daemon/setup-owned. Text and image model assets are
inventoried, downloaded, and MLX-converted through `capdep-setup models`
(with provenance manifests written under the model asset cache); large
image-generation dependencies live in the uv-managed `.venv-images` runtime
isolation boundary. Built-in MLX roles (fast / tool-heavy / quality / coding)
are documented in `README.md`. A shared cross-tool model pool
(`AI_MODELS_DIR`, so downloads land in one place instead of the per-user
Hugging Face cache) is planned but **not yet implemented** — tracked by #339,
scheduled for v0.61. Until then, do not assume an `AI_MODELS_DIR` pool exists.
