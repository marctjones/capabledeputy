"""Helpers for the user-local daemon config at
`~/.config/capabledeputy/daemon.yaml`.

Setup commands (`capdep imap-setup`, `capdep gworkspace-setup`) call
into here to register/de-register the upstream MCP server entry they
own. The file is line-edited (not YAML-roundtripped) so user-authored
content outside the managed markers is preserved verbatim across
re-registrations.

Layout:

    # capdep daemon config — managed by setup commands.
    # Edit freely outside the `# BEGIN/END capdep-managed:` blocks.

    upstream_servers:
      # BEGIN capdep-managed: imap
      - name: mail
        command: ["capdep", "mcp-server-imap"]
        ...
      # END capdep-managed: imap

      # ...user-authored entries here are preserved...

      # BEGIN capdep-managed: gworkspace
      - name: gworkspace
        ...
      # END capdep-managed: gworkspace
"""

from __future__ import annotations

import os
from pathlib import Path


def user_config_dir() -> Path:
    """XDG-aware config dir for capdep. Same resolution as the
    `imap-setup` / `gworkspace-setup` commands so the secrets and the
    daemon config live side-by-side."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "capabledeputy"


def user_default_daemon_config_path() -> Path:
    """Where the user-local default daemon config lives. May not exist
    yet — setup commands create it the first time they register."""
    return user_config_dir() / "daemon.yaml"


def resolve_daemon_config_with_source(
    explicit: str | None,
) -> tuple[Path | None, str]:
    """Pick the daemon config file the same way the daemon does, and
    also report which source provided it. `source` is one of:
      - `"explicit"`        : `--config` flag / explicit argument
      - `"env"`             : CAPDEP_CONFIG env var
      - `"user-default"`    : `~/.config/capabledeputy/daemon.yaml`
      - `"none"`            : no config found (bundled tools only)
    """
    if explicit:
        p = Path(explicit)
        return (p if p.is_file() else None, "explicit")
    env = os.environ.get("CAPDEP_CONFIG")
    if env:
        p = Path(env)
        return (p if p.is_file() else None, "env")
    p = user_default_daemon_config_path()
    if p.is_file():
        return (p, "user-default")
    return (None, "none")


IMAP_CONFIG_SECRET_PATH = "secrets/imap-config.yaml"


def imap_credentials_present() -> bool:
    """True iff `capdep imap-setup` has stashed IMAP credentials but
    the chat command can detect drift (creds exist, daemon config
    doesn't reference them) and emit a helpful pointer."""
    return (user_config_dir() / IMAP_CONFIG_SECRET_PATH).is_file()


_HEADER = """\
# capdep daemon config — managed by `capdep imap-setup` /
# `capdep gworkspace-setup`. Edit freely OUTSIDE the
# `# BEGIN/END capdep-managed:` markers — those blocks are
# regenerated on the next setup run.
#
# Used automatically by `capdep daemon start` and `capdep chat`
# when no --config / CAPDEP_CONFIG is set.

"""


def _begin_marker(block_id: str) -> str:
    return f"  # BEGIN capdep-managed: {block_id}"


def _end_marker(block_id: str) -> str:
    return f"  # END capdep-managed: {block_id}"


def has_managed_block(path: Path, block_id: str) -> bool:
    """Return True if `path` contains a managed block with the given id."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    return _begin_marker(block_id) in text and _end_marker(block_id) in text


def write_managed_block(
    path: Path,
    block_id: str,
    block_body: str,
) -> tuple[bool, bool]:
    """Insert or replace a managed block inside `path`.

    `block_body` is the YAML list-entry body — caller passes it raw,
    we wrap it with the BEGIN/END markers. Body lines should already
    be indented for placement under `upstream_servers:` (two spaces
    for the leading `- name: ...`, four spaces for nested fields).

    File creation: if `path` doesn't exist yet, writes a fresh file
    with the standard header + `upstream_servers:` skeleton + the
    block. Parent directory is created if missing.

    Idempotent: re-running with the same body is a no-op (returns
    `replaced=False`).

    Returns `(replaced, content_changed)` —
      replaced       : an existing block of the same id was overwritten
      content_changed: the file on disk differs after the call
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    begin = _begin_marker(block_id)
    end = _end_marker(block_id)
    body_normalized = block_body.rstrip("\n")
    new_block = f"{begin}\n{body_normalized}\n{end}\n"

    if not path.is_file():
        content = f"{_HEADER}upstream_servers:\n{new_block}"
        path.write_text(content, encoding="utf-8")
        return (False, True)

    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=False)

    # Find existing managed block by id
    begin_idx = next((i for i, ln in enumerate(lines) if ln.strip() == begin.strip()), -1)
    end_idx = next((i for i, ln in enumerate(lines) if ln.strip() == end.strip()), -1)

    if begin_idx >= 0 and end_idx >= 0 and end_idx > begin_idx:
        new_lines = lines[:begin_idx] + new_block.rstrip("\n").split("\n") + lines[end_idx + 1 :]
        new_content = "\n".join(new_lines)
        if not new_content.endswith("\n"):
            new_content += "\n"
        if new_content == original:
            return (True, False)
        path.write_text(new_content, encoding="utf-8")
        return (True, True)

    # No existing block — locate upstream_servers: or create one
    us_idx = next((i for i, ln in enumerate(lines) if ln.rstrip() == "upstream_servers:"), -1)
    if us_idx < 0:
        # Append a fresh upstream_servers: section with the block.
        suffix = "" if original.endswith("\n") else "\n"
        new_content = original + suffix + "\nupstream_servers:\n" + new_block
        path.write_text(new_content, encoding="utf-8")
        return (False, True)

    # Insert right after `upstream_servers:`. A blank line between
    # the header and the first managed block keeps the file readable.
    insert_at = us_idx + 1
    new_lines = lines[:insert_at] + new_block.rstrip("\n").split("\n") + lines[insert_at:]
    new_content = "\n".join(new_lines)
    if not new_content.endswith("\n"):
        new_content += "\n"
    path.write_text(new_content, encoding="utf-8")
    return (False, True)


