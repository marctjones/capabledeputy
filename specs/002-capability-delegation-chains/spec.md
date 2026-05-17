# Feature Specification: Capability Delegation Chains

**Feature Branch**: `main` (continues established v0.4–v0.7 workflow; no feature branch)  
**Created**: 2026-05-16  
**Status**: Draft  
**Input**: User description: "Capability delegation chains. A session can delegate an attenuated subset of its own capabilities to a child session it spawns. The deterministic policy engine MUST enforce monotonic attenuation … each hop must attenuate or preserve, never widen … revoking/expiring/rate-exhausting a capability cascades to every capability delegated from it across the live session graph … enforcement stays LLM-isolated … audit every delegation and every cascade revocation."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Delegate an attenuated capability to a child session (Priority: P1)

When the deputy spawns a child session to do a narrower piece of work, it can hand that child a *subset* of its own authority — never more. The deputy requests a delegated capability (optionally narrower in target, amount, lifetime, or rate); the policy engine derives the actual delegated capability by clamping every dimension to the parent and **rejects outright** any request that would broaden scope along any dimension. The child session then operates strictly within the attenuated grant.

**Why this priority**: This is the irreducible core of the feature and a self-contained MVP. Without monotonic attenuation enforced at delegation time, every other part of the feature is meaningless. It is independently demonstrable: spawn a child with a delegation request and observe the derived capability and the rejection of any broadening attempt.

**Independent Test**: Spawn a child session from a parent holding a capability, request a delegation that (a) equals the parent, (b) is narrower on each dimension, and (c) attempts to broaden each dimension in turn. Verify the engine grants exactly the clamped-to-parent capability for (a)/(b) and refuses (c) with a deterministic reason, all without any model involvement.

**Acceptance Scenarios**:

1. **Given** a parent session holding a capability of kind K with target pattern `mail/*`, max amount 100, expiry T, and a rate limit of 5/hour, **When** the deputy spawns a child requesting a delegated capability of kind K with pattern `mail/team/*`, max amount 40, expiry T−1h, rate 2/hour, **Then** the child session receives exactly that requested capability (it is provably within the parent on every dimension) and the delegation is recorded with its parent provenance.
2. **Given** the same parent capability, **When** the deputy requests a delegated capability with max amount 250 (broader than the parent's 100), **Then** the engine refuses the delegation with a deterministic reason naming the violated dimension, and the child session is created with no such capability.
3. **Given** the same parent capability, **When** the deputy requests a delegated capability whose target pattern (`mail/**`) is broader than the parent's (`mail/*`), **Then** the engine refuses the delegation, because the requested pattern is not provably a subset of the parent's.
4. **Given** a parent capability that is **not** marked destructive, **When** the deputy requests a delegated capability marked destructive, **Then** the engine refuses the delegation.
5. **Given** a parent session that does **not** hold any capability of kind K, **When** the deputy requests a delegated capability of kind K, **Then** the engine refuses the delegation (you cannot delegate authority you do not possess).
6. **Given** any delegation request, **When** the model proposes the delegation parameters, **Then** the model never authors the resulting grant directly and never approves its own request — the engine alone derives and validates the capability, and a model-supplied "pre-approved" or widened capability is ignored.

---

### User Story 2 - Cascade revocation across the live session graph (Priority: P2)

When a capability that has been delegated is revoked, expires, or exhausts its rate budget, every capability derived from it — directly or transitively, anywhere in the currently live session graph, including capabilities attached to queued-but-not-yet-decided approvals — becomes unusable at the same instant, deterministically. A child can never outlive the authority it was granted from.

**Why this priority**: Delegation without cascade revocation would let a child retain authority after the parent's was withdrawn — a containment hole. It builds directly on US1 and is independently testable once delegation exists, but it is not required to demonstrate the core attenuation guarantee, so it is P2.

**Independent Test**: Build a parent→child(→grandchild) delegation chain, then revoke (and separately: expire, and separately: rate-exhaust) the parent capability. Verify that every descendant capability is simultaneously rendered unusable for all subsequent policy decisions in every live session, that any pending approval whose authorizing capability was a descendant is invalidated, and that each cascade is audited.

**Acceptance Scenarios**:

1. **Given** a parent capability delegated to a child and on to a grandchild, **When** the parent capability is revoked, **Then** the next policy decision in the child session and in the grandchild session that relies on the descendant capability is denied, with a reason attributing the denial to the cascaded revocation of the ancestor.
2. **Given** the same chain with a pending approval in the grandchild session whose authorizing capability is the delegated descendant, **When** the parent capability is revoked, **Then** that pending approval is invalidated (it can no longer be approved into an allowed action) and the invalidation is audited.
3. **Given** a parent capability with an expiry time, **When** that expiry time passes, **Then** all descendant capabilities are treated as expired from that instant for every live session, with no separate per-descendant expiry needed.
4. **Given** a parent capability with a rate limit, **When** the parent's rate budget is exhausted, **Then** descendant capabilities derived from it are also treated as rate-exhausted for the same window (a child cannot be used to circumvent the parent's rate ceiling).
5. **Given** any cascade event, **When** it fires, **Then** an audit record is written naming the originating capability, the trigger (revoke/expire/rate-exhaust), and the set of descendant capabilities and sessions affected.

