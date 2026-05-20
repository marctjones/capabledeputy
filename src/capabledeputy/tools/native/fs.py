"""Local filesystem read tools — `fs.read` (text) and `fs.read_pdf`.

Two REAL tools (not stubs): the operator can read text files and PDFs
off the local filesystem. The tools are minimal by design — read-only,
size-bounded, and aware of `PolicyContext.bindings` if the operator has
declared any.

Binding behavior:
  - With a bound BindingSet: every path goes through `bindings.resolve`.
    Unbound paths refuse (fail-closed per FR-023). The matched binding
    determines `category` and `tier`, which the dispatcher uses for the
    v2 four-axis label propagation.
  - Without a binding set: the tool still reads but propagates the
    legacy `UNTRUSTED_USER_INPUT` label so the session knows the source
    is operator-curated-but-not-vetted.

Output bound at 64 KiB per file to keep the orchestrator's context
window honest. PDF pages are concatenated with form-feed separators so
schema-bounded extractors can detect page boundaries.

`fs.write` / `fs.modify` are deliberately NOT added here — write tools
have a much bigger blast radius and belong with spec-005's `fs.write`
that integrates with the binding's `write_discipline`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult

# Cap each read at 64 KiB. The orchestrator's context is precious; if
# the operator wants more, they should explicitly chunk via offset/limit
# (future work) or summarize with quarantined.extract first.
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
        # In the absence of a binding (no PolicyContext.bindings), tag
        # the value untrusted-user-input. With a binding, the dispatcher
        # will derive the correct category/tier via FR-043; the legacy
        # label still flows because that path is still wired.
        additional_labels=frozenset({Label.UNTRUSTED_USER_INPUT}),
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
    # Local import keeps pypdf optional for users who don't read PDFs.
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
        additional_labels=frozenset({Label.UNTRUSTED_USER_INPUT}),
    )


def make_fs_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="fs.read",
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
            inherent_labels=frozenset({Label.UNTRUSTED_USER_INPUT}),
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
            inherent_labels=frozenset({Label.UNTRUSTED_USER_INPUT}),
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
    ]