def write_top_level_managed_block(
    path: Path,
    block_id: str,
    block_body: str,
) -> tuple[bool, bool]:
    """Insert or replace a managed block at the YAML top level (not
    under `upstream_servers:`). Used for the `sandbox:` block, whose
    body INCLUDES the top-level key.

    Marker format and idempotency are the same as `write_managed_block`,
    but the markers are at column zero (no two-space indent) so they
    don't appear to be part of any list.

    Returns `(replaced, content_changed)`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    begin = f"# BEGIN capdep-managed: {block_id}"
    end = f"# END capdep-managed: {block_id}"
    body_normalized = block_body.rstrip("\n")
    new_block = f"{begin}\n{body_normalized}\n{end}\n"

    if not path.is_file():
        # File doesn't exist yet — write header + this block alone.
        content = f"{_HEADER}{new_block}"
        path.write_text(content, encoding="utf-8")
        return (False, True)

    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=False)

    begin_idx = next((i for i, ln in enumerate(lines) if ln.strip() == begin), -1)
    end_idx = next((i for i, ln in enumerate(lines) if ln.strip() == end), -1)

    if begin_idx >= 0 and end_idx >= 0 and end_idx > begin_idx:
        new_lines = lines[:begin_idx] + new_block.rstrip("\n").split("\n") + lines[end_idx + 1 :]
        new_content = "\n".join(new_lines)
        if not new_content.endswith("\n"):
            new_content += "\n"
        if new_content == original:
            return (True, False)
        path.write_text(new_content, encoding="utf-8")
        return (True, True)

    # No existing block — append at end, with a blank line separator.
    suffix = "" if original.endswith("\n") else "\n"
    new_content = original + suffix + "\n" + new_block
    path.write_text(new_content, encoding="utf-8")
    return (False, True)


def register_default_assistant_surface(
    path: Path,
    *,
    include_sandbox: bool | None = None,
) -> list[str]:
    """Write/refresh all the bundled MCP server managed blocks (fs,
    memory, git, fetch, search) in `path`. If `include_sandbox` is
    True, also writes the sandbox block; if False, skips it; if None
    (default), the sandbox block is included iff `podman --version`
    succeeds. Returns the list of human-readable status messages —
    callers print them to the operator.
    """
    messages: list[str] = []
    for block_id, body in DEFAULT_ASSISTANT_BUNDLED_BLOCKS:
        replaced, changed = write_managed_block(path, block_id, body)
        if changed and replaced:
            messages.append(f"refreshed {block_id}")
        elif changed:
            messages.append(f"registered {block_id}")
        else:
            messages.append(f"{block_id} already up to date")

    if include_sandbox is None:
        include_sandbox = podman_available()
    if include_sandbox:
        replaced, changed = write_top_level_managed_block(
            path,
            SANDBOX_BLOCK_ID,
            SANDBOX_BLOCK_BODY,
        )
        if changed and replaced:
            messages.append("refreshed sandbox (podman, scratch region)")
        elif changed:
            messages.append("registered sandbox (podman, scratch region)")
        else:
            messages.append("sandbox already up to date")
    else:
        messages.append("sandbox skipped (podman not detected)")

    return messages


def remove_managed_block(path: Path, block_id: str) -> bool:
    """Strip the managed block with this id from `path`. Returns True
    if a block was removed, False if nothing to do (file missing or
    block absent). Surrounding content is preserved verbatim."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    begin = _begin_marker(block_id).strip()
    end = _end_marker(block_id).strip()
    begin_idx = next((i for i, ln in enumerate(lines) if ln.strip() == begin), -1)
    end_idx = next((i for i, ln in enumerate(lines) if ln.strip() == end), -1)
    if begin_idx < 0 or end_idx < 0 or end_idx <= begin_idx:
        return False
    new_lines = lines[:begin_idx] + lines[end_idx + 1 :]
    new_content = "\n".join(new_lines)
    if new_content and not new_content.endswith("\n"):
        new_content += "\n"
    path.write_text(new_content, encoding="utf-8")
    return True


