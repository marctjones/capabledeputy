"""Standalone MCP server: read-only git operations.

Tools exposed:
  - git.status(repo_path)            git status --porcelain=v1
  - git.log(repo_path, max_count=10) git log oneline format
  - git.diff(repo_path, staged=False) git diff (or git diff --cached)
  - git.show(repo_path, ref)         git show <ref>
  - git.branch_list(repo_path)       local branch list

All tools are READ-ONLY. No commit / push / checkout / merge / rebase.
Operators wanting write ops should grant explicit caps + use shell.

Run via:
  capdep mcp-server-git
  python -m capabledeputy.mcp_servers.git
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools

SERVER_NAME = "capdep-git"
MAX_OUTPUT_BYTES = 256 * 1024
MAX_LOG_COUNT = 1000


async def _git(repo_path: str, *args: str) -> dict[str, Any]:
    p = Path(repo_path)
    if not p.is_absolute():
        raise ValueError(f"repo_path must be absolute: {repo_path}")
    if not (p / ".git").exists() and not (p.is_file() and p.name == ".git"):
        # Allow worktree linked .git file too.
        raise ValueError(f"not a git repository: {p}")
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(p),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    truncated = False
    if len(stdout_bytes) > MAX_OUTPUT_BYTES:
        stdout_bytes = stdout_bytes[:MAX_OUTPUT_BYTES]
        truncated = True
    return {
        "repo": str(p),
        "args": list(args),
        "returncode": proc.returncode,
        "stdout": stdout_bytes.decode("utf-8", errors="replace"),
        "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        "truncated": truncated,
    }


async def _status(args: dict[str, Any]) -> dict[str, Any]:
    return await _git(args["repo_path"], "status", "--porcelain=v1", "--branch")


async def _log(args: dict[str, Any]) -> dict[str, Any]:
    max_count = int(args.get("max_count", 10))
    if max_count < 1 or max_count > MAX_LOG_COUNT:
        raise ValueError(f"max_count must be in [1, {MAX_LOG_COUNT}]")
    return await _git(
        args["repo_path"],
        "log",
        f"--max-count={max_count}",
        "--oneline",
        "--decorate",
    )


async def _diff(args: dict[str, Any]) -> dict[str, Any]:
    if bool(args.get("staged", False)):
        return await _git(args["repo_path"], "diff", "--cached")
    return await _git(args["repo_path"], "diff")


async def _show(args: dict[str, Any]) -> dict[str, Any]:
    ref = str(args["ref"])
    # Refuse refs containing shell metacharacters as a basic defensive
    # measure. The subprocess uses exec (not shell), so injection isn't
    # the concern; the goal is to reject obvious malformed input early.
    if any(c in ref for c in [";", "&", "|", "$", "`", "\n"]):
        raise ValueError(f"invalid ref: {ref}")
    return await _git(args["repo_path"], "show", "--stat", "--patch", ref)


async def _branch_list(args: dict[str, Any]) -> dict[str, Any]:
    return await _git(args["repo_path"], "branch", "--list", "-v")


def tools() -> list[ToolDescriptor]:
    common_repo_schema = {
        "type": "object",
        "properties": {"repo_path": {"type": "string"}},
        "required": ["repo_path"],
    }
    return [
        ToolDescriptor(
            name="git.status",
            description="git status --porcelain=v1 --branch on a repository.",
            input_schema=common_repo_schema,
            handler=_status,
            annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="git.log",
            description=(
                "git log --oneline --decorate. max_count caps the number "
                f"of commits returned (default 10, max {MAX_LOG_COUNT})."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "max_count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_LOG_COUNT,
                        "default": 10,
                    },
                },
                "required": ["repo_path"],
            },
            handler=_log,
            annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="git.diff",
            description=(
                "git diff (unstaged) or git diff --cached (staged) on a "
                "repository. Set staged=true for the cached form."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "staged": {"type": "boolean", "default": False},
                },
                "required": ["repo_path"],
            },
            handler=_diff,
            annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="git.show",
            description="git show --stat --patch <ref> on a repository.",
            input_schema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "ref": {"type": "string"},
                },
                "required": ["repo_path", "ref"],
            },
            handler=_show,
            annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="git.branch_list",
            description="git branch --list -v on a repository.",
            input_schema=common_repo_schema,
            handler=_branch_list,
            annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
        ),
    ]


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
