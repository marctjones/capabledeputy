# Implementation Plan: Time-Bounded Capabilities

**Branch**: `001-time-bounded-capabilities` | **Date**: 2026-05-15 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/001-time-bounded-capabilities/spec.md`

## Summary

A capability gains an optional absolute expiry deadline. The
deterministic policy decision point treats a matched-but-expired
capability as non-matching and, when expiry is the sole reason an
action is blocked, attributes the denial to expiry distinctly from
"no capability." A duration helper resolves "for N" to an absolute
deadline at grant time. The deadline is persisted so it survives a
runtime restart. Enforcement is entirely deterministic and isolated
from the language model — consistent with the established
architecture where `decide()` is a pure function run unconditionally
at dispatch.

## Technical Context

**Language/Version**: Python 3.12 / 3.13
**Primary Dependencies**: anyio, typer, rich, prompt-toolkit (CLI);
no new runtime dependency required (standard-library datetime is
sufficient for absolute UTC deadlines)
**Storage**: SQLite session store (`session.store`), currently
`SCHEMA_VERSION = 3`; this feature adds the expiry attribute to the
serialized capability JSON — no new column, so the bump is a
data-shape evolution handled by the existing `Capability.from_dict`
default-tolerant pattern (no migration SQL needed; older rows
deserialize with expiry = none)
**Testing**: pytest, pytest-asyncio, hypothesis
**Target Platform**: Linux daemon (single long-running process) + CLI
clients over the JSON-RPC Unix socket
**Project Type**: single project — library core + `capdep` CLI +
daemon
**Performance Goals**: expiry check is an O(1) timestamp comparison
inside the existing per-dispatch `decide()`; no measurable change to
decision latency
**Constraints**: enforcement must remain deterministic and
LLM-isolated (FR-004 / SC-006); absolute UTC deadline is the unit of
truth; half-open window (valid up to, not including, the deadline)
**Scale/Scope**: per-session capability sets are small (single to low
tens); no indexing or scan concern

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The project constitution at `.specify/memory/constitution.md` is the
**unpopulated speckit template** (all `[PRINCIPLE_*]` placeholders).
No principles are ratified, therefore there are **no constitution
gates to evaluate**. This is recorded as N/A, not as a vacuous pass
and not as fabricated violations.

The de-facto architectural invariant this codebase already enforces —
*policy enforcement is a deterministic pure function isolated from the
language model* — is carried into this feature explicitly (FR-004,
SC-006, and the post-design re-check below). Recommendation: run
`/speckit-constitution` to ratify that invariant (and the
test-first / observability norms already practiced) so future
features get a real gate. Non-blocking for this plan.

**Gate result**: N/A (no ratified constitution) — proceed.

## Project Structure

### Documentation (this feature)

```text
specs/001-time-bounded-capabilities/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── policy-decision.md
└── checklists/
    └── requirements.md  # from /speckit-specify
```

### Source Code (repository root)

```text
src/capabledeputy/
├── policy/
│   ├── capabilities.py   # Capability gains optional expiry deadline +
│   │                     #   matches()/serialization; TTL helper
│   └── engine.py         # decide(): expired match → treated as
│                         #   non-match; new reason rule
│                         #   "capability-expired"; decision clock
├── session/
│   └── store.py          # serialized capability JSON carries expiry
│                         #   (default-tolerant from_dict; no DDL)
└── cli/
    └── chat.py           # /grant accepts a duration; /status, /caps,
                          #   and the bottom toolbar annotate
                          #   time-bounded + remaining/expired

tests/
├── test_policy_capabilities.py   # expiry attribute + matches() +
│                                 #   serialization round-trip + TTL helper
├── test_policy_engine.py         # decision-time evaluation, half-open
│                                 #   boundary, expiry-vs-no-capability
│                                 #   attribution, composition with
│                                 #   one-shot/revocation
├── test_session_store.py         # deadline survives reload (restart)
└── test_time_bounded_e2e.py      # NEW: grant-with-TTL → use before →
                                  #   use after → deterministic deny;
                                  #   LLM-isolation invariant (SC-006)
```

**Structure Decision**: Single project, existing layout. This feature
is an additive attribute on an existing entity (`Capability`) plus a
guard inside the existing single decision chokepoint (`decide()`). No
new module, no new package, no new dependency. The decision clock is
injected (parameter defaulting to "now in UTC") so tests are
deterministic without monkeypatching wall-clock.

## Complexity Tracking

> No constitution gates exist, so there are no violations to justify.
> The design deliberately adds zero new abstractions: one optional
> field, one comparison in the existing pure function, one new reason
> string, one duration helper. Recorded here only to state that the
> simpler-alternative bar was applied and nothing heavier was chosen.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| (none)    | —          | —                                    |