IMAP_BLOCK_ID = "imap"

IMAP_BLOCK_BODY = """\
  - name: mail
    command: ["capdep", "mcp-server-imap"]
    inherent_labels: []
    # OUTBOUND MAIL DISABLED (operator policy): refuse any SEND_EMAIL tool
    # (imap.send), so a re-run of `capdep imap-setup` can never enable
    # sending. Reading/organizing mail stays enabled. Remove to allow.
    disabled_kinds: ["SEND_EMAIL"]
    tool_overrides:
      # Issue #33 — IMAP read tools use IMAP_READ kind, not READ_FS.
      # Operators with legacy `/grant READ_FS *` keep working
      # (back-compat union); new grants should be `IMAP_READ *`.
      "imap.list_threads":
        capability_kind: IMAP_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      "imap.read_message":
        capability_kind: IMAP_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      "imap.search":
        capability_kind: IMAP_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      "imap.send":
        capability_kind: SEND_EMAIL
      "imap.list_folders":
        capability_kind: IMAP_READ
      "imap.mark_read":
        capability_kind: MODIFY_FS
      "imap.archive":
        capability_kind: MODIFY_FS
    strict: true
"""


# ---- bundled MCP server blocks (no external deps, ship with capdep) ----

BUNDLED_FS_BLOCK_ID = "bundled-fs"
BUNDLED_FS_BLOCK_BODY = """\
  - name: bundled-fs
    command: ["capdep", "mcp-server-fs"]
    inherent_labels: []
    tool_overrides:
      "fs.read":
        capability_kind: READ_FS
      "fs.list":
        capability_kind: READ_FS
      "fs.create":
        capability_kind: CREATE_FS
      "fs.write":
        capability_kind: WRITE_FS
      "fs.delete":
        capability_kind: DELETE_FS
    strict: true
"""

