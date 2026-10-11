<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:
`specs/003-labeling-framework/plan.md`
<!-- SPECKIT END -->

## Project status & roadmap

The current release line is **v0.58.0**, shipped with scoped capabilities.
Work is organised by GitHub milestones, in order: **01 Email — usable native
review on a reproducible build**, **02 Email — reliable daily use**,
**03 Email — everyday triage actions, reply-all and attachments**,
**04 Documents**, **05 Read-first**, plus the cross-cutting **Evidence**
milestone, the **Theme —** buckets (CapDepMac UX, model runtime) and
**v1.0 — Hardening and daily-driver acceptance**. Versions are assigned at
release, not in milestone names.
The consolidated review and rationale is `docs/daily-driver-plan-2026-10.md`.
The canonical, up-to-date roadmap is `ROADMAP.md`; per-version detail is in
`CHANGELOG.md`; sequencing rationale is in `docs/implementation-plan.md`.
Authoritative issue/milestone status lives in the GitHub tracker. Locally
implemented, integrated, native-accepted and daily-use-verified are distinct
states: never report a build or test state from a dirty working tree.

## Working conventions

- CI runs the FULL `uv run pyright` (including test files), `uv run ruff check`,
  and `ruff format --check` — local `uv run pytest` catches none of these. Run
  all three over the whole tree before every push.
- Verify the COMMITTED state, not your working tree: `scripts/verify-clean-checkout.sh`
  (ruff, format, pyright and the Swift build of `HEAD` in a throwaway worktree;
  `--full` adds pytest and the coverage ratchet). A dirty tree has hidden CI
  failures before (cross-file `private` Swift types, unformatted files, and tests
  that only passed because the developer's machine held real credentials).
- Tests must not depend on the developer's machine: mock credential, Podman and
  Metal checks; never read the real `HOME`, keychain or user integrations. To
  reproduce CI coverage, run the full suite with an empty `HOME`.
- Run the live-daemon lifecycle and spectator tests in the default suite.
  Lifecycle tests use explicit empty upstream configuration and isolated sockets;
  never point tests at user integrations or the default daemon PID.
- Tests and GUI automation never send real mail: use the synthetic harness
  (`*.invalid` addresses and the SMTP recorder, e.g. `scripts/test-email-mvp-gui.py`).
- The coverage ratchet (`scripts/coverage_ratchet.py` + `coverage-ratchet.json`)
  is per-surface line-coverage floors (some at 100%, others lower, with a small
  tolerance), not a blanket per-module 100%. A new module must be added to the
  baseline; floors only move up, so do not re-baseline downward to make CI pass.
- The uv-managed Python lives under `~/Library/Caches/capdep` (not `/tmp`, which
  macOS purges); `scripts/test-capdep.sh` fails fast with a repair hint if the
  venv is broken.
- Merge style: `gh pr merge <n> --merge --delete-branch` after green CI.

## Local models

Model management is daemon/setup-owned. Text and image model assets are
inventoried, downloaded, and MLX-converted through `capdep-setup models`
(with provenance manifests written under the model asset cache); large
image-generation dependencies live in the uv-managed `.venv-images` runtime
isolation boundary. Built-in MLX roles (fast / tool-heavy / quality / coding)
are documented in `README.md`. A shared cross-tool model pool
(`AI_MODELS_DIR`, so downloads land in one place instead of the per-user
Hugging Face cache) is planned but **not yet implemented** — tracked by #339
under the Theme — Model runtime milestone. Until then, do not assume an `AI_MODELS_DIR` pool exists.
