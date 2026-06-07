# Implementation plan & milestones

Living plan that organizes the open GitHub issues into sequenced
milestones with dependencies. Authoritative status is GitHub; this doc is
the *sequencing rationale*. Last refreshed 2026-06 (dev cycle after
v0.15.x).

Milestones (GitHub): **v0.16** Policy expressiveness & labeling · **v0.17**
Gap hardening & explainability · **v0.5** UX EPIC (in flight) · **Backlog**
Substrate breadth & formal models.

Two themes drive priority, both from `docs/security-alignment-assessment.md`:
1. **Decision fatigue** — coarse policy → rubber-stamping → eroded human
   oversight. Fixed by the decision-refinement layer (EPIC #41).
2. **The labeling oracle** — IFC guarantees ride on correct labels. Fixed
   by broadening label coverage (EPIC #42).

---

## Recently shipped (this cycle)

| # | What | Milestone |
|---|---|---|
| #2 | Agent-loop cap-fire auditability + thrash guard | — (P0 bug) |
| #50 | Catalog-aware tier resolution | v0.16 / #42 |
| #52 | Restricted-tier Pattern ③/⑤ floor in per-turn select_mode | v0.17 / #43 |
| #53 | `capdep policy models` — loud Biba gap | v0.17 / #43 |
| #46 | DecisionInspector / Starlark loader (layer is now LIVE) | v0.16 / #41 |
| #48 | Read-only session-history summary → inspectors (frequency policy) | v0.16 / #41 |
| #49 | `capdep why` — explain the rule/floor/inspector that fired | v0.17 / #43 |
| #47 | Starlark starter library (4 scripts + 2 builtins; 2 scripts blocked) | v0.16 / #41 |
| #5 | Dynamic filesystem labeling | v0.16 / #42 |
| #33 | Design: Workspace capability mapping | v0.16 / #42 |
| #34 | Email labeling — design + content-rule impl (raise-only labeler) | v0.16 / #42 |
| #13 | Credential vault (spawn-time; per-call needs #15/#16) | v0.17 |

**EPIC #41 essentially complete** (layer live, frequency policy, `capdep why`).
**EPIC #42 core shipped** (catalog tiers, fs + email labelers); remaining
is #51 (canonical ids) + the identity-dependent email layers.

---

## v0.16 — Policy expressiveness & labeling

The highest-leverage milestone: turn the refinement layer on (done) and
make labels real.

### EPIC #41 — Activate the decision-refinement layer
- ✅ **#46** wire the loader (done — layer is live)
- ◐ **#47** starter library — *blocked on #48 + richer session fields* for
  relationship-aware / frequency / reversible-write scripts. Shipped:
  sensitive-egress-confirm, purpose-scoped-relax, + the two builtins.
- ▶ **#48** thread a read-only session-history summary into inspector
  inputs (recent effect kinds, counts, recipients). **Next in this epic** —
  unblocks frequency caps + the rest of #47. Small, contained.
- → **#49** `capdep why <decision>` (tracked under v0.17 #43; pairs well
  here since the inspector layer adds new decision origins to explain).

### EPIC #42 — Strengthen the labeling oracle
- ✅ **#50** catalog-aware tiers · ✅ **#5** fs labeling
- ✅ **#33, #34** design docs (mapping + email labeling) — *design closed;
  implementation tracked under #51 and the email labeler*
- ▶ **email labeler** (impl of #34) — declarative `email_label_rules.yaml`
  + per-message hook, reusing the #5 labeler shape. **Next labeling impl.**
- → **#51** Gmail/Drive/Calendar SourcePort canonical ids (v0.17) — the
  identity layer both #33 and #34 depend on for external-recipient and
  message-id binding.

**Sequencing within v0.16:** #48 → finish #47 ; email labeler (uses #5
shape) ; then #51 unlocks the identity-dependent parts of #33/#34.

---

## v0.17 — Gap hardening & explainability

Close/guard the documented gaps; improve operator trust.

### EPIC #43 — Harden documented model/principle gaps
- ✅ **#52** restricted floor · ✅ **#53** loud Biba
- ▶ **#49** `capdep why <decision>` (P3 but high trust-value; the audit
  already has the data — surface the rule/floor/inspector that fired).
- ▶ **#54** purpose-contamination visible audit residual (P2) — flag
  decisions where inadmissible-category data is in-context; pairs with the
  P4 purpose-limitation story.
- ▶ **#55** cross-host RemoteApprovalEnvelope four-axis wire format (P2) —
  signed-protocol versioning for federation.

### Standalone v0.17
- **#13** credential vault (P1) — inject secrets at the chokepoint, never
  in LLM context. Highest standalone P1; independent of the epics.
- **#51** SourcePort canonical ids (P2) — also serves #42; schedule here.
- **#11** quarantined-extract schema library (P2) — EmailForwardable,
  WebPagePublicFacts; complements the email labeler.

**Sequencing:** #13 (independent, high value) ‖ #51 (serves both
milestones) → #49/#54 (explainability) → #55 (federation).

---

## v0.5 — UX EPIC (in flight, parallel track)

Surface convergence + the agent-cancellation papercuts. Independent of the
policy/labeling work, so it can proceed in parallel by whoever owns UX.
- P1 cluster: **#16** REPL parity, **#22** Rich Live streaming, **#23**
  Ctrl-C cancel, **#27** inline approval banner, **#31** cancel on
  disconnect, **#17** split-pane viewer.
- P2/P3: **#32** UI heartbeat, **#19** sixel/kitty, **#28** color palette,
  **#29** unicode width safety.

Note: #23/#31/#32 (turn cancellation) share machinery with the agent loop
already touched by #2 — coordinate so the cancel paths stay consistent.

---

## Backlog — Substrate breadth & formal models (v1.x / on-demand)

Deferred provider backends + formal work. Pull forward on demand.
- Isolation: **#9** Podman-by-default for upstream MCP, **#14** per-upstream
  egress allowlist (stdio path).
- Providers: **#56** more VersionedWritePort backends (Drive/S3),
  **#57** Modal/Firecracker actuators, **#51** also lands providers here.
- Formal: **#58** lattice join/dominance operator, **#59** ocap
  cascade-revocation eager teardown, **#45** formal-model completeness.

---

## Dependency graph (the load-bearing edges)

```
#48 (history) ──▶ #47 (frequency/relationship scripts)
#5 (fs labeler shape) ──▶ email labeler (#34 impl)
#51 (canonical ids) ──▶ external-recipient gates (#33), message-id bind (#34)
#13 (credential vault) ── independent, high value
agent-loop cancel (#23/#31/#32) ── coordinate with #2's loop changes
```

## Recommended next 3 (refreshed)

1. **#51** SourcePort canonical ids (Gmail/Drive/Calendar) — unblocks the
   identity-dependent layers of #33/#34 (external-recipient gates,
   Message-ID binding, sender-provenance) and hardens confused-deputy.
2. **#54** purpose-contamination visible audit residual (P2) — closes the
   most-cited honest gap (P4) with a visible signal, even pre-enforcement.
3. **#11** quarantined-extract schema library (P2) — EmailForwardable /
   WebPagePublicFacts; complements the email labeler + dual-LLM path.

Container-per-call isolation (#15/#16) is the prerequisite for the
*remaining* halves of #13 (echo-resistance) — pull it forward if
credential echo-resistance becomes a priority.