BUNDLED_FETCH_BLOCK_ID = "bundled-fetch"
BUNDLED_FETCH_BLOCK_BODY = """\
  - name: bundled-fetch
    command: ["capdep", "mcp-server-fetch"]
    inherent_labels: ["untrusted.external"]
    tool_overrides:
      "fetch.get":
        capability_kind: WEB_FETCH
    strict: true
"""

BUNDLED_SEARCH_BLOCK_ID = "bundled-search"
BUNDLED_SEARCH_BLOCK_BODY = """\
  - name: bundled-search
    command: ["capdep", "mcp-server-search"]
    inherent_labels: ["untrusted.external"]
    tool_overrides:
      "search.web":
        capability_kind: WEB_FETCH
    strict: true
"""

BUNDLED_MEMORY_BLOCK_ID = "bundled-memory"
BUNDLED_MEMORY_BLOCK_BODY = """\
  - name: bundled-memory
    command: ["capdep", "mcp-server-memory"]
    inherent_labels: []
    tool_overrides:
      "memory.create":
        capability_kind: CREATE_FS
      "memory.read":
        capability_kind: READ_FS
      "memory.update":
        capability_kind: WRITE_FS
      "memory.delete":
        capability_kind: DELETE_FS
      "memory.list":
        capability_kind: READ_FS
    strict: true
"""

BUNDLED_GIT_BLOCK_ID = "bundled-git"
BUNDLED_GIT_BLOCK_BODY = """\
  - name: bundled-git
    command: ["capdep", "mcp-server-git"]
    inherent_labels: []
    tool_overrides:
      "git.status":
        capability_kind: READ_FS
      "git.log":
        capability_kind: READ_FS
      "git.diff":
        capability_kind: READ_FS
      "git.show":
        capability_kind: READ_FS
      "git.branch_list":
        capability_kind: READ_FS
    strict: true
"""


# All bundled blocks grouped for `register_default_assistant_surface`.
# Order is the order they appear in the managed file, with the
# safer/more-bounded ones first.
DEFAULT_ASSISTANT_BUNDLED_BLOCKS: tuple[tuple[str, str], ...] = (
    (BUNDLED_FS_BLOCK_ID, BUNDLED_FS_BLOCK_BODY),
    (BUNDLED_MEMORY_BLOCK_ID, BUNDLED_MEMORY_BLOCK_BODY),
    (BUNDLED_GIT_BLOCK_ID, BUNDLED_GIT_BLOCK_BODY),
    (BUNDLED_FETCH_BLOCK_ID, BUNDLED_FETCH_BLOCK_BODY),
    (BUNDLED_SEARCH_BLOCK_ID, BUNDLED_SEARCH_BLOCK_BODY),
)


# ---- Google Workspace via official remote MCP servers ----
#
# Google's official Workspace MCP servers are remote HTTP endpoints,
# one per product. CapDep authenticates them with its native OAuth2
# browser/PKCE flow and then applies its own policy gates around every
# discovered MCP tool.

GWORKSPACE_BLOCK_ID = "gworkspace"
GWORKSPACE_DEFAULT_OFFICIAL_SERVICES = "gmail,drive,calendar,chat,people"