---

### User Story 3 - Bounded delegation depth (Priority: P3)

Delegation chains cannot grow without limit. There is a configurable maximum chain depth; an attempt to delegate beyond that depth is refused deterministically, independent of whether the requested capability would otherwise be a valid attenuation.

**Why this priority**: A safety bound that prevents pathological or runaway delegation trees and bounds the cost of cascade traversal. It is valuable but the system is already correct and containment-safe with US1+US2; depth bounding is a hardening refinement, hence P3.

**Independent Test**: With the maximum depth configured to N, build a chain of N valid delegations and confirm each succeeds, then attempt the (N+1)th and confirm it is refused with a deterministic depth-limit reason — with no relationship to the capability's other dimensions.

**Acceptance Scenarios**:

1. **Given** a configured maximum delegation depth of N, **When** a chain of exactly N attenuating delegations is created, **Then** every hop succeeds.
2. **Given** a chain already at depth N, **When** a further delegation from the deepest child is requested, **Then** it is refused with a reason that names the depth limit, even though the requested capability is a valid attenuation of its parent.
3. **Given** the maximum depth is reconfigured, **When** subsequent delegations are evaluated, **Then** the new limit governs new delegations; already-granted deeper chains remain valid until independently revoked or expired (the limit gates creation, not retroactive teardown).

---

### Edge Cases

- **Diamond / multi-parent provenance**: If a session ends up with two capabilities of the same kind from different parents, each delegated capability tracks its *own* single parent provenance; revoking one ancestor cascades only down that ancestor's subtree, not across unrelated capabilities of the same kind.
- **In-flight tool call vs. cascade**: A tool call that has already passed the deterministic policy chokepoint and is mid-execution when an ancestor is revoked is **not** retroactively interrupted; the cascade governs all *subsequent* policy decisions and invalidates *pending* (not-yet-decided) approvals. This matches existing revocation/expiry semantics in the runtime.
- **Re-delegation of a still-valid sibling**: Revoking one delegated capability does not affect a sibling delegated separately from the same parent; only the revoked capability's own descendants cascade.
- **Self-delegation / cycles**: A session cannot delegate to itself, and the parent→child spawn relationship is acyclic by construction; a delegation request that would form a cycle is refused.
- **Delegating an already-cascaded-dead capability**: Requesting a delegation whose parent capability is itself already revoked/expired/rate-exhausted is refused (you cannot delegate from dead authority).
- **Pattern subset undecidability**: When it cannot be *proven* that the requested target pattern is a subset of the parent's, the request is refused (fail-closed), rather than attempting a permissive match.
- **Parent loses the capability after delegating**: If the parent's capability is removed for any reason after a delegation, the descendant is cascaded dead (US2) — the descendant never has standing the parent lacks.

