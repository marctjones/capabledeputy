# CapableDeputy

A structurally secure runtime for personal AI agents.

> *A capable deputy, never a confused one.*

CapableDeputy is an AI agent runtime built as a faithful implementation of recognized security models — a reference monitor, an information-flow lattice, and the object-capability model — with the LLM treated as an untrusted component *outside* the trusted computing base. Every action the agent takes flows through one deterministic capability and information-flow chokepoint, escalates to programmatic execution when stakes warrant, and surfaces every cross-compartment data flow through human-auditable approval gates.

**Status:** Alpha — **v0.58.0** — real assistant capabilities and a safe default surface: web.fetch (real SSRF-guarded HTTP), email SEND (real SMTP), a persistent tasks store, an image-safety default, and a zero-config safe assistant surface — all real, policy-gated, and verified in-repo. Shipped scoped: integrations whose acceptance needs live credentials (real Google calendar/inbox reads, first-class Google Workspace + GitHub account connect) are deferred to v0.59 rather than faked, and Microsoft 365 / Notion are marked not-supported-for-v1.0. This sits on v0.54–v0.57: the egress-complete chokepoint, reachable safe-handling flow patterns for restricted data, named security-posture profiles with a floor-invariance conformance harness plus the unified policy-authoring stack (`capdep policy check`/`why`, mutation CLI), and daemon reliability/supervision (supervised auto-restart, mid-session reconnect, timeouts/circuit-breaking, non-destructive state-DB lifecycle, `capdep doctor`, live telemetry). Earlier releases delivered natural web search, daily-driver workflow validation and policy defaults, dead-simple Google account connection, consolidated setup automation, bounded native office automation skills, SKILL.md interoperability, CommonMark rendering parity, daemon-owned local media/model operations, and CapDepMac prompt queueing/recovery. See [CHANGELOG.md](CHANGELOG.md) for the per-version detail and [ROADMAP.md](ROADMAP.md) for longer-term planning.

## Why

Most defenses for LLM agents are perimeter classifiers — they try to *detect* a bad instruction ("does this look like an attack?"). A classifier can always be fooled. CapableDeputy takes the opposite approach: enforce recognized information-security models at the architecture level so the bad *outcome* has no reachable path — independent of *why* the model misbehaves, whether a crafted injection, a hallucination, a buggy or malicious tool / MCP server, or the user's own mistake. The classifier can fail; the capability check cannot.

Because the guarantees come from the models rather than from per-attack rules, a range of risks is structurally mitigated as a *consequence* of the design, not as individually-coded features. Illustratively (non-exhaustive):

- **Silent data exfiltration** — information-flow taint blocks egress of data a session has read (Denning lattice).
- **Confused-deputy / ambient-authority abuse** — no authority without an explicit, scoped capability (object-capability).
- **Over-broad or escalating delegated authority** — monotonic attenuation across delegation chains.
- **Unauthorized irreversible or committing actions** — human-in-the-loop approval (Clark-Wilson separation of duty).
- **Conflict-of-interest data mixing** — cross-compartment access rules (Brewer-Nash).
- **Unaccountable automated decisions** — deterministic decisions over an append-only provenance record.
- **Purpose limitation / purpose-contamination boundary** — inadmissible data categories are refused before entering a purpose-scoped workflow; the narrower question of whether admissible data inappropriately influenced model cognition is explicitly out of scope.
- **Prompt-injection-driven misuse** — mitigated as one special case of the above: the model is treated as untrusted regardless of how it was subverted.

This is an illustrative consequence of the enforced models, not a feature checklist or a completeness claim.

The design draws on classical information security models — Bell-LaPadula, Biba, Brewer-Nash, Clark-Wilson, and the object-capability model — synthesized with the dual-LLM and programmatic-execution patterns from CaMeL (Google DeepMind, 2025) and Dromedary (Microsoft). These are tracked frameworks, not loose inspiration: every enforcement mechanism traces to a named model with any deliberate deviation recorded ([docs/security-models.md](docs/security-models.md)), and every way the planner LLM touches labeled data is one of a small set of named flow patterns ([docs/llm-flow-patterns.md](docs/llm-flow-patterns.md)). Those docs are the design yardstick; this README intentionally does not restate the theory.