_GWORKSPACE_OFFICIAL_BLOCKS: dict[str, str] = {
    "gmail": """\
  - name: google-gmail
    transport: streamable_http
    url: "https://gmailmcp.googleapis.com/mcp/v1"
    auth:
      type: oauth2
      client_id_env: GOOGLE_MCP_CLIENT_ID
      client_secret_env: GOOGLE_MCP_CLIENT_SECRET
      authorization_url: "https://accounts.google.com/o/oauth2/v2/auth"
      token_url: "https://oauth2.googleapis.com/token"
      scopes:
        - "https://www.googleapis.com/auth/gmail.readonly"
        - "https://www.googleapis.com/auth/gmail.compose"
      extra_authorize_params:
        access_type: offline
        prompt: consent
    inherent_labels: ["confidential.personal", "untrusted.user_input"]
    disabled_kinds: ["SEND_EMAIL"]
    tool_overrides:
      create_draft:
        capability_kind: GMAIL_DRAFT
        additional_labels: ["confidential.personal"]
        target_arg: to
      create_label:
        capability_kind: MODIFY_FS
        additional_labels: ["confidential.personal"]
      get_thread:
        capability_kind: GMAIL_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      label_message:
        capability_kind: MODIFY_FS
        additional_labels: ["confidential.personal"]
      label_thread:
        capability_kind: MODIFY_FS
        additional_labels: ["confidential.personal"]
      list_drafts:
        capability_kind: GMAIL_READ
        additional_labels: ["confidential.personal"]
      list_labels:
        capability_kind: GMAIL_READ
        additional_labels: ["confidential.personal"]
      search_threads:
        capability_kind: GMAIL_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      unlabel_message:
        capability_kind: MODIFY_FS
        additional_labels: ["confidential.personal"]
      unlabel_thread:
        capability_kind: MODIFY_FS
        additional_labels: ["confidential.personal"]
    strict: true
""",
    "drive": """\
  - name: google-drive
    transport: streamable_http
    url: "https://drivemcp.googleapis.com/mcp/v1"
    auth:
      type: oauth2
      client_id_env: GOOGLE_MCP_CLIENT_ID
      client_secret_env: GOOGLE_MCP_CLIENT_SECRET
      authorization_url: "https://accounts.google.com/o/oauth2/v2/auth"
      token_url: "https://oauth2.googleapis.com/token"
      scopes:
        - "https://www.googleapis.com/auth/drive.readonly"
        - "https://www.googleapis.com/auth/drive.file"
      extra_authorize_params:
        access_type: offline
        prompt: consent
    inherent_labels: ["confidential.personal", "untrusted.user_input"]
    tool_overrides:
      copy_file:
        capability_kind: CREATE_FS
        additional_labels: ["confidential.personal"]
      create_file:
        capability_kind: CREATE_FS
        additional_labels: ["confidential.personal"]
      download_file_content:
        capability_kind: DRIVE_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      get_file_metadata:
        capability_kind: DRIVE_READ
        additional_labels: ["confidential.personal"]
      get_file_permissions:
        capability_kind: DRIVE_READ
        additional_labels: ["confidential.personal"]
      list_recent_files:
        capability_kind: DRIVE_READ
        additional_labels: ["confidential.personal"]
      read_file_content:
        capability_kind: DRIVE_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      search_files:
        capability_kind: DRIVE_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
    strict: true
""",
    "calendar": """\
  - name: google-calendar
    transport: streamable_http
    url: "https://calendarmcp.googleapis.com/mcp/v1"
    auth:
      type: oauth2
      client_id_env: GOOGLE_MCP_CLIENT_ID
      client_secret_env: GOOGLE_MCP_CLIENT_SECRET
      authorization_url: "https://accounts.google.com/o/oauth2/v2/auth"
      token_url: "https://oauth2.googleapis.com/token"
      scopes:
        - "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
        - "https://www.googleapis.com/auth/calendar.events.freebusy"
        - "https://www.googleapis.com/auth/calendar.events.readonly"
      extra_authorize_params:
        access_type: offline
        prompt: consent
    inherent_labels: ["confidential.personal", "untrusted.user_input"]
    tool_overrides:
      create_event:
        capability_kind: CREATE_CAL
        additional_labels: ["confidential.personal"]
        target_template: "gcal://calendar/{calendar_id}/events/attendees/{attendees}"
      delete_event:
        capability_kind: DELETE_CAL
        additional_labels: ["confidential.personal"]
        target_template: "gcal://calendar/{calendar_id}/event/{event_id}"
      get_event:
        capability_kind: CALENDAR_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      list_calendars:
        capability_kind: CALENDAR_READ
        additional_labels: ["confidential.personal"]
      list_events:
        capability_kind: CALENDAR_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      respond_to_event:
        capability_kind: MODIFY_CAL
        additional_labels: ["confidential.personal"]
        target_template: "gcal://calendar/{calendar_id}/event/{event_id}"
      suggest_time:
        capability_kind: CALENDAR_READ
        additional_labels: ["confidential.personal"]
      update_event:
        capability_kind: MODIFY_CAL
        additional_labels: ["confidential.personal"]
        target_template: "gcal://calendar/{calendar_id}/event/{event_id}/attendees/{attendees}"
    strict: true
""",
    "chat": """\
  - name: google-chat
    transport: streamable_http
    url: "https://chatmcp.googleapis.com/mcp/v1"
    auth:
      type: oauth2
      client_id_env: GOOGLE_MCP_CLIENT_ID
      client_secret_env: GOOGLE_MCP_CLIENT_SECRET
      authorization_url: "https://accounts.google.com/o/oauth2/v2/auth"
      token_url: "https://oauth2.googleapis.com/token"
      scopes:
        - "https://www.googleapis.com/auth/chat.spaces.readonly"
        - "https://www.googleapis.com/auth/chat.memberships.readonly"
        - "https://www.googleapis.com/auth/chat.messages.readonly"
        - "https://www.googleapis.com/auth/chat.messages.create"
        - "https://www.googleapis.com/auth/chat.users.readstate.readonly"
      extra_authorize_params:
        access_type: offline
        prompt: consent
    inherent_labels: ["confidential.personal", "untrusted.user_input"]
    tool_overrides:
      list_messages:
        capability_kind: CHAT_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      search_conversations:
        capability_kind: CHAT_READ
        additional_labels: ["confidential.personal"]
      search_messages:
        capability_kind: CHAT_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      send_message:
        capability_kind: SEND_MESSAGE
        additional_labels: ["confidential.personal"]
    strict: true
""",
    "people": """\
  - name: google-people
    transport: streamable_http
    url: "https://people.googleapis.com/mcp/v1"
    auth:
      type: oauth2
      client_id_env: GOOGLE_MCP_CLIENT_ID
      client_secret_env: GOOGLE_MCP_CLIENT_SECRET
      authorization_url: "https://accounts.google.com/o/oauth2/v2/auth"
      token_url: "https://oauth2.googleapis.com/token"
      scopes:
        - "https://www.googleapis.com/auth/directory.readonly"
        - "https://www.googleapis.com/auth/userinfo.profile"
        - "https://www.googleapis.com/auth/contacts.readonly"
      extra_authorize_params:
        access_type: offline
        prompt: consent
    inherent_labels: ["confidential.personal"]
    tool_overrides:
      get_user_profile:
        capability_kind: PEOPLE_READ
        additional_labels: ["confidential.personal"]
      search_contacts:
        capability_kind: PEOPLE_READ
        additional_labels: ["confidential.personal"]
      search_directory_people:
        capability_kind: PEOPLE_READ
        additional_labels: ["confidential.personal"]
    strict: true
""",
}


