# Google Workspace setup

CapableDeputy wires Google Workspace (Gmail / Drive / Docs / Sheets /
Calendar) via **`gws-mcp-server`** — a community MCP server that
shells out to the `gws` Workspace CLI. `gws auth login` caches OAuth
tokens in your OS keyring (AES-256-GCM at rest); `gws-mcp-server`
reuses those tokens for every API call. No second consent flow, no
plaintext credentials in `~/.config`.

> **Why not the "official" Google MCP server?** As of `@googleworkspace/cli`
> v0.22.5 (May 2026), the `gws` CLI itself does NOT include an MCP
> server subcommand — only a Discovery-API command surface for
> Drive/Gmail/etc. Blog posts from early 2026 announced MCP support,
> but the feature hasn't shipped. `gws-mcp-server` is the working
> bridge until that lands.

## Prerequisites

- **Node.js / npm** (`node --version` ≥ 18).
- **gcloud CLI** — required by `gws auth setup`. Install per
  <https://cloud.google.com/sdk/docs/install>.

## Install + authenticate (once)

```bash
# 1. Install the Workspace CLI.
npm install -g @googleworkspace/cli

# 2. One-time gcloud + OAuth client setup.
gws auth setup

# 3. Browser consent for the services you want.
gws auth login -s drive,gmail,calendar,docs,sheets

# 4. The MCP server that wraps the CLI.
npm install -g gws-mcp-server
```

That's it. Tokens are encrypted in your OS keyring; nothing
sensitive lands in a plaintext file under `~/.config`.

## Wire into CapableDeputy

```bash
capdep gworkspace-setup
```

This adds a managed `# BEGIN/END capdep-managed: gworkspace` block
to `~/.config/capabledeputy/daemon.yaml` that spawns
`npx gws-mcp-server --services drive,sheets,calendar,docs,gmail`
as an upstream MCP server. Subsequent `capdep chat` runs load
Workspace tools automatically — no `--config` needed.

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
capdep daemon stop   # if already running
capdep chat
# inside the REPL:
/tools gws
```

You should see 24 tools listed:

- **Drive** (8): `drive_list_files`, `drive_get_file`,
  `drive_create_file`, `drive_copy_file`, `drive_update_file`,
  `drive_delete_file`, `drive_export_file`, `drive_share_file`
- **Sheets** (4): `sheets_get_metadata`, `sheets_read_values`,
  `sheets_write_values`, `sheets_append_values`
- **Calendar** (5): `calendar_list_events`, `calendar_get_event`,
  `calendar_insert_event`, `calendar_update_event`,
  `calendar_delete_event`
- **Docs** (3): `docs_get_document`, `docs_create_document`,
  `docs_batch_update`
- **Gmail** (4 — READ-ONLY): `gmail_list_messages`,
  `gmail_get_message`, `gmail_list_threads`, `gmail_get_thread`

Note that Gmail in `gws-mcp-server` is read-only by design — no
`send`/`delete`/`label` tools. If you need to send mail, use the
bundled `mcp-server-imap` (separate path; see `docs/imap-setup.md`).

## Labels, capability gates, and what the agent can do

The managed block applies these defaults:

- **Inherent label** `confidential.personal` on every Workspace tool.
- **Explicit capability overrides** on every mutating tool so nothing
  destructive depends on inference:

  | Tool | Capability |
  |------|------------|
  | `drive_delete_file` | `DELETE_FS` |
  | `drive_update_file` / `drive_share_file` | `MODIFY_FS` |
  | `drive_create_file` / `drive_copy_file` | `CREATE_FS` |
  | `calendar_delete_event` | `DELETE_CAL` |
  | `calendar_update_event` | `MODIFY_CAL` |
  | `calendar_insert_event` | `CREATE_CAL` |
  | `sheets_write_values` / `sheets_append_values` | `MODIFY_FS` |
  | `docs_batch_update` | `MODIFY_FS` |
  | `docs_create_document` | `CREATE_FS` |

- **Read tools** (`*_list_*`, `*_get_*`, `*_read_*`) fall through to
  the adapter's name-based inference → `READ_FS` / `CALENDAR_READ`.

To audit what was assigned: `/tools gws` in chat. To override
anything that surprises you, edit the daemon config OUTSIDE the
`# BEGIN/END capdep-managed: gworkspace` markers — those edits
survive `gworkspace-setup --register-only`.

## Use

```bash
capdep chat
```

Try:

- "Summarize my unread email from this morning." (`gmail_list_messages`)
- "Read the Google Doc with id <DOC_ID> and pull out the action items." (`docs_get_document`)
- "What's on my calendar tomorrow?" (`calendar_list_events`)
- "Search Drive for files named 'budget'." (`drive_list_files`)

Each call goes through the chokepoint. Gmail/Drive/Calendar content
carries `confidential.personal` labels — once your session has read
Workspace data, it can't egress to an untrusted destination without
an explicit override.

## Revoke / re-do consent

```bash
gws auth logout       # clears keyring tokens
gws auth login -s ... # browser consent again
```

Or revoke the OAuth client entirely at
<https://myaccount.google.com/permissions>.
