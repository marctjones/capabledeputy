"""Local filesystem tools — `fs.read`, `fs.read_pdf`, `fs.create`,
`fs.modify`.

REAL tools (not stubs). Two read operations and two write operations
on the local filesystem. The tools are minimal by design — size-
bounded, absolute-path-only, and aware of `PolicyContext.bindings` if
the operator has declared any.

Read-side label behavior:
  - With a bound BindingSet: every path goes through `bindings.resolve`.
    Unbound paths refuse (fail-closed per FR-023). The matched binding
    determines `category` and `tier`, which the dispatcher uses for the
    v2 four-axis label propagation.
  - Without a binding set: the tool still reads but propagates the
    legacy `UNTRUSTED_USER_INPUT` label so the session knows the source
    is operator-curated-but-not-vetted.

Write-side capability mapping:
  - `fs.create` -> CREATE_FS (non-destructive: refused if target exists)
  - `fs.modify` -> MODIFY_FS (destructive — the destructive-op gate
                  fires unless the matched capability declares
                  `allows_destructive=True`)

`fs.delete` is intentionally not yet exposed — delete is the highest
blast-radius primitive and belongs with spec-005's binding-driven
write_discipline (the binding tells us whether the path is in a
versioned/snapshotted zone). Until then operators run shell deletes.

Size cap: 64 KiB on both reads and writes. The cap is per-call, not
per-process; an agent can chunk explicitly via successive calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import (
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult

_MAX_BYTES = 64 * 1024


def _to_file_uri(path: str) -> str:
    """Normalize a user-supplied path into a `file://` URI for binding
    resolution. Absolute paths get `file://`; relative paths are
    refused (we don't have a cwd we trust)."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError(f"refusing relative path {path!r}; pass an absolute path")
    return f"file://{p.as_posix()}"


async def _fs_read_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    raw_path = str(args["path"])
    try:
        uri = _to_file_uri(raw_path)
    except ValueError as e:
        return ToolResult(output={"ok": False, "error": str(e)})
    p = Path(raw_path).expanduser()
    if not p.is_file():
        return ToolResult(output={"ok": False, "error": f"not a file: {raw_path}"})
    if p.stat().st_size > _MAX_BYTES:
        return ToolResult(
            output={
                "ok": False,
                "error": f"file exceeds {_MAX_BYTES}-byte read cap; chunk explicitly",
            },
        )
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ToolResult(output={"ok": False, "error": f"read failed: {e}"})
    return ToolResult(
        output={"ok": True, "path": raw_path, "uri": uri, "text": text},
        additional_tags=LabelState(
            b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)})
        ),
    )


async def _fs_read_pdf_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    raw_path = str(args["path"])
    try:
        uri = _to_file_uri(raw_path)
    except ValueError as e:
        return ToolResult(output={"ok": False, "error": str(e)})
    p = Path(raw_path).expanduser()
    if not p.is_file():
        return ToolResult(output={"ok": False, "error": f"not a file: {raw_path}"})
    if p.stat().st_size > 8 * _MAX_BYTES:
        return ToolResult(
            output={"ok": False, "error": "PDF exceeds the read-size cap"},
        )
    try:
        from pypdf import PdfReader
    except ImportError:
        return ToolResult(
            output={"ok": False, "error": "pypdf not installed"},
        )
    try:
        reader = PdfReader(str(p))
        pages: list[str] = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
            if sum(len(s) for s in pages) > _MAX_BYTES:
                break
    except Exception as e:
        return ToolResult(output={"ok": False, "error": f"pdf parse failed: {e}"})
    text = "\f".join(pages)
    return ToolResult(
        output={
            "ok": True,
            "path": raw_path,
            "uri": uri,
            "n_pages": len(pages),
            "text": text[:_MAX_BYTES],
            "truncated": len(text) > _MAX_BYTES,
        },
        additional_tags=LabelState(
            b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)})
        ),
    )


