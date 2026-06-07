# Design: Google Workspace operations → CapabilityKind mapping (#33)

How each Google Workspace MCP operation maps to a CapableDeputy
`CapabilityKind`, so the policy engine gates Workspace actions with the
same four-axis machinery as native tools. Gmail is shipped in
`configs/google-workspace-local.yaml`; this doc records the design,
generalizes it to Drive + Calendar, and lists the gaps.

## Principle

The MCP server is intentionally wide-open; **the capability mapping is
where each operation acquires its policy meaning.** Strict mode
(`strict: true`) refuses any unmapped tool, so the mapping is the
*entire* enabled surface — there is no silent passthrough.

Operations sort into five effect tiers:

| Tier | CapabilityKind | Gate character |
|---|---|---|
| Read | `*_READ` (e.g. `GMAIL_READ`) | auto-grantable; taints the session with the source's labels |
| Local draft / create | `CREATE_FS` | no egress, no social commitment — lightweight |
| In-place modify / organize | `MODIFY_FS` | gated behind explicit grant; not an egress |
| Destructive (trash/delete) | `DELETE_FS` | destructive-op gate (needs `allows_destructive`) |
| Egress / send | `SEND_EMAIL` (+ purchase-like for paid) | ApprovalRoute: full preview, irreversible, social-commitment |

The discriminator is **effect**, not product: "create a Drive file" and
"create a Gmail draft" are both `CREATE_FS`; "share a Drive file
externally" and "send a Gmail" are both egress.

## Gmail (shipped)

See `configs/google-workspace-local.yaml`. Summary: read surface →
`GMAIL_READ`; drafts → `CREATE_FS`/`MODIFY_FS`/`DELETE_FS`; sends
(`send_gmail_message`, `send_gmail_draft`) → `SEND_EMAIL`; label/move ops
→ `MODIFY_FS`; trash → `DELETE_FS`. Inbound mail carries
`untrusted.external` + `confidential.personal` server-wide (refined
per-message in [[email-labeling-design]] / #34).

## Drive (proposed)

| Operation | CapabilityKind | Notes |
|---|---|---|
| `search_files`, `get_file_metadata`, `read_file_content`, `download_file_content`, `list_recent_files` | `DRIVE_READ` (or `READ_FS` union) | taints with the file's labels — pairs with a Drive labeler (analogue of fs labeling, #5) |
| `create_file`, `copy_file` | `CREATE_FS` | non-destructive create |
| update/rename/move | `MODIFY_FS` | in-place; a versioned backend earns reversible/system (VersionedWritePort, #56) |
| delete/trash | `DELETE_FS` | destructive-op gate |
| **change sharing / permissions** | `SHARE_EXTERNAL` (egress) | **the Drive egress gate** — granting external read is exfiltration; must route through an ApprovalRoute, NOT `MODIFY_FS` |

The one subtlety Drive adds over Gmail: **permission changes are egress.**
Mapping `get_file_permissions` is read; *setting* a permission that adds
an external principal is the moment data leaves the boundary and must be
treated like a send.

## Calendar (proposed)

| Operation | CapabilityKind | Notes |
|---|---|---|
| `list_calendars`, `list_events`, `get_event`, `suggest_time` | `CALENDAR_READ` | event bodies can be `confidential.personal` |
| `create_event`, `update_event` | `CREATE_FS` / `MODIFY_FS` | reversible (event has history) |
| `delete_event` | `DELETE_FS` | destructive-op gate |
| **`create_event` / `update_event` with external attendees** | egress (`SEND_EMAIL`-class) | inviting an external attendee mails them the event details — egress |
| `respond_to_event` | `MODIFY_FS` | RSVP; low-stakes |

Same pattern: the action is read/create/modify *until it adds an external
recipient*, at which point it is egress.

## Gaps / follow-ups

- **External-recipient detection** for Drive shares and Calendar invites
  needs the RelationshipGroups registry (canonical recipient identity) to
  decide "external" vs "family/work" — ties to #51 (SourcePort canonical
  ids) and the cookbook P2.3 relationship rules.
- **Per-operation labels** are coarse today (blanket `confidential.personal`
  on the whole Gmail surface). Content-aware labeling is #34.
- **Drive/Calendar mappings are proposed, not shipped** — they land with
  the respective MCP server wiring; this doc is the spec they implement.
