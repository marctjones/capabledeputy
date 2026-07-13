# Proposal: Ingestion model for Pattern-3/5 safe handling (v0.55)

**Status:** proposal · **Decides:** #300, #301, #302, #303 · **Author:** design pass, 2026-07-13

## The problem this solves

v0.55 wants restricted-tier data to be *used* (routed, transformed, summarized)
without the planner ever holding the raw value — Pattern 3 (reference handles)
and Pattern 5 (sealed sandbox) — instead of the turn being refused. The
mechanism exists and works for data **already in labeled memory** (proven end to
end in `tests/test_reference_routing.py`: `memory.handle → fs.create(content=<handle>)`
routes the real value; the planner only holds the handle UUID).

The gap is **getting restricted source data into a handle in the first place**,
which has two coupled sub-problems:

1. **No source→handle path is wired.** Only `memory.handle` produces handles,
   and only for data already in memory. `make_handle_wrapper` /
   `wrap_output_with_handles` (the producer side) exist in
   `patterns/reference_handle.py` but are wired to **zero** read tools. A read
   of restricted data (e.g. `fs.read` of a bank statement) returns raw text.

2. **First-read timing.** A clean session starts in TURN_LEVEL, where `fs.read`
   is visible. The planner reads the file *raw* — the value lands in its
   context — and only *then* does the restricted taint attach and mid-turn
   re-selection (`loop.py:1596`) flip to REFERENCE. The horse is already out.

The core insight: **for "the planner never holds the raw value" to actually
hold, restricted data must be labeled *before the planner acts on it*.** A model
where the planner freely reads a file and we retroactively taint the session is
inherently unsafe — and that is exactly why restricted reads correctly refuse
today.

## The decision: two viable models

### Model A — Ingest-then-handle (recommended)

Restricted data enters the session only through a **SourcePort/ingest step that
lands it in labeled memory**, never via a planner-issued raw read. The planner
then operates exclusively on `memory.handle` references.

Flow:
1. A non-planner ingest path (SourcePort, an explicit `capdep` command, or a
   system pre-step) reads the restricted source and writes it to memory with the
   correct Axis-A label — the planner is not in the loop for this step.
2. The session is now restricted-tainted *before the first planner turn*, so
   `select_mode` picks REFERENCE from the start. No TURN_LEVEL raw-read window.
3. The planner sees `memory.handle` (visible in REFERENCE) and the handle-aware
   routing tools (`fs.create`/`fs.modify`, and later `email.send`), and routes
   the handle. Raw readers (`fs.read`, `memory.read`, `web.fetch`) are hidden.

Why recommended:
- **Closes the timing hole by construction** — the planner never has a
  raw-read window because the restricted value is labeled at ingest.
- **Minimal new surface** — `memory.handle` + handle-aware routing tools
  (#301, already partly done) are the whole mechanism; the "new" piece is an
  ingest command/SourcePort that writes labeled memory, which the SourcePort
  design (v0.35) already anticipates.
- **Matches the threat model** — "the planner requests; the runtime grants";
  ingest is a runtime action, not a planner action.

Cost:
- Requires a real ingest entry point for restricted sources ("attach this file
  to the session as labeled memory"). This is a UX addition, not deep policy
  work.
- "Just point the agent at a folder and let it read sensitive files" is *not*
  supported for restricted data — and shouldn't be. That is the honest boundary.

### Model B — Tier-gated read-to-handle

Keep planner-issued reads, but wrap read tools so that when a read *would*
return restricted-tier data, it returns a **handle** instead of raw text
(via `make_handle_wrapper`), and the raw variant is hidden/denied for
restricted tiers.

Flow:
1. `fs.read` is wrapped: it resolves the file, computes its label; if the label
   is restricted-tier, the output value is auto-issued as a handle
   (`wrap_output_with_handles`) and the planner receives `{path, handle}`, never
   the text.
2. Below restricted tier, it returns text as today.

Why not recommended as the primary model:
- **The timing hole is only half-closed.** The wrap decision happens *inside the
  read handler*, which needs to know the tier *before* returning — fine for
  path/rule-based labels (`fs_label_rules`), but content-based labels
  (`content_regex`) are only known *after* reading the bytes, at which point the
  handler has them in memory (not the planner's, but a step closer). More
  importantly, the *mode* is still TURN_LEVEL at first read, so the raw `fs.read`
  variant is visible unless we also hide it — which means a tier check at
  tool-surface-filtering time, i.e. we've reinvented the ingest gate less
  cleanly.
- **`_RAW_LABELED_DATA_TOOLS` is a hardcoded set** — the same "enumerated, not
  declared" smell as the v0.54 egress bug. Doing this well means making
  "returns-raw-labeled-data" a declared tool property, which is real refactor.

Model B is worth adding *later* as a convenience for the "read a local file"
workflow, layered on top of Model A — but it should not be the load-bearing
mechanism.

## Recommendation

**Adopt Model A as the v0.55 model.** Concretely:

1. **#301 (done in part):** keep the handle-aware routing tools
   (`fs.create`/`fs.modify` landed; add `email.send`/`memory.create` body/value
   when de-stubbed in v0.58).
2. **#300:** restricted sessions select REFERENCE (already works once handle
   tools are visible — landed).
3. **New (the real work):** a runtime ingest entry point — `capdep ingest <path>
   --as <category>` / a SourcePort attach RPC — that writes the source into
   labeled memory *outside* a planner turn. This is what makes the canonical
   "file my bank statement" workflow expressible safely.
4. **#302 (CaMeL):** same shape — untrusted email must reach the planner only
   through `quarantined.extract`, which reads a *memory* value. So inbox
   ingestion → labeled memory → `quarantined.extract` projection is the path;
   only then hide `inbox.read` in DUAL_LLM. Do not hide the raw reader before
   the quarantined path is wired.
5. **#303:** the validation workflow is: ingest a restricted doc → planner routes
   it via handle under REFERENCE → assert (a) mode REFERENCE, (b) routing tool
   acted on the real value, (c) planner context never contained the raw value.
   The `test_reference_routing.py` probe is the seed; extend it to drive the real
   daemon and the ingest step.

## Open question for the owner

The one product decision: **is "point the agent at a folder of sensitive files
and let it work" a supported workflow, or is explicit ingest acceptable?** Model
A says explicit ingest (safe, slightly more friction). If the folder-scanning UX
is a hard requirement, that pushes toward Model B's tier-gated read-to-handle as
a first-class path, with the timing hole addressed by making restricted-tier
raw reads *deny in TURN_LEVEL* (forcing the wrapped handle variant) — more work,
and a weaker guarantee at the edges. Recommendation stands with Model A.
