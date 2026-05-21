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
    return (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "capabledeputy"
    )


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
        new_lines = (
            lines[:begin_idx]
            + new_block.rstrip("\n").split("\n")
            + lines[end_idx + 1 :]
        )
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
    tool_overrides:
      "imap.list_threads":
        capability_kind: READ_FS
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      "imap.read_message":
        capability_kind: READ_FS
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      "imap.search":
        capability_kind: READ_FS
        additional_labels: ["confidential.personal", "untrusted.user_input"]
      "imap.send":
        capability_kind: SEND_EMAIL
      "imap.list_folders":
        capability_kind: READ_FS
      "imap.mark_read":
        capability_kind: MODIFY_FS
      "imap.archive":
        capability_kind: MODIFY_FS
    strict: true
"""
