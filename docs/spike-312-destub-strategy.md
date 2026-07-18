# Spike #312 — De-stub strategy: build native vs wire an MCP server

**Status:** resolved · **Date:** 2026-07-18 · **Blocks:** v0.58 (#324, #325, #326)
**Question:** for each stubbed capability (email, calendar, inbox, tasks,
web.fetch), do we build a native backend or wire an existing curated/real MCP
server?

## TL;DR decision

| Capability | Decision | Route |
|---|---|---|
| **email — read / draft** | **WIRE** | Gmail (`GMAIL_READ`/`GMAIL_DRAFT`), IMAP, apple-mail — retire `inbox.py` + `email.draft_*` stubs |
| **inbox — read/list** | **WIRE** | same servers; retire the in-memory `Inbox` stub |
| **email — SEND** | **BUILD native** (keep the stub's contract; de-stub delivery behind it) — *not* wireable safely today | native `email.send` |
| **calendar** | **WIRE** | Google Calendar / CalDAV MCP — retire `calendar.py` stub (see scope caveat) |
| **tasks** | **BUILD / keep native** | no production MCP server exists |
| **web.fetch** | **WIRE** | bundled `mcp-server-fetch` — retire the `WebMock` stub (`web.search` is already real) |

The single rule behind the table: **wire when kind + label + destructive gating
is sufficient; build native when the SEND/COMMIT contract matters.**

## The decisive finding (why the rule is what it is)

A native `ToolDefinition` and a wired-upstream `ToolDefinition` are **not gated
identically**. The upstream adapter (`upstream/adapter.py`) reconstructs only a
*subset* of the policy contract from `capability_kind` + `inherent_tags` +
`default_operation_for_kind()`:

- **Preserved when wired** (kind/label/operation-driven): the capability-grant
  structural check, `covers_kind` visibility, the destructive-op gate (via
  synthesized `operations`/`DESTRUCTIVE_KINDS`), label propagation, and the
  untrusted/confidential **egress-conflict floors** (labels are always threaded
  regardless of `effect_class`). Plus registration-time `strict` /
  `disabled_kinds` / `disabled_tools` refusal.
- **Dropped when wired**: `effect_class` (so the whole v2 effect_class rule leg
  is dormant — `client.py` only runs it `if tool.effect_class is not None`),
  the `social_commitment` assurance gate, the structured `approval_route`
  payload (the email body/recipient preview a human approves), and the per-tool
  `default_reversibility` irreversibility signal.

That dropped set **is the send/commit contract**. For a **read** it doesn't
matter — labels + untrusted-provenance floors do all the safety work, and a
wired read is gated exactly as well as a native one. For a **send** it matters a
lot: `email.send` is precisely the tool that sets `social_commitment=True`, an
`approval_route` with the body payload, and irreversible/external reversibility.

This is corroborated by the catalog itself: **every** curated config disables
outbound send — Gmail (`disabled_kinds: [SEND_EMAIL]`, `gmail.compose` scope
only), community-gws, IMAP, bundled-imap; apple-mail/outlook expose `*_DRAFT`
only. **There is no wired outbound-send path anywhere.** The native no-op
`email.send` outbox is the only send surface that exists, and it is the only one
carrying the full contract.

## Per-capability rationale

### email read/draft + inbox → WIRE (retire stubs)
Real wired paths already exist with correct labels: Gmail
`GMAIL_READ`/`GMAIL_DRAFT`, IMAP `IMAP_READ`, apple-mail `APPLE_MAIL_READ`/
`_DRAFT`, all carrying `untrusted.external` provenance and (Gmail) confidential
categories. Reads only need kind+label gating, which the adapter preserves.
**Retire** `inbox.py`'s in-memory store and `email.draft_*`; route through the
prefixed upstream tools (`google-gmail.*`, `bundled-imap.*`). **Timing
constraint** (from `docs/proposals/ingestion-model-for-reference-routing.md`):
untrusted `inbox.read` must be surfaced *labeled, behind a quarantined
extract/projection*, not raw to the planner — the #359 projection-only posture
already enforces this for `inbox.read`, so the wired reader must register under
the same projection-only treatment (or be renamed into the quarantined path).

### email SEND → BUILD native (de-stub delivery behind the existing contract)
No wireable send path exists, and wiring one through the adapter today would
silently drop `social_commitment` / `approval_route` / irreversibility — a
safety regression on the single most dangerous action. So: **keep the native
`email.send` ToolDefinition exactly as-is** (it already declares the full
contract) and replace only its *body* — the no-op `EmailOutbox.append` — with
real delivery (SMTP, or a Gmail-send call once scopes allow). The policy gate is
unchanged; only the actuator changes. #324's acceptance ("send a real email,
policy-gated") is met without touching the chokepoint.

> **Alternative (larger, deferred):** extend the upstream adapter to carry the
> send contract — let a `tool_overrides` entry declare `effect_class`,
> `social_commitment`, `approval_route`, `default_reversibility` for an upstream
> tool. That would make wiring a send-capable MCP server safe and is the *right*
> long-term shape, but it's a chokepoint change, not a v0.58 de-stub. Recommend
> filing it as a follow-up; do **not** block #324 on it.

### calendar → WIRE (with a scope caveat)
The Google Calendar path already maps read (`CALENDAR_READ`) and writes
(`CREATE_CAL`/`MODIFY_CAL`/`DELETE_CAL`) with `target_template` gcal URIs and the
destructive route — writes are commit-ish but the **destructive-op gate is
preserved** when wired (kind-driven), so the contract that matters for calendar
(destructive gating + labels) survives. **Retire** `calendar.py`. **Caveat to
fix in #325:** the `google-calendar` server currently declares *readonly* OAuth
scopes while overriding create/update/delete to write kinds — Google would
refuse the write. Either broaden the scopes (`calendar.events`) or ship
calendar as read-only-wired + keep native writes until scopes are widened.

### tasks → BUILD / keep native
No production MCP server exists for tasks in the catalog (Todoist/Google Tasks
are only referenced in a docstring). Keep the native store; de-stub it against a
real backend (Google Tasks via the existing Google OAuth path, or a local store)
only if #325 scope demands it. Lowest priority — least user-visible.

### web.fetch → WIRE (retire WebMock)
A real `mcp-server-fetch` already ships (`bundled-fetch`, `official-reference`
fetch) with `untrusted.external` labels + container isolation, and the
destination-gated egress floor from v0.54 (#296) already governs it — a fetch is
a read whose safety is label+destination-driven, which the adapter preserves.
**Retire** the `WebMock` stub; route `web.fetch` to the bundled fetch server.
`web.search` is already real (Brave/DuckDuckGo) — leave native.

## Cross-cutting enabling work (feeds v0.58)

1. **Adapter send-contract gap** (follow-up, not v0.58-blocking): let
   `tool_overrides` carry `effect_class` / `social_commitment` / `approval_route`
   / `default_reversibility` so a send-capable upstream tool is gated like the
   native one. Until then, **all send/commit stays native**.
2. **Calendar OAuth scope mismatch** (fix in #325): readonly scopes vs
   write-kind overrides.
3. **Ingestion/quarantine timing** (constrains #324/#325/#326): a de-stubbed
   reader that returns untrusted content must land it *labeled, behind the
   quarantined projection*, before the planner acts — honor the projection-only
   posture on the wired `inbox.read`.

## Mapping to v0.58 issues

- **#324 (email)** — BUILD native send (swap the actuator behind the existing
  contract); WIRE read/draft (Gmail/IMAP/apple-mail), retire `inbox.py` +
  `email.draft_*`.
- **#325 (calendar/inbox/tasks)** — WIRE calendar (fix scopes) + inbox; keep
  tasks native.
- **#326 (web.fetch)** — WIRE bundled fetch, retire `WebMock`.

Guiding principle throughout: **do not duplicate the real Google-Workspace
path** — route Gmail/Calendar/Drive/Chat/People capabilities *through* it; build
native only where (a) no server exists (tasks) or (b) the send/commit contract
would be lost by wiring (email send).
