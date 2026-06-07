# RFC: Dynamic filesystem labeling (Issue #5)

**Status:** implemented (declarative tiers); Starlark escape hatch deferred.
**Refs:** FR-024 (sticky labels), FR-025 (raise-only), §7.1 (Axis A),
§10.6 (PolicyScriptHost), [[issue #5]], [[issue #42]] (labeling-oracle epic).

## Problem

A raw `fs.read` attaches no Axis-A (data category) label, so
`~/Documents/Financial/budget.pdf` and `/etc/hosts` return with identical
labels. Axis A is therefore *dead* for filesystem data — egress checks
have nothing to fire on, and the bait-and-pivot exfiltration pattern
("read the budget, then email it out") is not structurally blocked for
local files. This is the single largest hole in the labeling oracle
(contingency #1 of the governance scope): the IFC guarantees ride on
correct labels, and fs reads were unlabeled.

## Design

A declarative labeler, loaded from `configs/fs_label_rules.yaml`, runs at
fs-read time and attaches the matched Axis-A category labels to the
result's `additional_tags`. Because the chokepoint composes
`additional_tags` into the session via `most_restrictive_inherit`
(monotone), the labels are **sticky** (FR-024) and **raise-only**
(FR-025): once attached they propagate through every later decision and
can never be silently dropped.

### Three tiers (a rule fires if ANY facet matches)

1. **path-prefix** — fast, matched on the expanduser'd path. The 80% case
   (`~/Documents/Financial/`, `~/.ssh/`).
2. **filename-glob** — `*.key`, `id_rsa*`, `*password*`. Catches sensitive
   files wherever they live.
3. **content-regex** — opt-in; only consulted when the file body is read
   (`BEGIN OPENSSH PRIVATE KEY`). This is the raise-only "content scan"
   tier: it can only escalate.

### Labels

`confidential.<category>` resolves the category's tier from the
`labels.yaml` catalog (#50) — so `confidential.financial` is `restricted`,
not a flattened `REGULATED`. Provenance strings (`untrusted.external`,
`trusted.user_direct`) are also accepted. An unknown label fails closed at
load (a typo must never *under*-classify).

### Integration

- `policy/fs_labeling.py` — `FsLabeler` + `load_fs_label_rules`.
- `tools/native/fs.py` — `make_fs_tools(labeler)` wraps the `fs.read` /
  `fs.read_pdf` handlers; the wrapper consults the labeler with the path
  (and, when any content rules exist, the file body) and merges the result
  into `additional_tags`.
- `app.py` / `daemon/lifecycle.py` — the daemon loads the rules from the
  configs dir and threads the labeler into the App. Absent file ⇒ no-op.

## Invariants

- **Monotone / raise-only.** Composition is `most_restrictive_inherit`;
  a rule can only add categories/levels, never remove them. Multiple
  matching rules union.
- **Fail-closed config.** A malformed rule or unknown label refuses daemon
  start, never silently under-labels.
- **Provenance unchanged.** The base handler still marks fs reads
  `EXTERNAL_UNTRUSTED`; category labels are added on top.

## Why this is the moat

IronClaw gates `FileRead` per path (yes/no) but a read has *no
information-flow consequence* afterward. NanoClaw/NemoClaw isolate at
execution and don't model what was read. Propagating IFC labels on local
filesystem data — so a later egress is blocked *because of what the
session read* — is uniquely CapableDeputy's.

## Deferred

- **Starlark escape hatch** (`starlark:` match key) for complex predicates
  (e.g. "path ends with `.env` AND body contains `SECRET`"). The
  `PolicyScriptHost` (#46) already exists; wiring a per-rule predicate is a
  follow-up. The declarative tiers cover the common cases first.
- **Directory-walk / mount-aware rules** and **symlink canonicalization**
  beyond `expanduser` — a hardening follow-up.
