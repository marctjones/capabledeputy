# Curated upstream MCP catalog

Vetted `upstream/` configs that let CapableDeputy drive **real** tools
behind the policy engine — restricted by construction. Native stubs
remain the deterministic dev/test path; these are the "real but
locked-down" path for demonstrating actual workflows.

## What maps to which business workflow

| Config | Servers | Priority workflow it serves |
|---|---|---|
| `official-reference.yaml` | filesystem, fetch, git, time, memory | doc drafting, web research, dev/work, reminders |
| `google-workspace.yaml` | official Gmail, Drive, Calendar, Chat, People MCP | email triage, Drive research, calendar, chat, contacts |
| `github.yaml` | GitHub official remote MCP | repository, issue, PR, and code review workflows |
| `microsoft-365.yaml` | Microsoft 365 / Graph MCP mapping fixture | Outlook, OneDrive/SharePoint, and calendar workflows |
| `notion.yaml` | Notion MCP mapping fixture | workspace research, page drafting, and database notes |
| `slack.yaml` | Slack official remote MCP | team communication |
| `kagi.yaml` | Kagi official MCP | web/news/search and page extraction |
| `playwright.yaml` | Playwright official MCP | browser automation and web workflow validation |
| `google-workspace-community.yaml` | legacy `gws-mcp-server` wrapper | compatibility path for Docs/Sheets or existing `gws` users |

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
3. **Destructive and active tools pinned to granular kinds.** Every write /
   edit / delete / commit / send / automation action is overridden to
   `MODIFY_*`, `DELETE_*`, `CREATE_*`, `SEND_EMAIL`, `SEND_MESSAGE`,
   or the narrow browser/macOS/iWork kinds (`BROWSER_NAVIGATE`,
   `BROWSER_INTERACT`, `BROWSER_SCRIPT`, `BROWSER_FILE`,
   `MACOS_APP_CONTROL`, `APPLE_MAIL_DRAFT`, `KEYNOTE_PRESENT`,
   `PAGES_EDIT`, `NUMBERS_EDIT`, etc.) so the right policy gates fire.
   The legacy `BROWSER_AUTOMATION` and `MACOS_AUTOMATION` kinds remain
   compatibility umbrellas, not the preferred mapping for new tools.
   Nothing destructive rides on inference.
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
  (`official-reference.yaml`), Google Workspace remote MCP, GitHub remote
  MCP, Slack remote MCP, Kagi MCP, and Playwright MCP.
- **Tier-1 mapping fixtures:** `microsoft-365.yaml` and `notion.yaml`
  pin CapDep's tool capability policy for those providers, but keep endpoint
  hostnames as operator-reviewed placeholders until the current producer MCP
  endpoint is confirmed for the deployment.
- **Community / compatibility:** `google-workspace-community.yaml` uses
  `gws-mcp-server`. Treat package upgrades as an admission event: re-review
  `rejected_tools` and the override map.

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
- Official remote configs that use OAuth expect operator-created client
  credentials in environment variables and a one-time
  `capdep oauth login --config ... --server ...` token bootstrap.
