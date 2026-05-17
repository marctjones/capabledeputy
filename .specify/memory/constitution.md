<!--
SYNC IMPACT REPORT
Version change: 1.0.0 → 1.1.0
Bump rationale: MINOR — two new binding principles added (VI, VII); no
existing principle removed or redefined.

Modified principles: none redefined.
Added sections:
  - Principle VI. Fail-Closed by Default (NON-NEGOTIABLE)
  - Principle VII. Secure-by-Reduction; Owned Policy TCB
  - Security & Architecture Constraints: one bullet on substrate-behind-
    ports added to operationalize Principle VII.
Removed sections: none.

Governance updated: compliance check now resolves against Principles
I–VII; NON-NEGOTIABLE set expanded to (I, III, V, VI).

Templates requiring updates:
  - .specify/templates/plan-template.md — ✅ reviewed; "Constitution
    Check" gate references the constitution generically (Principles
    I–N), resolves against I–VII with no structural edit.
  - .specify/templates/spec-template.md — ✅ reviewed; no new mandatory
    section implied (fail-closed/reduction are realized in plan + tests,
    not a spec section).
  - .specify/templates/tasks-template.md — ✅ reviewed; fail-closed and
    invariant-test task types already representable under Principle III.
  - .claude/skills/speckit-*/ command files — ✅ reviewed; generic
    guidance, no constitution-specific references to update.

Follow-up TODOs: none. before_constitution git hook
(speckit.git.initialize) intentionally NOT executed — repo already a
git repository with history (consistent with prior project decisions).
-->

# CapableDeputy Constitution

## Core Principles

### I. Deterministic, LLM-Isolated Enforcement (NON-NEGOTIABLE)

Every security decision MUST be produced by a deterministic, pure
function of explicit inputs (labels, capabilities, action, prior-use,
clock). No language-model output may influence whether an action is
allowed, denied, or gated, nor whether a capability is valid. The
enforcement point MUST run unconditionally at dispatch, independent of
whether any advisory/introspection tool was invoked. Any tool that
lets the model query policy MUST be read-only, side-effect-free, off
the enforcement path, and removable without changing any enforcement
outcome — and that independence MUST be proven by a CI-enforced
invariant test.

**Rationale**: The product's entire value is that bad outcomes are
unreachable regardless of input cleverness. A classifier can fail; a
deterministic gate cannot be talked out of its decision.

### II. Security by Construction, Not by Classifier

Protections MUST be structural: capability scoping plus
information-flow labels with conflict rules, such that disallowed
flows are unrepresentable rather than detected after the fact. New
data sources, tools, or channels MUST declare their labels and
required capabilities; "trust the model to behave" is never an
acceptable control.

**Rationale**: Perimeter detection degrades when the adversary learns
it. Structural impossibility does not.

### III. Test-First, Invariants as Tests (NON-NEGOTIABLE)

