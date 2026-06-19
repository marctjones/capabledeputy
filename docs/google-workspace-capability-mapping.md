# Google Workspace Capability Mapping

CapDep maps Google Workspace MCP operations to capability kinds by **effect**,
not by product. The official remote Workspace config is
`configs/curated/google-workspace.yaml`; `capdep gworkspace-setup` writes the
same shape into the user-local daemon config.

The mapping is fail-closed: each official server uses `strict: true`, so an
unmapped upstream tool is refused at registration time.

## Effect Tiers

| Effect | Capability kind |
|---|---|
| Gmail read/search | `GMAIL_READ` |
| Drive read/search/download | `DRIVE_READ` |
| Calendar read/free-busy | `CALENDAR_READ` |
| Chat read/search | `CHAT_READ` |
| People/profile/contact read | `PEOPLE_READ` |
| Draft/create local-ish state | `CREATE_FS`, `CREATE_CAL` |
| Modify existing state | `MODIFY_FS`, `MODIFY_CAL` |
| Delete state | `DELETE_FS`, `DELETE_CAL` |
| Send a chat/message | `SEND_MESSAGE` |
| Send email | `SEND_EMAIL` |

`READ_FS *` remains a backward-compatible union for the external read kinds,
but new grants should use the granular kinds so "read local files" is distinct
from "read Gmail/Drive/Chat/People".

## Official Server Mapping

| Server | Endpoint | Tools |
|---|---|---|
| `google-gmail` | `https://gmailmcp.googleapis.com/mcp/v1` | `search_threads`, `get_thread`, `list_drafts`, `list_labels` -> `GMAIL_READ`; `create_draft` -> `CREATE_FS`; label tools -> `MODIFY_FS` |
| `google-drive` | `https://drivemcp.googleapis.com/mcp/v1` | search/read/download/metadata/permissions -> `DRIVE_READ`; create/copy -> `CREATE_FS` |
| `google-calendar` | `https://calendarmcp.googleapis.com/mcp/v1` | list/get/suggest -> `CALENDAR_READ`; create/update/respond/delete -> `CREATE_CAL`/`MODIFY_CAL`/`DELETE_CAL` |
| `google-chat` | `https://chatmcp.googleapis.com/mcp/v1` | list/search -> `CHAT_READ`; `send_message` -> `SEND_MESSAGE` |
| `google-people` | `https://people.googleapis.com/mcp/v1` | profile/contact/directory search -> `PEOPLE_READ` |

## Safety Notes

- Chat sends are social egress. `SEND_MESSAGE` participates in the same
  egress conflict checks as email: untrusted or health data cannot be sent,
  and financial data is blocked by the financial egress floor.
- Gmail drafts are not egress by themselves, so they map to `CREATE_FS`.
  Sending email remains `SEND_EMAIL`.
- Drive permission writes are not exposed in the current official mapping. If
  a future tool grants external access to a Drive file, it must be treated as
  egress rather than ordinary `MODIFY_FS`.
- Calendar event writes are local state changes until they invite or notify an
  external party. A future attendee-aware mapper should route external invites
  through an egress/approval path.
- Workspace data is labeled `confidential.personal`; user-authored or
  third-party content is also treated as untrusted input where appropriate.
