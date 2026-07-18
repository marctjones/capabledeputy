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

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
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


def _capdep_executable() -> str | None:
    capdep = shutil.which("capdep")
    if capdep is None and sys.argv:
        candidate = Path(sys.argv[0]).resolve()
        if candidate.is_file():
            capdep = str(candidate)
    return capdep


def _project_venv_bin() -> Path | None:
    capdep = _capdep_executable()
    if capdep:
        bin_dir = Path(capdep).resolve().parent
        if bin_dir.name == "bin" and bin_dir.parent.name == ".venv":
            return bin_dir
    executable = Path(sys.executable).resolve()
    bin_dir = executable.parent
    if bin_dir.name == "bin" and bin_dir.parent.name == ".venv":
        return bin_dir
    cwd_venv_bin = Path.cwd() / ".venv" / "bin"
    return cwd_venv_bin if cwd_venv_bin.is_dir() else None


def uvx_spawn_command() -> list[str]:
    """Resolve uvx through the project dev environment.

    CapDep standardizes Python tooling on uv with a repo-local `.venv`.
    GUI/launchd/tmux daemon launches may not inherit an operator shell PATH,
    so upstream configs can keep portable `["uvx", ...]` commands while the
    daemon resolves them to `.venv/bin/uvx` when available.
    """
    venv_bin = _project_venv_bin()
    if venv_bin is not None:
        uvx = venv_bin / "uvx"
        if uvx.is_file():
            return [str(uvx)]
    uvx = shutil.which("uvx")
    if uvx:
        return [uvx]
    return ["uvx"]


def capdep_spawn_command(mcp_subcommand: str) -> list[str]:
    """Resolve the capdep CLI used to spawn bundled MCP subprocesses.

    The daemon often starts without the operator's venv on PATH, so
    managed blocks must record an absolute executable when possible.
    """
    capdep = _capdep_executable()
    if capdep:
        return [capdep, mcp_subcommand]
    return [sys.executable, "-m", "capabledeputy.cli.main", mcp_subcommand]


def image_generate_mcp_module() -> str:
    return "capabledeputy.mcp_servers.image_generate"


def image_fetch_mcp_module() -> str:
    return "capabledeputy.mcp_servers.image_fetch"


def images_mcp_module() -> str:
    return "capabledeputy.mcp_servers.images"


def _images_venv_python() -> Path | None:
    capdep = _capdep_executable()
    if not capdep:
        return None
    bin_dir = Path(capdep).resolve().parent
    venv_dir = bin_dir.parent
    repo_root = venv_dir.parent if venv_dir.name == ".venv" else bin_dir.parent
    venv_python = repo_root / ".venv-images" / "bin" / "python"
    return venv_python if venv_python.is_file() else None


def image_generate_spawn_command(mcp_subcommand: str = "mcp-server-image-generate") -> list[str]:
    """Python for image generation — isolated venv with torch/diffusers."""
    _ = mcp_subcommand
    module = image_generate_mcp_module()
    explicit = os.environ.get("CAPDEP_IMAGE_PYTHON", "").strip()
    if explicit:
        return [explicit, "-m", module]
    venv_python = _images_venv_python()
    if venv_python is not None:
        return [str(venv_python), "-m", module]
    return [sys.executable, "-m", module]


def image_fetch_spawn_command(mcp_subcommand: str = "mcp-server-image-fetch") -> list[str]:
    """Python for image fetch — lightweight; uses main CapDep venv by default."""
    _ = mcp_subcommand
    module = image_fetch_mcp_module()
    explicit = os.environ.get("CAPDEP_IMAGE_FETCH_PYTHON", "").strip()
    if explicit:
        return [explicit, "-m", module]
    capdep = _capdep_executable()
    if capdep:
        return [capdep, "mcp-server-image-fetch"]
    return [sys.executable, "-m", module]


def images_spawn_command(mcp_subcommand: str = "mcp-server-images") -> list[str]:
    """Legacy combined images MCP (generate + fetch in one process)."""
    _ = mcp_subcommand
    module = images_mcp_module()
    explicit = os.environ.get("CAPDEP_IMAGE_PYTHON", "").strip()
    if explicit:
        return [explicit, "-m", module]
    venv_python = _images_venv_python()
    if venv_python is not None:
        return [str(venv_python), "-m", module]
    return [sys.executable, "-m", module]