def google_workspace_official_block_body(
    services: str = GWORKSPACE_DEFAULT_OFFICIAL_SERVICES,
) -> str:
    """Build the official Google Workspace managed block for selected services."""
    requested = [s.strip().lower() for s in services.split(",") if s.strip()]
    unknown = [s for s in requested if s not in _GWORKSPACE_OFFICIAL_BLOCKS]
    if unknown:
        raise ValueError(
            "unknown official Google Workspace MCP service(s): "
            + ", ".join(unknown)
            + ". Expected any of: "
            + ", ".join(_GWORKSPACE_OFFICIAL_BLOCKS),
        )
    return "\n".join(_GWORKSPACE_OFFICIAL_BLOCKS[s].rstrip("\n") for s in requested) + "\n"


GWORKSPACE_BLOCK_BODY = google_workspace_official_block_body()


# ---- Legacy Google Workspace via `gws-mcp-server` community wrapper ----
#
# Kept for operators who already authenticated through `gws auth login` or
# need Docs/Sheets tools that are not in the official remote MCP preview.

GWORKSPACE_COMMUNITY_BLOCK_BODY = """\
  - name: gws
    command: ["npx", "gws-mcp-server", "--services", "drive,sheets,calendar,docs,gmail"]
    inherent_labels: ["confidential.personal"]
    disabled_kinds: ["SEND_EMAIL"]
    tool_overrides:
      drive_delete_file:
        capability_kind: DELETE_FS
        additional_labels: ["confidential.personal"]
      drive_update_file:
        capability_kind: MODIFY_FS
        additional_labels: ["confidential.personal"]
      drive_create_file: {capability_kind: CREATE_FS}
      drive_copy_file: {capability_kind: CREATE_FS}
      drive_share_file:
        capability_kind: MODIFY_FS
        additional_labels: ["confidential.personal"]
      calendar_delete_event:
        capability_kind: DELETE_CAL
        additional_labels: ["confidential.personal"]
      calendar_update_event:
        capability_kind: MODIFY_CAL
        additional_labels: ["confidential.personal"]
      calendar_insert_event: {capability_kind: CREATE_CAL}
      sheets_write_values:
        capability_kind: MODIFY_FS
        additional_labels: ["confidential.personal"]
      sheets_append_values:
        capability_kind: MODIFY_FS
        additional_labels: ["confidential.personal"]
      docs_batch_update:
        capability_kind: MODIFY_FS
        additional_labels: ["confidential.personal"]
      docs_create_document: {capability_kind: CREATE_FS}
      gmail_messages_list:
        capability_kind: GMAIL_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      gmail_messages_get:
        capability_kind: GMAIL_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      gmail_threads_list:
        capability_kind: GMAIL_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      gmail_threads_get:
        capability_kind: GMAIL_READ
        additional_labels: ["confidential.personal", "untrusted.user_input"]
    strict: false
"""