## Scope: a control at the intersection, not a governance program

CapableDeputy is deliberately narrow. It is a **runtime control at the intersection of InfoSec, Data & Privacy, and AI governance** — it defends the conjunction those three programs structurally cannot — sensitive data, untrusted input, and capable action converging in one agent (the "lethal trifecta," in governance terms) — and intentionally does *not* attempt their breadth. Within AI governance it is deep and faithful on agentic-effect containment, human oversight, and decision accountability, and silent by design on model accuracy, bias/fairness, eval, and content safety. Every in-scope guarantee is bounded by three contingencies: correct labeling, a trustworthy substrate, and the fact that purpose-scoping is enforced as observable read-admissibility rather than model-interpretability. See **[docs/governance-scope.md](docs/governance-scope.md)** for the precise in/out-of-scope statement and its alignment with this vision.

Why all three at once: a running agent collapses what were three mostly *design-time* governance disciplines into a single *runtime* problem — the right decision depends on the live context (purpose, recipient, sensitivity, reversibility) of each action. CapableDeputy resolves that context per action at one deterministic, LLM-isolated chokepoint: the *protection strength* adapts to context, the *mechanism deciding it* does not. The decision-layer rationale and its grounding in adaptive-governance / Contextual Integrity theory is in **[docs/trust-model.md](docs/trust-model.md)** (§9).

## How it works — and its honest limits

**The mechanism, in one paragraph.** Every action the agent proposes is
stopped at one deterministic chokepoint *outside* the LLM. There the engine
reads four orthogonal axes — the data's **category × sensitivity tier**, its
**provenance** (principal-direct ▸ system-internal ▸ external-untrusted), the
**effect** the action would have (read / reversible-write / irreversible /
egress …), and the **decision context** (recipient, purpose, time) — and
returns one of *allow · require-approval · deny*. Confidentiality (Bell-LaPadula),
integrity (Biba), conflict-of-interest (Brewer-Nash), and confused-deputy
defense (object-capability) are **always-on engine invariants**, not
per-attack rules: an action with no policy-allowed path is denied regardless
of *why* the model proposed it. The default is **fail-closed**. When stakes or
ambiguity rise, the runtime escalates from turn-level inheritance to a
quarantined dual-LLM split or to programmatic execution, and surfaces every
cross-compartment flow to a human approval gate.

**What this does *not* claim.** The guarantees are about **control of effects,
not correctness of content** — CapableDeputy will not tell you whether the
agent's answer is *right*, *unbiased*, or *well-reasoned*; that is out of scope
by design. Several of the classical models are deliberately implemented in
**scoped/approximate form** (read-up-only BLP, one-direction Biba,
*session-scoped* Brewer-Nash, *intransitive* noninterference) because the
faithful forms are either undecidable or incompatible with a useful agent —
these are documented as deviations, not hidden. And three limits are
**inherent**: transitive noninterference and the general safety question of an
access-control system are not decidable; model interpretability is not
pursued; and containment is **not** declassification — contained data stays
contained, it is never silently *cleansed*.

**The gaps we actively watch** (so the design doesn't make them worse, and
ideally makes them better):

- **Decision fatigue** — coarse policy pushes real workflows toward
  "always-approve," and rubber-stamped approvals are the practical way human
  oversight erodes. The fix is *more expressive policy* (the Starlark
  decision-inspector layer), so the human is asked less often and more
  meaningfully — not more often.
- **The labeling oracle** — every information-flow guarantee rides on data
  being labeled correctly; mislabeled data means the defense is *silently
  absent*. Broadening label coverage matters more for real safety than any
  new model.
- **A default-inert refinement layer** — the `DecisionInspector` chokepoint
  and sandboxed Starlark policy host are wired through daemon config, but no
  inspectors are enabled by default. Shipping conservative starter inspectors
  is now higher leverage than adding more approval prompts.