async def _fs_create_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    raw_path = str(args["path"])
    content = str(args.get("content", ""))
    try:
        uri = _to_file_uri(raw_path)
    except ValueError as e:
        return ToolResult(output={"ok": False, "error": str(e)})
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        return ToolResult(
            output={
                "ok": False,
                "error": f"content exceeds {_MAX_BYTES}-byte write cap",
            },
        )
    p = Path(raw_path).expanduser()
    if p.exists():
        return ToolResult(
            output={
                "ok": False,
                "error": f"target exists: {raw_path} (use fs.modify to overwrite)",
            },
        )
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(encoded)
    except OSError as e:
        return ToolResult(output={"ok": False, "error": f"write failed: {e}"})
    return ToolResult(
        output={
            "ok": True,
            "path": raw_path,
            "uri": uri,
            "bytes_written": len(encoded),
            "created": True,
        },
    )


async def _fs_modify_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    raw_path = str(args["path"])
    content = str(args.get("content", ""))
    try:
        uri = _to_file_uri(raw_path)
    except ValueError as e:
        return ToolResult(output={"ok": False, "error": str(e)})
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        return ToolResult(
            output={
                "ok": False,
                "error": f"content exceeds {_MAX_BYTES}-byte write cap",
            },
        )
    p = Path(raw_path).expanduser()
    if not p.is_file():
        return ToolResult(
            output={
                "ok": False,
                "error": f"target does not exist: {raw_path} (use fs.create to create)",
            },
        )
    try:
        # Write to a sibling temp file first, then atomic rename, so
        # a half-completed write can't leave the original truncated.
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(encoded)
        tmp.replace(p)
    except OSError as e:
        return ToolResult(output={"ok": False, "error": f"write failed: {e}"})
    return ToolResult(
        output={
            "ok": True,
            "path": raw_path,
            "uri": uri,
            "bytes_written": len(encoded),
            "created": False,
        },
    )


def make_fs_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="fs.read",
            operations=(Operation(EffectClass.FETCH, subtype="fs.read"),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            description=(
                "Read a UTF-8 text file from disk. Required arg: path "
                "(absolute). Returns {ok, path, uri, text} on success or "
                "{ok: False, error} on failure. Bounded at "
                f"{_MAX_BYTES} bytes per read."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=_fs_read_handler,
            target_arg="path",
            effect_class="data.read_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            surfaces_destination_id=True,
            inherent_tags=LabelState(
                b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)})
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute filesystem path to read.",
                    },
                },
                "required": ["path"],
            },
        ),
        ToolDefinition(
            name="fs.read_pdf",
            operations=(Operation(EffectClass.FETCH, subtype="fs.read_pdf"),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            description=(
                "Read a PDF file and return its text content. Pages are "
                "separated by ASCII form-feed. Required arg: path "
                "(absolute). Bounded at 8x the text read cap."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=_fs_read_pdf_handler,
            target_arg="path",
            effect_class="data.read_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            surfaces_destination_id=True,
            inherent_tags=LabelState(
                b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)})
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute filesystem path to the PDF.",
                    },
                },
                "required": ["path"],
            },
        ),
        ToolDefinition(
            name="fs.create",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="fs.create"),),
            risk_ids=("RISK-DESTRUCTIVE-WRITE",),
            description=(
                "Create a new UTF-8 text file. REFUSES if the target "
                "already exists (use fs.modify to overwrite). Required "
                "args: path (absolute), content (string). Bounded at "
                f"{_MAX_BYTES} bytes."
            ),
            capability_kind=CapabilityKind.CREATE_FS,
            handler=_fs_create_handler,
            target_arg="path",
            effect_class="data.create_local",
            # Creating a NEW file (refuses if target exists) is genuinely
            # low-stakes: the operator can delete it. Real deployments
            # can add friction via envelopes or path bindings without
            # touching this default.
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            surfaces_destination_id=True,
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path."},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        ),
        ToolDefinition(
            name="fs.modify",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="fs.modify"),),
            risk_ids=("RISK-DESTRUCTIVE-WRITE",),
            description=(
                "Overwrite an existing UTF-8 text file. REFUSES if the "
                "target does not already exist (use fs.create instead). "
                "Atomic-replace semantics — a failed write leaves the "
                "original intact. Required args: path (absolute), content."
            ),
            capability_kind=CapabilityKind.MODIFY_FS,
            handler=_fs_modify_handler,
            target_arg="path",
            effect_class="data.modify_local",
            default_reversibility={"degree": "reversible-with-friction", "agent": "human"},
            tool_provenance="operator-curated",
            surfaces_destination_id=True,
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path."},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        ),
    ]
