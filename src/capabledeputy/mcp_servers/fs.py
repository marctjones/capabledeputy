"""Standalone MCP server: filesystem operations.

Tools exposed:
  - fs.read(path)               read text content of a file
  - fs.list(path)               list directory entries
  - fs.create(path, content)    create a file; refuses to overwrite
  - fs.write(path, content)     overwrite an existing file
  - fs.delete(path)             delete a file

Operates only on absolute paths. 64KB cap on read; 256KB cap on
write. No traversal protection — that's the host application's job
(the CapableDeputy chokepoint enforces bindings + capabilities).

Run via:
  capdep mcp-server-fs                    (from CapableDeputy CLI)
  python -m capabledeputy.mcp_servers.fs  (standalone, any MCP host)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools

SERVER_NAME = "capdep-fs"
MAX_READ_BYTES = 64 * 1024
MAX_WRITE_BYTES = 256 * 1024


def _validate_path(path_str: str) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        raise ValueError(f"path must be absolute: {path_str}")
    return p


async def _read(args: dict[str, Any]) -> dict[str, Any]:
    p = _validate_path(args["path"])
    if not p.is_file():
        raise ValueError(f"not a file: {p}")
    size = p.stat().st_size
    if size > MAX_READ_BYTES:
        raise ValueError(f"file too large: {size} bytes (max {MAX_READ_BYTES})")
    text = p.read_text(encoding="utf-8")
    return {"path": str(p), "size": size, "content": text}


async def _list(args: dict[str, Any]) -> dict[str, Any]:
    p = _validate_path(args["path"])
    if not p.is_dir():
        raise ValueError(f"not a directory: {p}")
    entries = []
    for child in sorted(p.iterdir()):
        entries.append(
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            },
        )
    return {"path": str(p), "entries": entries}


async def _create(args: dict[str, Any]) -> dict[str, Any]:
    p = _validate_path(args["path"])
    if p.exists():
        raise ValueError(f"file exists; use fs.write to overwrite: {p}")
    content = str(args["content"])
    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        raise ValueError(f"content too large (max {MAX_WRITE_BYTES} bytes)")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": str(p), "size": p.stat().st_size, "created": True}


async def _write(args: dict[str, Any]) -> dict[str, Any]:
    p = _validate_path(args["path"])
    if not p.is_file():
        raise ValueError(f"file does not exist; use fs.create: {p}")
    content = str(args["content"])
    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        raise ValueError(f"content too large (max {MAX_WRITE_BYTES} bytes)")
    # Atomic replace via tmp file in same directory.
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(p)
    return {"path": str(p), "size": p.stat().st_size, "replaced": True}


async def _delete(args: dict[str, Any]) -> dict[str, Any]:
    p = _validate_path(args["path"])
    if not p.is_file():
        raise ValueError(f"not a file: {p}")
    p.unlink()
    return {"path": str(p), "deleted": True}


def tools() -> list[ToolDescriptor]:
    return [
        ToolDescriptor(
            name="fs.read",
            description=(
                "Read the UTF-8 text content of a file. Refuses files "
                f"larger than {MAX_READ_BYTES} bytes. Path must be absolute."
            ),
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=_read,
            annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="fs.list",
            description="List entries in a directory (sorted, non-recursive).",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=_list,
            annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="fs.create",
            description=(
                "Create a file. Refuses if the file already exists; use "
                "fs.write to overwrite an existing file."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=_create,
            annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="fs.write",
            description=(
                "Overwrite an existing file atomically. Refuses if the "
                "file does not yet exist; use fs.create for new files."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=_write,
            annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="fs.delete",
            description="Delete a file. Irreversible.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=_delete,
            annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
        ),
    ]


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