- **Model-reasoning purpose contamination** — CapDep now blocks
  inadmissible categories from entering purpose-scoped workflows, but it
  deliberately does not claim to prove how admissible data influenced the
  model's private reasoning.

The full, grounded model-by-model / pattern-by-pattern / principle-by-principle
read — strengths, weaknesses, and prioritized fixes — is in
**[docs/security-alignment-assessment.md](docs/security-alignment-assessment.md)**.

## Documentation

- [docs/governance-scope.md](docs/governance-scope.md) — **what CapableDeputy is expected to do (and not)** — scope, AI-gov coverage, contingencies
- [docs/architecture.md](docs/architecture.md) — **daemon-first architecture** — core/safety functionality lives in the daemon; CLI, TUI, macOS GUI, and future Windows/Linux GUIs are thin clients over the same contract
- [docs/security-models.md](docs/security-models.md) — theoretical model lineage, faithful-vs-approximate intent, deliberate deviations (the yardstick)
- [docs/llm-flow-patterns.md](docs/llm-flow-patterns.md) — the named planner↔labeled-data flow patterns and their selector
- [docs/trust-model.md](docs/trust-model.md) — decision layer: who authorizes, and the adaptive-context / Contextual Integrity grounding
- [docs/responsible-ai-frameworks.md](docs/responsible-ai-frameworks.md) — **the actionable core of responsible AI: keeping the human in control of the agent's actions, not policing model correctness** — the eight enforced principles, the human in/on/over-the-loop ladder, and what is deliberately out of scope
- [docs/security-alignment-assessment.md](docs/security-alignment-assessment.md) — **grounded alignment scorecard** — how the code actually lines up with each security model, flow pattern, and AI-safety principle, with strengths, live gaps, and prioritized fixes
- [docs/gui-greenfield-design.md](docs/gui-greenfield-design.md) — **greenfield GUI product design** — primary users, desktop posture, workflows, automation model, screen-space rules, and integrated menu system
- [docs/macos-desktop-ux-strategy.md](docs/macos-desktop-ux-strategy.md) — **macOS desktop UX strategy** — native menu-bar/command-palette/approval/dashboard design guidance for CapDep's supervised desktop assistant shell
- [docs/safe-scripting-assistant-v040.md](docs/safe-scripting-assistant-v040.md) — **safe practical scripting assistant** — daemon-owned script planning, sandboxed execution, evidence, and exact export approvals for non-programmer automation tasks
- [docs/skills-interoperability.md](docs/skills-interoperability.md) — **SKILL.md interoperability** — Codex/Claude-style folder packages, explicit guidance/tool/hybrid modes, and sandbox-only script execution
- [docs/image-generation-adult-model-notes.md](docs/image-generation-adult-model-notes.md) — local image-generation backend/model capability notes and deferred model-download findings
- [docs/commonmark-client-capabilities.md](docs/commonmark-client-capabilities.md) — CommonMark rendering and fallback matrix for CapDepMac, CLI/TUI, MCP-control, and plain/log clients

- [docs/SURFACES.md](docs/SURFACES.md) — **which command do I use?** (chat vs console vs tui vs demo vs …) — start here
- [DESIGN.md](DESIGN.md) — full design specification
- [ROADMAP.md](ROADMAP.md) — phased implementation plan
- [docs/workflow-index.md](docs/workflow-index.md) — **categorized index of every workflow** — 26 narrated demos, the 1126-scenario allow/deny catalogue, and the enforcement suites, grouped by use case + security mechanism
- [docs/workflow-plan.md](docs/workflow-plan.md) — the **executable assurance plan**: a coverage matrix (mechanisms × pressured?), a per-workflow scorecard, and two gates — the spec we execute against
- [docs/workflow-registry.md](docs/workflow-registry.md) — the same workflows with **status + results** (implemented? tested? regression-guard vs. finding), prior gap closure, standing boundaries, and a findings log
- [docs/testing.md](docs/testing.md) — deterministic, live-daemon, macOS GUI-sensitive, external MCP smoke, and coverage-ratchet test tiers
- [docs/demos/README.md](docs/demos/README.md) — 21 historical walkthrough demos
- [demos/scenarios/README.md](demos/scenarios/README.md) — **26 narrated executable demos** (runnable via pytest)
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup and contribution guide

