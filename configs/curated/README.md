# Curated upstream MCP catalog

Vetted `upstream/` configs that let CapableDeputy drive **real** tools
behind the policy engine — restricted by construction. Native stubs
remain the deterministic dev/test path; these are the "real but
locked-down" path for demonstrating actual workflows.

## What maps to which business workflow

| Config | Servers | Priority workflow it serves |
|---|---|---|
| `official-reference.yaml` | filesystem, fetch, git, time, memory | doc drafting, web research, dev/work, reminders |
| `slack.yaml` | slack (official) | team communication |
| `google-workspace.yaml` | gmail, gcal, gtasks (community/preview) | email triage & briefing, calendar, tasks |

Light purchasing has **no reputable MCP server** and intentionally
stays a native stub (human-approved, demo-only) — that is a deliberate
scope decision, not a gap.

## Security posture (uniform across the catalog)

1. **`strict: true` everywhere.** An upstream tool that is neither
   explicitly overridden nor confidently classifiable is **refused
   registration** — never silently granted a permissive default
   (`upstream/adapter.py`, WI-1). The set of refused tools is exposed as
   `LabeledMcpAdapter.rejected_tools`.
2. **`rejected_tools` *is* the admission step.** Bring a server up once,
   read the rejected list, then add an explicit `tool_overrides` entry
   for each tool you actually intend to allow (with the correct granular
   capability kind). Anything you don't map stays unavailable. This is
   the admission-control workflow — by hand, deterministic, auditable.
3. **Destructive tools pinned to granular kinds.** Every write / edit /
   delete / commit / send is overridden to `MODIFY_*` / `DELETE_*` /
   `CREATE_*` / `SEND_EMAIL` so the policy engine's destructive-op and
   egress conflict rules fire. Nothing destructive rides on inference.
4. **Least-privilege isolation.** Each server runs containerized: no
   network unless the tool inherently needs it, and where it does, an
   explicit host allowlist (placeholder `REPLACE...`/host entries — set
   these per deployment); scoped, read-only volumes by default.
5. **Provenance labels.** Inbound third-party content (fetch, inbox,
   Slack history) carries `untrusted.external`; personal sources carry
   `confidential.personal`. The conflict-rule engine then blocks
   exfiltration and gates regulated data automatically.

## Producer trust tiers

- **Official / low risk:** the `modelcontextprotocol` reference servers
  (`official-reference.yaml`) and Slack's official server.
- **Community / preview — accepted with mitigations:**
  `google-workspace.yaml` uses the community Google Workspace MCP
  (official is Developer Preview). It is the **most locked-down** config
  precisely because of the supply-chain risk: strict + every tool
  explicitly overridden + network allowlisted + OAuth-scoped. Treat any
  upgrade of that package as an admission event (re-review
  `rejected_tools` and the override map).

## Caveats (honest)

- `command:` lines are canonical at time of writing; **verify against
  each producer's current docs** before use — upstream packaging churns.
  The security fields (overrides / labels / isolation) are what matter
  and are launcher-independent.
- Tool name lists in the overrides are the security-critical ones we
  know; they are **not** asserted to be exhaustive. `strict: true` is
  what makes that safe: anything unlisted is refused, not guessed.
- These configs are inert until an operator explicitly loads one; none
  is wired into the default app.