## Clarifications

### Session 2026-05-17

- Q: Does a delegated capability's use count against the parent/ancestor rate budget, or only its own? → A: Pooled — a use counts against the child's own and every ancestor capability's rate window; any ancestor window reaching its limit disqualifies the subtree (makes US2-4 true by construction).
- Q: What does a delegated capability get for the non-enumerated fields (`revoked_by`, `expiry` lifetime enum, `origin`)? → A: Inherit-restrictive — `revoked_by` ⊇ parent's (request may add, never remove); `expiry` lifetime clamped on one_shot<session<persistent, default one_shot; `origin` set by engine to a distinct DELEGATED marker. No non-enumerated field may yield a less-restrictive child.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow a session to delegate, to a child session it spawns, a capability derived from a capability the parent session currently holds.
- **FR-002**: The deterministic policy engine MUST derive each delegated capability by clamping every constrained dimension to the parent's: same capability kind; target pattern equal to or a provable subset of the parent's; maximum amount ≤ the parent's; expiry time ≤ the parent's; rate limit no looser (no higher count, no shorter window) than the parent's; destructive only if the parent is destructive.
- **FR-003**: The engine MUST refuse, with a deterministic machine-readable reason naming the violated dimension, any delegation request that would broaden any dimension relative to the parent, or that requests a kind the parent does not hold.
- **FR-004**: When the target pattern subset relationship cannot be proven, the engine MUST refuse the delegation (fail-closed); it MUST NOT grant a delegation on an unprovable-narrower pattern.
- **FR-005**: Delegation MUST compose into chains (parent → child → grandchild → …); each hop MUST independently satisfy FR-002 against its immediate parent, so attenuation is monotonic along the entire chain.
- **FR-006**: The system MUST enforce a configurable maximum delegation chain depth and refuse any delegation that would exceed it, with a deterministic depth-limit reason, independent of the requested capability's other dimensions.
- **FR-007**: Revoking, expiring, or rate-exhausting a capability MUST cascade — at the same logical instant — to every capability transitively derived from it, for all subsequent policy decisions in every live session.
- **FR-008**: A cascade MUST invalidate any pending (not-yet-decided) approval whose authorizing capability is a cascaded descendant, such that the approval can no longer be approved into an allowed action.
- **FR-009**: A cascade MUST NOT retroactively interrupt or reverse a tool invocation that already passed the policy chokepoint before the cascade fired.
- **FR-010**: Each delegation MUST record its provenance: the specific parent capability it derives from and the parent/child session relationship.
- **FR-011**: The system MUST write an audit record for every delegation (granted or refused, with the reason) and for every cascade event (naming the originating capability, the trigger, and the affected descendant capabilities and sessions).
- **FR-012**: The model/LLM MUST be able to *request* a delegation as part of spawning a child session, but MUST NOT be able to author the resulting grant, widen it, bypass attenuation, or approve its own delegation request; the engine alone derives and validates the capability, and any model-supplied capability content that would broaden scope MUST be ignored.
- **FR-013**: A session MUST NOT delegate authority it does not itself currently hold, and MUST NOT delegate from a capability that is already revoked, expired, or rate-exhausted.
- **FR-014**: Delegation MUST be acyclic: a session cannot delegate to itself, and a delegation request that would create a cycle in the session/delegation graph MUST be refused.
- **FR-015**: A use of a delegated capability MUST be recorded against its own **and every ancestor** capability's rate window (pooled accounting up the provenance chain). A capability is rate-disqualified if its own or any ancestor's window has reached its limit — so a child can never be used to spend beyond an ancestor's rate ceiling (operationalizes US2-4, by construction).
- **FR-016**: A delegated capability MUST inherit its parent's restrictive non-enumerated fields monotonically: `revoked_by` MUST be a superset of the parent's (a request MAY add prior-use kill conditions, MUST NOT remove any); the `expiry` lifetime MUST be clamped on the ordering `one_shot < session < persistent` and default to `one_shot` (never longer-lived than the parent); `origin` MUST be set by the engine to a distinct `DELEGATED` value for audit. No non-enumerated field may yield a less-restrictive child than its parent.