## Development

CapDep standardizes on **uv** for Python packaging and one repo-local
development environment: `.venv`. Do not install project dependencies with
`pip`, Poetry, or ad hoc virtualenvs. Bootstrap once, then use `uv run ...`
or the checked-in launch scripts, which put `.venv/bin` first on `PATH`.
The optional `.venv-images` environment is a uv-managed runtime isolation
boundary for large image-generation dependencies, not a second developer
environment.

```bash
scripts/bootstrap-dev-env.sh
uv run pytest
```

### See it in action

The fastest way to understand what the policy engine actually does:

```bash
# Run all 26 narrated executable demos in order, with operator-facing prose:
uv run pytest demos/scenarios/run_all.py --no-cov -s

# Or a single demo (the marquee one):
uv run pytest demos/scenarios/daily_briefing.py --no-cov -s
```

See [demos/scenarios/README.md](demos/scenarios/README.md) for the
demo lineup and what each one proves.

### One-time setup automation

Use `capdep-setup` for machine-local and account-preparation work that should
not live in the daemon or client surfaces:

```bash
capdep-setup list
capdep-setup assistant-surface --apply
capdep-setup daily-driver --self you@example.com --trusted-draft assistant@example.com
capdep-setup google-cloud --project PROJECT_ID --services gmail,drive,calendar
capdep-setup google-workspace --services gmail,drive,calendar
capdep-setup images
capdep-setup models
capdep-setup models --apply --download
capdep-setup models --apply --convert
capdep-setup sandbox
capdep-setup office-automation
capdep-setup macos-daemon
capdep-setup macos-daemon --apply --verify
```

The consolidated setup commands are dry-run/check by default. Mutating actions
require `--apply`, including writing managed daemon config, creating image
venvs, installing packages, downloading model assets, checking sandbox runtime
health, writing local model conversion manifests, checking native Office app
availability, or verifying daemon launch/parity state. The daemon remains
responsible for live readiness, OAuth/token state, policy, approvals, audit,
runtime status, and user workflows. Standard setup tests use temp homes, fake
caches, and fake subprocess runners so they do not mutate the developer's real
`~/.config/capabledeputy`, `.venv-images`, Hugging Face cache, launchd state,
daemon sockets, keychain, or model cache.

`capdep-setup daily-driver` is the v0.51 policy-readiness entry point. It
checks the curated daily-driver tool catalog, reports available, degraded,
missing, and intentionally disabled tools, and can generate exact self/trusted
relationship groups plus draft approval patterns. It does not enable direct
sends, generic browser scripting, generic desktop automation, or broad shell
authority. Its JSON details include the daily-driver workflow validation report
described in [docs/daily-driver-validation.md](docs/daily-driver-validation.md).

Google account setup is preset-first in CapDepMac: users choose Gmail,
Gmail + Calendar, or Gmail + Calendar + Drive, while the daemon keeps the
underlying MCP services separately scoped, audited, and reloadable. Advanced
bring-your-own Google OAuth client setup remains available; OAuth sign-in only
proves account access and never grants CapDep action authority by itself.

Native desktop Office automation is bounded by app-specific MCP servers and
SKILL.md guidance packages. CapDep includes Apple Mail, Pages, Numbers,
Keynote, Microsoft Outlook, Word, and PowerPoint surfaces for read, draft,
edit, export, and presentation workflows. It does not expose arbitrary
AppleScript, Office macros, VBA, shell execution, or unrestricted UI scripting
as user-facing Office capabilities.

### macOS desktop chat (CapDepMac)

The native Swift GUI (`apps/macos/CapDep`) is the primary conversational
surface. It drives daemon-managed turns over the same policy gate as the CLI
and TUI.

