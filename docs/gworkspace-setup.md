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

Authenticate with Application Default Credentials using the scopes you enabled:

```bash
gcloud auth application-default login --scopes=\
https://www.googleapis.com/auth/gmail.readonly,\
https://www.googleapis.com/auth/gmail.compose,\
https://www.googleapis.com/auth/drive.readonly,\
https://www.googleapis.com/auth/drive.file,\
https://www.googleapis.com/auth/calendar.calendarlist.readonly,\
https://www.googleapis.com/auth/calendar.events.freebusy,\
https://www.googleapis.com/auth/calendar.events.readonly,\
https://www.googleapis.com/auth/chat.spaces.readonly,\
https://www.googleapis.com/auth/chat.memberships.readonly,\
https://www.googleapis.com/auth/chat.messages.readonly,\
https://www.googleapis.com/auth/chat.messages.create,\
https://www.googleapis.com/auth/chat.users.readstate.readonly,\
https://www.googleapis.com/auth/directory.readonly,\
https://www.googleapis.com/auth/userinfo.profile,\
https://www.googleapis.com/auth/contacts.readonly
```

Then register the CapDep managed block:

```bash
capdep gworkspace-setup
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

| Surface | CapDep kinds |
|---|---|
| Gmail reads | `GMAIL_READ` |
| Gmail draft/label mutation | `CREATE_FS`, `MODIFY_FS` |
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

For official mode, revoke or refresh the Google OAuth credentials used by
Application Default Credentials:

```bash
gcloud auth application-default revoke
gcloud auth application-default login
```

For community mode:

```bash
gws auth logout
gws auth login -s ...
```