def resolve_upstream_spawn_command(command: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Resolve placeholder spawn commands at daemon runtime."""
    if not command:
        return ()
    head = command[0]
    if head == "capdep-image-generate":
        sub = command[1] if len(command) > 1 else "mcp-server-image-generate"
        return tuple(image_generate_spawn_command(sub))
    if head == "capdep-image-fetch":
        sub = command[1] if len(command) > 1 else "mcp-server-image-fetch"
        return tuple(image_fetch_spawn_command(sub))
    if head == "capdep-images":
        sub = command[1] if len(command) > 1 else "mcp-server-images"
        return tuple(images_spawn_command(sub))
    if head == "capdep" and len(command) > 1:
        return tuple(capdep_spawn_command(command[1]))
    if head == "uvx":
        return tuple(uvx_spawn_command() + list(command[1:]))
    return tuple(command)


def materialize_capdep_commands(block_body: str) -> str:
    """Replace placeholder spawn commands with resolved absolute paths."""

    def _replace_capdep(match: re.Match[str]) -> str:
        return json.dumps(capdep_spawn_command(match.group(1)))

    def _replace_image_generate(match: re.Match[str]) -> str:
        return json.dumps(image_generate_spawn_command(match.group(1)))

    def _replace_image_fetch(match: re.Match[str]) -> str:
        return json.dumps(image_fetch_spawn_command(match.group(1)))

    def _replace_images(match: re.Match[str]) -> str:
        return json.dumps(images_spawn_command(match.group(1)))

    body = re.sub(r'\["capdep", "([^"]+)"\]', _replace_capdep, block_body)
    body = re.sub(
        r'\["capdep-image-generate", "([^"]+)"\]',
        _replace_image_generate,
        body,
    )
    body = re.sub(
        r'\["capdep-image-fetch", "([^"]+)"\]',
        _replace_image_fetch,
        body,
    )
    return re.sub(r'\["capdep-images", "([^"]+)"\]', _replace_images, body)


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
    body_normalized = materialize_capdep_commands(block_body.rstrip("\n"))
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
        # #361 — use the deep readiness check, not the shallow `--version` one:
        # writing the sandbox block when the machine is DOWN produces the
        # "block present but SEALED fails at runtime" trap. Same source of truth
        # the `capdep-setup sandbox` step consults, so the two never disagree.
        include_sandbox = podman_ready()
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
        messages.append("sandbox skipped (podman not ready — install + start the machine)")

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
      "wikipedia.lookup":
        capability_kind: WEB_FETCH
        target_template: "wikipedia://{title}"
    strict: true
"""

BUNDLED_IMAGE_GENERATE_BLOCK_ID = "bundled-image-generate"
BUNDLED_IMAGE_GENERATE_BLOCK_BODY = """\
  - name: bundled-image-generate
    command: ["capdep-image-generate", "mcp-server-image-generate"]
    inherent_labels: []
    env:
      CAPDEP_IMAGE_PROFILE: "default"
      CAPDEP_IMAGE_BACKEND: "auto"
      CAPDEP_IMAGE_MODEL: "z-image-turbo"
      CAPDEP_IMAGE_MODEL_PATH: "filipstrand/Z-Image-Turbo-mflux-4bit"
      CAPDEP_IMAGE_QUANTIZE: "8"
      CAPDEP_IMAGE_PROMPT_FILTER: "off"
      CAPDEP_IMAGE_SAFETY: "on"
      CAPDEP_IMAGE_STYLE: "photoreal"
      CAPDEP_IMAGE_DEVICE: "auto"
      CAPDEP_IMAGE_WIDTH: "768"
      CAPDEP_IMAGE_HEIGHT: "768"
      CAPDEP_IMAGE_STEPS: "9"
      # On Apple Silicon, auto requires MFLUX with MLX/Metal and fails
      # closed instead of falling back to CPU/Torch/Diffusers.
      # Profiles: default, quality-flux2, quality-qwen,
      #   flux-nsfw, flux2-nsfw, sdxl-nsfw, pony-nsfw.
      # Flux profiles use CAPDEP_IMAGE_LORAS / CAPDEP_IMAGE_LORA_SCALES.
      # SDXL/Pony profiles use the checkpoint env overrides below.
      # CAPDEP_IMAGE_CHECKPOINT_PATH: "/path/to/photoreal-or-sdxl.safetensors"
      # CAPDEP_IMAGE_GRAPHIC_NOVEL_CHECKPOINT_PATH: "/path/to/pony-or-illustrious.safetensors"
    tool_overrides:
      "image.generate":
        capability_kind: GENERATE_IMAGE
        target_template: "*"
    strict: true
"""

BUNDLED_IMAGE_FETCH_BLOCK_ID = "bundled-image-fetch"
BUNDLED_IMAGE_FETCH_BLOCK_BODY = """\
  - name: bundled-image-fetch
    command: ["capdep", "mcp-server-image-fetch"]
    inherent_labels: ["untrusted.external"]
    env:
      CAPDEP_IMAGE_OUTPUT_DIR: "~/.capdep/work/images"
    tool_overrides:
      "image.fetch":
        capability_kind: FETCH_IMAGE
        target_arg: url
    strict: true
"""

# Legacy combined server (generate + fetch). Kept for reference / manual overrides.
BUNDLED_IMAGES_BLOCK_ID = "bundled-images"
BUNDLED_IMAGES_BLOCK_BODY = """\
  - name: bundled-images
    command: ["capdep-images", "mcp-server-images"]
    inherent_labels: []
    env:
      CAPDEP_IMAGE_PROFILE: "default"
      CAPDEP_IMAGE_BACKEND: "auto"
      CAPDEP_IMAGE_MODEL: "z-image-turbo"
      CAPDEP_IMAGE_MODEL_PATH: "filipstrand/Z-Image-Turbo-mflux-4bit"
      CAPDEP_IMAGE_QUANTIZE: "8"
      CAPDEP_IMAGE_PROMPT_FILTER: "off"
      CAPDEP_IMAGE_SAFETY: "on"
      CAPDEP_IMAGE_STYLE: "photoreal"
      CAPDEP_IMAGE_DEVICE: "auto"
      CAPDEP_IMAGE_WIDTH: "768"
      CAPDEP_IMAGE_HEIGHT: "768"
      CAPDEP_IMAGE_STEPS: "9"
      # On Apple Silicon, auto requires MFLUX with MLX/Metal and fails
      # closed instead of falling back to CPU/Torch/Diffusers.
      # Profiles: default, quality-flux2, quality-qwen,
      #   flux-nsfw, flux2-nsfw, sdxl-nsfw, pony-nsfw.
      # Flux profiles use CAPDEP_IMAGE_LORAS / CAPDEP_IMAGE_LORA_SCALES.
      # SDXL/Pony profiles use the checkpoint env overrides below.
      # CAPDEP_IMAGE_CHECKPOINT_PATH: "/path/to/photoreal-or-sdxl.safetensors"
      # CAPDEP_IMAGE_GRAPHIC_NOVEL_CHECKPOINT_PATH: "/path/to/pony-or-illustrious.safetensors"
    tool_overrides:
      "image.generate":
        capability_kind: GENERATE_IMAGE
        target_template: "*"
      "image.fetch":
        capability_kind: FETCH_IMAGE
        target_arg: url
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
        target_arg: query
    strict: true
"""

KAGI_BLOCK_ID = "kagi"
KAGI_BLOCK_BODY = """\
  - name: kagi
    command: ["uvx", "kagimcp"]
    env:
      KAGI_API_KEY: "${KAGI_API_KEY}"
    strict: true
    inherent_labels: ["untrusted.external"]
    tool_overrides:
      kagi_search_fetch:
        capability_kind: WEB_FETCH
        target_arg: query
      kagi_extract:
        capability_kind: WEB_FETCH
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
    (BUNDLED_IMAGE_FETCH_BLOCK_ID, BUNDLED_IMAGE_FETCH_BLOCK_BODY),
    (BUNDLED_IMAGE_GENERATE_BLOCK_ID, BUNDLED_IMAGE_GENERATE_BLOCK_BODY),
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


def podman_readiness(
    command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
) -> tuple[str, str]:
    """Canonical Podman readiness for Pattern 5 (SEALED):
    ('ready' | 'machine_not_running' | 'not_installed', detail).

    `podman --version` succeeds even when the machine/VM is not running (macOS),
    so it is not enough to know SEALED will actually work — `podman info` is the
    real check (it fails when the machine is down or the service is unreachable).
    Uses a non-checking runner so a nonzero exit is observed, not raised.

    This is the single source of truth: both the `capdep-setup sandbox` step and
    the `assistant-surface` auto-detect consult it, so they can never disagree
    about whether the sandbox block should be written. It lives here (the
    lower-level module) because setup_domains imports from _managed_config, not
    the reverse.
    """

    def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if command_runner is not None:
            return command_runner(cmd)
        return subprocess.run(list(cmd), check=False, text=True, capture_output=True, timeout=5)

    if command_runner is None and shutil.which("podman") is None:
        return "not_installed", "podman CLI not found on PATH"
    exe = shutil.which("podman") or "podman"
    try:
        version = _run([exe, "--version"])
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "not_installed", "podman --version failed to run"
    if version.returncode != 0:
        return "not_installed", "podman --version returned nonzero"
    try:
        info = _run([exe, "info"])
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "machine_not_running", "podman info failed to run"
    if info.returncode != 0:
        return "machine_not_running", "podman info returned nonzero (machine likely not running)"
    return "ready", (getattr(version, "stdout", "") or "").strip()


def podman_ready(
    command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
) -> bool:
    """True iff Podman is installed AND its machine is up (`podman info` succeeds)
    — i.e. SEALED is genuinely reachable. The gate for writing the sandbox block."""
    return podman_readiness(command_runner)[0] == "ready"


def podman_available() -> bool:
    """True iff the `podman` CLI is on PATH and `--version` exits zero — i.e.
    Podman is *installed*. NB: this is a weaker signal than `podman_ready()`; on
    macOS `--version` succeeds even when the machine is down, so this must NOT be
    used to decide whether SEALED is reachable (use `podman_ready()` for that).
    Retained for callers that only need the installed-check."""
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
