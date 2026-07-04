# Google Workspace setup

CapDep supports Google Workspace in two modes:

- **Official remote MCP servers** (default): Gmail, Drive, Calendar, Chat,
  and People over streamable HTTP/OAuth.
- **Community wrapper** (`--mode community`): legacy local
  `gws-mcp-server` around the `gws` CLI, useful if you already rely on
  that setup or need Docs/Sheets tools.

Google documents the official remote endpoints and scopes in the
[Workspace MCP configuration guide](https://developers.google.com/workspace/guides/configure-mcp-servers).
CapDep treats these as production integrations: strict admission stays on,
every known tool is explicitly mapped, and unknown tools are refused until
reviewed.

## Official Mode

Prerequisites:

- Google Cloud CLI (`gcloud`).
- A Google Cloud project with the Workspace APIs enabled.
- OAuth consent configured with the scopes for the Workspace MCP servers you
  want to use.
- A Google OAuth client ID and secret exported as `GOOGLE_MCP_CLIENT_ID` and
  `GOOGLE_MCP_CLIENT_SECRET`.

CapDep can automate the safe Google Cloud pieces and then point you at the
Google-controlled Auth/Admin pages that still require browser approval:

```bash
capdep oauth google cloud-setup \
  --project PROJECT_ID \
  --services gmail,drive,calendar \
  --register-capdep
```

By default this is a dry run. Add `--apply` to run the generated `gcloud`
commands. Add `--create-project` and `--billing-account BILLING_ACCOUNT_ID` if
you want the helper to create and link a new project before enabling APIs.

The helper can:

- Create/select a Google Cloud project.
- Link a billing account when requested.
- Enable the Google Workspace APIs and official MCP services for the selected
  products.
- Register CapDep's local managed Google Workspace block.
- Print or open the Google Auth Platform and Workspace Admin API Controls pages.

The helper cannot bypass Google-controlled approval steps. You still need to
configure/publish the OAuth consent app, create the OAuth client, copy the
client ID/secret into CapDep, and trust/allow the OAuth client in Workspace
Admin API Controls for Workspace domains such as `joneslaw.io`.

Enable the Workspace APIs and MCP services:

```bash
gcloud services enable gmail.googleapis.com \
  drive.googleapis.com \
  calendar-json.googleapis.com \
  chat.googleapis.com \
  people.googleapis.com \
  --project=PROJECT_ID

gcloud services enable gmailmcp.googleapis.com \
  drivemcp.googleapis.com \
  calendarmcp.googleapis.com \
  chatmcp.googleapis.com \
  people.googleapis.com \
  --project=PROJECT_ID
```

Register the CapDep managed block:

```bash
capdep gworkspace-setup
```

Run one OAuth login per enabled official server. Each server gets the scopes
declared in the config and stores a refreshable token under
`~/.config/capabledeputy/oauth/`:

```bash
capdep oauth login --server google-gmail
capdep oauth login --server google-drive
capdep oauth login --server google-calendar
capdep oauth login --server google-chat
capdep oauth login --server google-people
```

This writes five strict upstream entries to
`~/.config/capabledeputy/daemon.yaml`:

- `google-gmail` -> `https://gmailmcp.googleapis.com/mcp/v1`
- `google-drive` -> `https://drivemcp.googleapis.com/mcp/v1`
- `google-calendar` -> `https://calendarmcp.googleapis.com/mcp/v1`
- `google-chat` -> `https://chatmcp.googleapis.com/mcp/v1`
- `google-people` -> `https://people.googleapis.com/mcp/v1`

To narrow services:

```bash
capdep gworkspace-setup --services gmail,drive,calendar
```

## Community Mode

Use this only when you specifically want the local `gws-mcp-server` wrapper:

```bash
npm install -g @googleworkspace/cli
gws auth setup
gws auth login -s drive,gmail,calendar,docs,sheets
npm install -g gws-mcp-server

capdep gworkspace-setup --mode community
```

## Verify

```bash
capdep daemon stop
capdep chat
```

Inside the REPL:

```text
/server
/tools google-
```

Expected official-mode servers:

- `google-gmail`
- `google-drive`
- `google-calendar`
- `google-chat`
- `google-people`

## Capability Mapping

The official mode pins every known tool explicitly and uses `strict: true`,
so unknown upstream tools are refused until reviewed.

Gmail drafts use the `to` argument as the policy target. Calendar mutations
materialize policy targets with calendar/event/attendee fields when the
upstream tool supplies them. This makes approval patterns and audit trails more
specific without enabling direct sends.

| Surface | CapDep kinds |
|---|---|
| Gmail reads | `GMAIL_READ` |
| Gmail draft/label mutation | `GMAIL_DRAFT`, `MODIFY_FS` |
| Drive reads | `DRIVE_READ` |
| Drive create/copy | `CREATE_FS` |
| Calendar reads | `CALENDAR_READ` |
| Calendar create/update/delete | `CREATE_CAL`, `MODIFY_CAL`, `DELETE_CAL` |
| Chat reads | `CHAT_READ` |
| Chat sends | `SEND_MESSAGE` |
| People/profile reads | `PEOPLE_READ` |

Workspace content carries `confidential.personal` and, where appropriate,
`untrusted.user_input`. After a session reads Workspace data, CapDep's
conflict rules prevent unsafe egress unless the operator explicitly uses an
approved override path.

## Revoke

For official mode, remove CapDep's cached OAuth tokens or revoke the OAuth app
grant from your Google account:

```bash
rm ~/.config/capabledeputy/oauth/google-*.json
```

For community mode:

```bash
gws auth logout
gws auth login -s ...
```