```bash
# From the repo root — builds, restarts the daemon, and opens CapDepMac:
apps/macos/CapDep/scripts/run-local-app.sh

# Configure Kagi web search (API key outside git):
#   ~/.config/capabledeputy/secrets/vault.yaml  →  kagi / KAGI_API_KEY
#   ~/.config/capabledeputy/servers.d/kagi.yaml →  kagi_search_fetch tool
# Kagi's `uvx kagimcp` command resolves through .venv/bin/uvx at daemon start.
```

New CapDepMac sessions receive foreground read caps for common home-directory
paths (`~/Documents`, `~/Projects`, etc.). If a file tool is denied for a
specific path, the chat shows a **Grant access** banner — that is a capability
grant (`/grant`), not the approval queue used for outbound or destructive
actions.

CapDepMac now treats chat as an asynchronous work queue: prompts can be entered
while earlier turns are still pending, daemon events are correlated back to the
right turn, scrollback stays available across recovered sessions, and generated
image/session artifacts are persisted so they remain visible to later turns in
the same session.

### Local image/model operations

The v0.42 image workflow is daemon-owned. Profiles, readiness checks, job
status, cancellation, and persisted defaults are exposed through daemon RPCs and
the `capdep image` CLI; CapDepMac renders the same selected profile and
readiness state in Settings > Assistant.

```bash
# Inspect benchmark-informed local profiles:
capdep image profiles

# Select the persisted daemon default:
capdep image profile balanced

# Check backend/model/account readiness without printing secret values:
capdep image readiness --profile default

# Start a daemon-owned image job and inspect/cancel it later:
capdep image generate "a clean product photo of a brass desk lamp"
capdep image jobs
capdep image job <job-id>
capdep image cancel <job-id>
```

Default, fast, and balanced profiles use the Apple Silicon MFLUX/MLX
Z-Image-Turbo path. The quality profile uses the slower Flux2 Klein path, with
`quality-flux2` and `quality-qwen` exposed as benchmark challengers before any
default promotion. Diffusers SDXL/Pony profiles remain explicit fallback
profiles for local checkpoint installs. Readiness checks cover backend imports,
output paths, LoRA/checkpoint paths, Hugging Face token presence, and selected
model metadata; they report token source names only, not token values.

`capdep-setup models` now produces a conversion-aware model asset inventory
covering text and image profiles, source formats, Hugging Face repositories,
gate/fallback status, and native MLX/MFLUX feasibility. `--download` fetches
recommended source/native assets; `--convert` runs supported MLX conversion
commands and writes provenance manifests under the model asset cache. Unsupported
SDXL/Pony safetensors fallbacks stay explicit source-runtime fallbacks and are
not silently promoted to defaults. Use `scripts/benchmark_image_models.py` for
local benchmark evidence before changing profile defaults.

Built-in MLX text roles keep `Qwen/Qwen3-4B-MLX-4bit` as the fast default,
use `mlx-community/Qwen3-14B-4bit` for tool-heavy turns,
`mlx-community/Qwen3-30B-A3B-4bit` for quality turns, and
`mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit` for programmatic
coding/scripting turns. `mlx-community/Qwen3.6-27B-OptiQ-4bit` is tracked as
the first quality-planner challenger, but remains candidate-only until local
CapDep benchmarks beat the current quality role. Qwen3.6 VLM assets stay
experimental until CapDep has an explicit `mlx-vlm` backend path.

### Safe scripting assistant

The v0.40/v0.41 scripting workflow is daemon-owned. Clients can ask for a plan,
prepare a script artifact, run it in the sandbox, inspect captured evidence,
and approve an exact export without gaining extra authority themselves.

```bash
# Ask the daemon for a practical scripting plan:
capdep scripting plan "rename the photos in this folder by date" --workspace-root ~/Pictures

# Prepare, run, and export reviewed artifacts through the same daemon RPCs:
capdep scripting prepare-script ./script.py --workspace-root ~/Pictures --target-path safe-scripting/script.py
capdep scripting run-artifact ./run-result.json --workspace-root ~/Pictures
capdep scripting export-artifact ./output.txt --workspace-root ~/Pictures --target-path exports/output.txt
```

## License

Proprietary — Copyright (c) 2026 Marc Jones. All rights reserved. No license is granted; see [LICENSE](LICENSE).