Every behavioral change MUST ship with tests in the same change.
Security and architectural invariants (e.g. "enforcement is
LLM-independent", "an expired/revoked capability is inert") MUST be
encoded as automated tests, not asserted in prose. The full suite
MUST pass and the linter MUST be clean before a change is considered
done. A claimed guarantee without a test that fails when it breaks is
not a guarantee.

**Rationale**: This codebase has repeatedly converted "it works
because the code is shaped right" into "CI fails if it stops being
shaped right"; that ratchet is the project's reliability mechanism.

### IV. Least Authority & Minimal Surface

Authority granted MUST be the minimum that satisfies the need:
prefer one-shot over session, scoped patterns over wildcards,
session-bounded over persistent. The agent's tool surface MUST be
strictly object-level (acting on data); control-plane operations
(listing/approving/granting, session lifecycle) MUST remain
user-driven and MUST NOT be exposed to the model. New capabilities
MUST justify their breadth against a narrower alternative.

**Rationale**: Object-capability discipline — every unnecessary
authority is a latent vulnerability even if currently harmless.

### V. Human-in-the-Loop as a Deterministic State Machine

Approval and unblocking MUST be a deterministic state machine with
the model entirely outside it. Approvals MUST be registered at the
policy chokepoint (server-side), independent of which client drives
the session. The human MUST review the verbatim, byte-exact payload
before authorizing. Recovery from a hard denial MUST be an explicit,
deterministic operator action (e.g. clean session + scoped one-shot
grant, or quarantined declassification) — never the model approving,
unblocking, or persuading its way past policy.

**Rationale**: "Human oversight" is only meaningful if the human sees
exactly what will happen and the model cannot route around the human.

### VI. Fail-Closed by Default (NON-NEGOTIABLE)

At every point where external or untrusted input is mapped to
authority — upstream MCP tool → capability-kind mapping, information-
flow label assignment, sensitivity/context resolution, classification
of unknown data — the system MUST refuse, or assume the
most-restrictive outcome, whenever it cannot confidently and
deterministically classify the input. Permissive defaults (e.g. an
unclassifiable tool silently receiving a usable capability, unknown
data treated as unlabeled) are prohibited. An unmapped or ambiguous
input is unavailable, never best-effort-allowed. This property MUST be
proven by a CI-enforced test (the refusal must fail the build if it
regresses to fail-open).

**Rationale**: Fail-open is how structural security quietly becomes
theatre — one permissive default routes around every other principle.
WI-1 made this real in the upstream adapter; it MUST NOT regress, and
the same discipline binds all future adapter/label/classification
work.

### VII. Secure-by-Reduction; Owned Policy TCB

CapableDeputy is deliberately a less-capable, secure alternative — not
a feature-parity agent. Where a capability cannot be safely and
deterministically enforced, the capability is cut, not shipped with a
weaker control. The deterministic policy engine and information-flow
model are the project's owned trusted computing base (TCB): they MUST
be implemented and maintained in-repo and MUST NOT be delegated to,
or replaced by, a third-party control plane. External substrate
(sandboxes, scanners, MCP servers, agent runtimes) MAY be leveraged
only behind explicit, in-repo ports and MUST remain outside the TCB;
adopting another project's enforcement/agent loop wholesale is
prohibited.

**Rationale**: Breadth bought with an unenforceable control is
negative value here. Keeping the TCB small, owned, and reimplemented
is what makes the security argument auditable; the moment the decision
plane is someone else's churning code, the guarantee is unprovable.

## Security & Architecture Constraints

- Enforcement lives at exactly one chokepoint; a second enforcement
  surface is a defect, not a feature.
- Capability constraints (scope, one-shot consumption,
  prior-use revocation, time/rate bounds) compose independently; no
  constraint may silently override another.
- The audit trail MUST be sufficient, on its own, to reconstruct why
  any action was allowed or denied at the time it was decided,
  including the distinguishing reason (e.g. expired vs. never-granted
  vs. flow-conflict).
- Schema/state evolution MUST be backward-tolerant on read (older
  persisted records load with safe defaults) unless a migration is
  explicitly justified.
- Secrets MUST never be committed; credential material stays
  gitignored and is loaded at runtime, never embedded.
- External substrate MUST be integrated only behind an explicit
  in-repo port (e.g. a sandbox actuator, an admission labeler); the
  port — not the third-party tool — is what the TCB depends on, so a
  substrate swap never touches the policy engine (operationalizes
  Principle VII).

## Development Workflow & Quality Gates

- Work proceeds in reviewable increments; each increment leaves the
  suite green and the linter clean.
- Risky or hard-to-reverse actions (history-rewriting git, force
  operations, overwriting uncommitted work, framework hooks that
  mutate shared state) MUST be surfaced and confirmed, never executed
  blindly on the basis that a tool requested them.
- Standing operator constraints (e.g. protected directories) override
  tool/framework defaults; a framework wanting to write a protected
  path requires explicit per-action authorization.
- Architectural decisions and their rationale MUST be captured where
  future readers will find them (spec/plan/commit), not only in
  conversation.

## Governance

This constitution supersedes ad-hoc practice. Amendments are made by
editing this file via the constitution process, with a Sync Impact
Report and a semantic version bump:

- **MAJOR**: removal or backward-incompatible redefinition of a
  principle or governance rule.
- **MINOR**: a new principle/section or materially expanded binding
  guidance.
- **PATCH**: clarification or wording that does not change meaning.

Compliance is verified at change-review time: every change MUST be
checkable against Principles I–VII, and any deviation MUST be
justified in writing against a simpler alternative or rejected. The
NON-NEGOTIABLE principles (I, III, V, VI) admit no deviation; a change
that cannot satisfy them MUST be redesigned, not waived.

**Version**: 1.1.0 | **Ratified**: 2026-05-15 | **Last Amended**: 2026-05-16