def gws_cli_available() -> bool:
    """True iff the `gws` binary is on PATH. Used by the setup command
    to refuse a register-only flow before the user has installed the
    Workspace CLI."""
    import shutil
    import subprocess

    bin_path = shutil.which("gws")
    if bin_path is None:
        return False
    try:
        result = subprocess.run(
            [bin_path, "--version"],
            capture_output=True,
            timeout=3,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def gws_mcp_server_available() -> bool:
    """True iff `gws-mcp-server` is installed (npm global or accessible
    via `npx`). We check for the binary directly; if it's not there,
    `npx` will fetch it on first run, but warning the operator up
    front gives them a cleaner story."""
    import shutil

    return shutil.which("gws-mcp-server") is not None


# ---- top-level `sandbox:` block ----
# This is NOT inside `upstream_servers:` — it's a peer YAML key. The
# managed-block helper handles it via the `top_level=True` mode below.

SANDBOX_BLOCK_ID = "sandbox"
SANDBOX_BLOCK_BODY = """\
sandbox:
  provider: podman
  regions:
    - id: scratch
      image: docker.io/library/alpine:latest
      network: none
      memory_mb: 512
      cpus: 1.0
      pids_limit: 128
      timeout_seconds_default: 30
      auto_io_mounts: true
"""


def podman_available() -> bool:
    """True iff the `podman` CLI is on PATH and `--version` exits zero.
    Used by setup commands to decide whether to write the sandbox
    managed block. Cached per-process; not aggressive enough about
    detection that we'd want to retry."""
    import shutil
    import subprocess

    bin_path = shutil.which("podman")
    if bin_path is None:
        return False
    try:
        result = subprocess.run(
            [bin_path, "--version"],
            capture_output=True,
            timeout=3,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
