# Google Workspace setup

CapableDeputy wires Google Workspace (Gmail / Drive / Docs / Sheets /
Calendar) via the **Google Workspace CLI** — `gws` from
`@googleworkspace/cli`. Google maintains the binary, owns the
Discovery-API-derived tool surface, and stores OAuth tokens in your
OS keyring (AES-256-GCM at rest). CapableDeputy spawns it as an
upstream MCP server and proxies stdio through the policy chokepoint.

## Prerequisites

- **Node.js / npm** — for installing the CLI.
  (`node --version` should print v18 or later.)
- **gcloud CLI** — required by `gws auth setup` to create the
  OAuth client in your Google Cloud project. Install per
  <https://cloud.google.com/sdk/docs/install>.

## Install + authenticate

```bash
# 1. Install the CLI (one-time)
npm install -g @googleworkspace/cli

# 2. One-time auth setup — runs gcloud project picker + creates
#    an OAuth client for you. Follow the prompts.
gws auth setup

# 3. Per-machine browser consent for the services you want
gws auth login -s drive,gmail,calendar,docs,sheets
```

That's it. Tokens are encrypted in your OS keyring; nothing
sensitive lands in a plaintext file under `~/.config`.

## Wire into CapableDeputy

```bash
capdep gworkspace-setup
```

This adds a managed `# BEGIN/END capdep-managed: gworkspace` block
to `~/.config/capabledeputy/daemon.yaml` that spawns
`gws mcp -s drive,gmail,calendar,docs,sheets` as an upstream MCP
server. Subsequent `capdep chat` runs load Workspace tools
automatically — no `--config` needed.

To narrow services:

```bash
capdep gworkspace-setup -s drive,gmail
```

To re-register only (e.g. after editing the managed block by hand
to add `tool_overrides`):

```bash
capdep gworkspace-setup --register-only
```

## Verify

```bash
capdep daemon stop  # if already running
capdep chat
# in the REPL:
/tools gws
```

You should see Workspace tools listed with names like
`gws.gmail.users.messages.list`, `gws.drive.files.list`, etc. The
exact naming follows Google's Discovery API method paths — they're
dense but stable. Don't worry about memorizing; the agent finds
what it needs by description.

## Labels, capability gates, and what the agent can do

The managed block applies these defaults:

- **Inherent label** `confidential.personal` on every Workspace tool.
  Reading anything from Workspace marks the session — outbound
  egress to untrusted destinations is blocked.
- **Explicit capability overrides** on the most-dangerous tools so
  nothing destructive depends on the inference fallback:

  | Tool | Capability | Extra labels |
  |------|------------|--------------|
  | `gmail.users.messages.send` | `SEND_EMAIL` | — |
  | `calendar.events.delete` | `DELETE_CAL` | `confidential.personal` |
  | `calendar.events.update` | `MODIFY_CAL` | `confidential.personal` |
  | `drive.files.delete` | `DELETE_FS` | `confidential.personal` |
  | `drive.files.update` | `MODIFY_FS` | `confidential.personal` |

- **Other tools** inherit from the adapter's name-based heuristic:
  `*.list`, `*.get` → `READ_FS`; `*.create`, `*.insert` → `CREATE_FS`;
  `*send*` → `SEND_EMAIL`; etc. Unmatched tools fall back to
  `READ_FS` (`strict: false`) — safe, but worth auditing.

To audit what was assigned, use `/tools gws` in chat. To override
anything that surprises you, edit the daemon config OUTSIDE the
`# BEGIN/END capdep-managed: gworkspace` markers — those edits
survive `gworkspace-setup --register-only`.

## Use

```bash
capdep chat
```

Try:
- "Summarize my unread email from this morning." (gmail.users.messages.list + get)
- "Read the Google Doc with id <DOC_ID> and pull out the action items." (docs.documents.get)
- "What's on my calendar tomorrow?" (calendar.events.list)
- "Search Drive for files named 'budget'." (drive.files.list)

Each call goes through the chokepoint. Gmail content carries
`confidential.personal` labels — once your session has read Gmail,
it can't egress to an untrusted destination without an explicit
override.

## Revoke / re-do consent

```bash
gws auth logout       # clears keyring tokens for this machine
gws auth login -s ... # browser consent again
```

Or revoke the OAuth client entirely at
<https://myaccount.google.com/permissions>.

## Service selection trade-off

Each service adds 10–80 tools. The default
`drive,gmail,calendar,docs,sheets` registers ~150–250 tools. The
adapter declines to register tools without a confident capability
mapping, so the actual visible-to-agent count is smaller — but
worth tuning. Drop services you don't use:

```bash
capdep gworkspace-setup -s gmail,calendar
```
