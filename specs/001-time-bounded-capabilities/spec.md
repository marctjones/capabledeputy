# Feature Specification: Time-Bounded Capabilities

**Feature Branch**: `001-time-bounded-capabilities`
**Created**: 2026-05-15
**Status**: Implemented
**Input**: User description: "time-bounded capabilities — a Capability may carry an expiry; the policy engine deterministically denies an expired capability; a helper sets expiry from a duration; expiry is enforced at the policy chokepoint, not by the LLM"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Granting a capability that automatically stops working after a deadline (Priority: P1)

The operator authorizes the agent to perform a scoped action, but only
for a bounded window — for example, "you may send email to the
accountant, but only for the next 15 minutes." After the window
elapses, the same authorization no longer works, with no further
operator action required. The operator does not have to remember to
revoke it.

**Why this priority**: This is the core value. Without it, every grant
is effectively permanent-until-revoked, which forces the operator to
track and manually clean up authorizations — exactly the
approval-fatigue and over-authorization problem the product exists to
prevent. A capability that self-expires is the minimum viable slice.

**Independent Test**: Grant a capability with a short expiry, perform
the action successfully before the deadline, wait past the deadline,
attempt the same action, observe a deterministic denial citing
expiry. Fully testable with a single capability and a clock.

**Acceptance Scenarios**:

1. **Given** a session holds a capability whose expiry is in the
   future, **When** the agent attempts a matching action, **Then** the
   policy decision is unaffected by the expiry (it allows, denies, or
   requires approval exactly as it would for a non-expiring
   capability).
2. **Given** a session holds a capability whose expiry is in the past,
   **When** the agent attempts an action that would otherwise match
   that capability, **Then** the policy deterministically denies the
   action and the decision identifies expiry as the reason.
3. **Given** a session holds an expired capability and also a separate
   non-expired capability that matches the same action, **When** the
   agent attempts the action, **Then** the non-expired capability
   satisfies it and the action is allowed (an expired capability is
   inert, not poisonous).

---

### User Story 2 - Setting an expiry as a duration from now (Priority: P2)

The operator expresses the bound the way humans think about it — "for
10 minutes," "for the rest of this session's hour" — rather than
computing an absolute wall-clock timestamp by hand.

**Why this priority**: Reduces operator error (miscomputed
timestamps), but the system is still correct and usable without it if
the operator supplies an absolute deadline directly. Convenience on
top of P1, not a precondition for it.

**Independent Test**: Request a capability "good for N minutes,"
inspect the resulting capability, confirm its deadline is
approximately now + N minutes within a small tolerance, and confirm
behavior matches User Story 1 around that deadline.

**Acceptance Scenarios**:

1. **Given** the operator grants a capability "for the next 10
   minutes," **When** the capability is created, **Then** its expiry
   deadline equals the creation time plus 10 minutes.
2. **Given** a capability granted with a zero or negative duration,
   **When** it is created, **Then** it is already expired and the next
   matching action is denied for expiry.

---

### User Story 3 - Seeing time-remaining and expiry in operator views (Priority: P3)

When the operator inspects a session's authorizations, each
time-bounded capability shows that it is time-bounded and roughly how
much time remains (or that it has already lapsed), so the operator can
reason about what the agent can still do.

**Why this priority**: Improves operator situational awareness and
trust, but enforcement is fully correct without any display change.
Purely observability.

**Independent Test**: Grant a time-bounded capability, open the
operator inspection view, confirm the capability is annotated with its
bounded nature and remaining/expired state; confirm a non-expiring
capability is shown without that annotation.

**Acceptance Scenarios**:

1. **Given** a session holds a capability expiring in the future,
   **When** the operator inspects the session, **Then** that
   capability is shown as time-bounded with its remaining window.
2. **Given** a session holds an expired capability, **When** the
   operator inspects the session, **Then** that capability is shown as
   expired.

---

### Edge Cases

- **Decision-time evaluation**: Expiry is judged at the moment an
  action is attempted, not at grant time. A capability that is valid
  when granted but lapses before use must deny at use.
- **Boundary instant**: At the exact expiry instant the capability is
  treated as expired (the window is half-open: valid up to but not
  including the deadline).
- **Persistence across restart**: If the runtime restarts, a
  previously granted time-bounded capability must still expire at its
  original deadline — the deadline is absolute, not relative to
  process start.
- **Interaction with approval**: If a matching capability has expired,
  an action that would have been allowed by it must not silently fall
  through to an approval path it was not otherwise subject to; the
  outcome is the same as if that capability were absent.