### Key Entities

- **Capability**: A scoped grant of authority along the dimensions kind, target pattern, maximum amount, expiry, rate limit, and destructive flag. (Existing entity; extended here with delegation provenance.)
- **Delegated Capability**: A capability whose authority is derived from exactly one parent capability via monotonic attenuation; carries a reference to its parent capability and the depth of its position in the chain.
- **Delegation Provenance / Edge**: The recorded link from a delegated capability to its single parent capability and the parent→child session relationship; the basis for cascade traversal and audit.
- **Session Graph**: The live tree of parent/child sessions; the scope across which cascades and depth limits are evaluated.
- **Approval (pending)**: A queued, not-yet-decided request whose authorizing capability may be a delegated descendant; subject to cascade invalidation.
- **Audit Record**: The append-only security record; gains delegation-granted, delegation-refused, and cascade-revocation entry types.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of delegation requests that broaden *any* dimension relative to the parent are refused; zero broadening delegations are ever granted (verified by exhaustive per-dimension test coverage).
- **SC-002**: For any revoked/expired/rate-exhausted capability, 100% of its transitive descendants across all live sessions are denied on their next relevant policy decision, with no descendant remaining usable.
- **SC-003**: 100% of pending approvals whose authorizing capability is a cascaded descendant are invalidated by the cascade.
- **SC-004**: No delegation chain longer than the configured maximum depth can be created (0 successful over-depth delegations across testing).
- **SC-005**: Every delegation (granted or refused) and every cascade event produces exactly one corresponding audit record with the originating capability, trigger, and affected scope.
- **SC-006**: No model-authored or model-widened capability is ever honored; in all adversarial-prompt tests the engine-derived attenuated capability is the only one in effect.
- **SC-007**: Delegation and cascade decisions are deterministic — identical inputs produce identical decisions and identical audit content on repeated runs.

## Assumptions

- **Workflow**: This feature continues the established v0.4–v0.7 practice of developing on `main` (user confirmed; the speckit feature-branch hook was intentionally skipped).
- **Default maximum delegation depth**: A conservative default (e.g., 3 hops) is assumed when not explicitly configured; the exact default is an implementation/plan decision and is not scope-critical. The depth limit is configurable.
- **Pattern subset check is conservative**: General glob-vs-glob containment is undecidable, so the subset test is a decidable conservative approximation that errs toward refusal (fail-closed) rather than permissiveness.
- **Cascade semantics match existing revocation model**: Cascades affect *subsequent* policy decisions and *pending* approvals only; they do not unwind already-dispatched tool calls. This is consistent with the runtime's existing `revoked_by`/expiry/rate-limit behavior introduced in v0.7.
- **Single-parent provenance**: Each delegated capability derives from exactly one parent capability (a tree, not a DAG of authority), simplifying cascade traversal and audit; sessions may still independently hold multiple unrelated capabilities.
- **Reuses existing infrastructure**: The deterministic `decide()` policy chokepoint, the session graph, the approval queue, the audit writer, and the v0.7 capability constraint dimensions (expiry, rate limit, destructive, revocation) are reused; this feature extends them with provenance and cascade rather than introducing a parallel mechanism.
- **Enforcement remains LLM-isolated**: Consistent with the project constitution (Principle I), all delegation derivation and validation is deterministic and performed outside any model; the model is a requester only.