- **Interaction with one-shot and revocation**: Expiry composes with
  the existing one-shot and prior-use-revocation behaviors — whichever
  condition makes the capability unusable first governs; none override
  expiry.
- **Clock source**: Expiry is evaluated against a single authoritative
  time source so two evaluations microseconds apart cannot disagree
  about validity in a way that affects auditability.
- **No matching non-expired capability**: When every matching
  capability is expired, the action is denied exactly as if no
  capability matched, with expiry surfaced as the reason rather than a
  generic "no capability."

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A capability MUST be able to carry an optional absolute
  expiry deadline. A capability without one behaves exactly as
  capabilities do today (no expiry).
- **FR-002**: The policy decision MUST treat a capability whose expiry
  deadline is at or before the decision time as if it does not match
  the attempted action.
- **FR-003**: When an action is denied solely because the only
  matching capabilities are expired, the policy decision MUST identify
  expiry as the reason, distinctly from a "no matching capability"
  denial, so audits can tell the two apart.
- **FR-004**: Expiry MUST be evaluated deterministically by the
  runtime at the policy decision point. No language-model output may
  influence whether a capability is considered expired.
- **FR-005**: A non-expired capability that matches an action MUST
  still satisfy that action even if other matching capabilities in the
  same session are expired.
- **FR-006**: The system MUST provide a way to create a capability
  whose expiry is a given duration after its creation time.
- **FR-007**: A capability created with a non-positive duration MUST
  be treated as already expired at first use.
- **FR-008**: A time-bounded capability's deadline MUST survive a
  runtime restart and continue to expire at its original absolute
  deadline.
- **FR-009**: Operator inspection views MUST distinguish a
  time-bounded capability from a non-expiring one and convey whether
  it is still within its window.
- **FR-010**: Every policy decision affected by expiry MUST be
  recorded in the audit trail with enough detail to reconstruct why
  the action was allowed or denied at that time.
- **FR-011**: Expiry MUST compose with existing capability constraints
  (scope/pattern match, one-shot consumption, prior-use revocation)
  such that a capability is usable only if it satisfies all applicable
  constraints simultaneously.

### Key Entities *(include if feature involves data)*

- **Capability**: An unforgeable, scoped authorization held by a
  session. Gains an optional expiry deadline attribute. All existing
  attributes (what action it authorizes, its scope/pattern, one-shot
  vs. session lifetime, prior-use revocation) are unchanged and
  continue to apply alongside expiry.
- **Policy Decision**: The deterministic verdict (allow / deny /
  requires-approval) produced for an attempted action against a
  session's labels and capabilities. Gains the ability to attribute a
  denial specifically to capability expiry.
- **Decision Clock**: The single authoritative notion of "now" used
  when evaluating expiry, so a decision is reproducible and auditable.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of attempts to use a capability after its expiry
  deadline are denied; 0% succeed.
- **SC-002**: An action attempted before a capability's deadline
  yields exactly the same decision it would have yielded if the
  capability had no expiry — measured as identical decision and
  reason across paired with/without-expiry scenarios.
- **SC-003**: A capability granted "for N minutes" expires within 1
  second of N minutes after its creation, for every tested N.
- **SC-004**: After a simulated runtime restart, a previously granted
  time-bounded capability still denies use past its original deadline
  in 100% of cases.
- **SC-005**: Expiry-driven denials are attributed to expiry (not
  generic "no capability") in 100% of cases, verifiable from the audit
  trail alone without inspecting code.
- **SC-006**: No expiry decision varies based on language-model
  output: with the policy-introspection tool absent entirely, expiry
  enforcement behaves identically — demonstrable as a passing
  invariant check.

## Assumptions

- The product's existing deterministic policy-decision point is the
  correct and only place to enforce expiry; this feature extends that
  point rather than introducing a new enforcement surface. (Consistent
  with the established principle that enforcement is deterministic and
  isolated from the language model.)
- Absolute UTC deadlines are the unit of truth; durations are a
  convenience that resolves to an absolute deadline at grant time.
- Capability persistence already exists; this feature adds the expiry
  attribute to what is persisted, and a storage-schema evolution is an
  acceptable, expected cost.
- Operator-facing inspection surfaces already enumerate a session's
  capabilities; this feature annotates that existing enumeration
  rather than building a new view.
- Wall-clock skew within the single runtime process is negligible for
  the windows operators will use (minutes to hours); sub-second
  precision at the exact boundary instant is resolved by the
  half-open-window rule, not by clock synchronization.
- "Expiry" is independent of, and composes with, the existing
  one-shot and prior-use-revocation mechanisms; this feature does not
  redefine those.
