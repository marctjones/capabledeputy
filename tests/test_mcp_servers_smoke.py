"""Smoke tests for the five bundled Python MCP servers.

These tests call the tool handlers directly (bypassing the MCP stdio
protocol). The serve()/main() entry points only wire up the protocol;
the tool logic is what we want to verify.

For end-to-end stdio testing, see tests/test_upstream_adapter.py
(which can launch these as subprocesses).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from capabledeputy.mcp_servers import fs as fs_server
from capabledeputy.mcp_servers import git as git_server
from capabledeputy.mcp_servers import memory as memory_server


def _h(server_mod, tool_name):
    """Look up a handler by tool name within a server module."""
    for t in server_mod.tools():
        if t.name == tool_name:
            return t.handler
    raise KeyError(tool_name)


# ---------- fs ----------


@pytest.mark.asyncio
async def test_fs_create_then_read_then_delete(tmp_path: Path) -> None:
    p = tmp_path / "hello.txt"
    create = _h(fs_server, "fs.create")
    read = _h(fs_server, "fs.read")
    delete = _h(fs_server, "fs.delete")

    r1 = await create({"path": str(p), "content": "hi"})
    assert isinstance(r1, dict)
    assert r1["created"] is True
    assert r1["size"] == 2

    r2 = await read({"path": str(p)})
    assert isinstance(r2, dict)
    assert r2["content"] == "hi"
    assert r2["size"] == 2

    r3 = await delete({"path": str(p)})
    assert isinstance(r3, dict)
    assert r3["deleted"] is True
    assert not p.exists()


@pytest.mark.asyncio
async def test_fs_create_refuses_existing(tmp_path: Path) -> None:
    p = tmp_path / "exists.txt"
    p.write_text("already here")
    with pytest.raises(ValueError, match="file exists"):
        await _h(fs_server, "fs.create")({"path": str(p), "content": "new"})


@pytest.mark.asyncio
async def test_fs_write_refuses_nonexistent(tmp_path: Path) -> None:
    p = tmp_path / "nope.txt"
    with pytest.raises(ValueError, match="does not exist"):
        await _h(fs_server, "fs.write")({"path": str(p), "content": "x"})


@pytest.mark.asyncio
async def test_fs_relative_path_refused() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        await _h(fs_server, "fs.read")({"path": "relative.txt"})


@pytest.mark.asyncio
async def test_fs_list(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("bb")
    (tmp_path / "subdir").mkdir()
    r = await _h(fs_server, "fs.list")({"path": str(tmp_path)})
    assert isinstance(r, dict)
    names = [e["name"] for e in r["entries"]]
    assert names == ["a.txt", "b.txt", "subdir"]
    types = {e["name"]: e["type"] for e in r["entries"]}
    assert types["subdir"] == "dir"
    assert types["a.txt"] == "file"


# ---------- memory ----------


@pytest.fixture
def memory_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Force memory server to use a tmp_path-scoped SQLite db."""
    db = tmp_path / "memory.sqlite"
    monkeypatch.setenv("CAPDEP_MCP_MEMORY_DB", str(db))
    return db


@pytest.mark.asyncio
async def test_memory_create_read_list_delete(memory_db: Path) -> None:
    create = _h(memory_server, "memory.create")
    read = _h(memory_server, "memory.read")
    list_ = _h(memory_server, "memory.list")
    delete = _h(memory_server, "memory.delete")

    r1 = await create({"key": "alpha", "value": "first"})
    assert isinstance(r1, dict)
    assert r1["key"] == "alpha"
    r2 = await create({"key": "beta", "value": "second"})
    assert isinstance(r2, dict)
    assert r2["key"] == "beta"

    r3 = await read({"key": "alpha"})
    assert isinstance(r3, dict)
    assert r3["value"] == "first"

    r4 = await list_({})
    assert isinstance(r4, dict)
    assert r4["count"] == 2
    assert {e["key"] for e in r4["entries"]} == {"alpha", "beta"}

    r5 = await list_({"prefix": "be"})
    assert isinstance(r5, dict)
    assert r5["count"] == 1
    assert r5["entries"][0]["key"] == "beta"

    r6 = await delete({"key": "alpha"})
    assert isinstance(r6, dict)
    assert r6["deleted"] is True

    with pytest.raises(ValueError, match="no such key"):
        await read({"key": "alpha"})


@pytest.mark.asyncio
async def test_memory_create_refuses_duplicate(memory_db: Path) -> None:
    create = _h(memory_server, "memory.create")
    await create({"key": "k", "value": "v"})
    with pytest.raises(ValueError, match="entry exists"):
        await create({"key": "k", "value": "different"})


@pytest.mark.asyncio
async def test_memory_update_refuses_nonexistent(memory_db: Path) -> None:
    update = _h(memory_server, "memory.update")
    with pytest.raises(ValueError, match="no such key"):
        await update({"key": "nope", "value": "x"})


# ---------- git ----------


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A throwaway git repo for git server smoke tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.mark.asyncio
async def test_git_status_log_branches(git_repo: Path) -> None:
    status = await _h(git_server, "git.status")({"repo_path": str(git_repo)})
    assert isinstance(status, dict)
    assert status["returncode"] == 0
    # Clean tree after the init commit
    assert "## " in status["stdout"]  # branch line

    log = await _h(git_server, "git.log")({"repo_path": str(git_repo), "max_count": 5})
    assert isinstance(log, dict)
    assert log["returncode"] == 0
    assert "init" in log["stdout"]

    branches = await _h(git_server, "git.branch_list")({"repo_path": str(git_repo)})
    assert isinstance(branches, dict)
    assert branches["returncode"] == 0


@pytest.mark.asyncio
async def test_git_refuses_relative_path() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        await _h(git_server, "git.status")({"repo_path": "rel/path"})


@pytest.mark.asyncio
async def test_git_refuses_non_repo(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a git"):
        await _h(git_server, "git.status")({"repo_path": str(tmp_path)})


@pytest.mark.asyncio
async def test_git_show_refuses_bad_ref(git_repo: Path) -> None:
    with pytest.raises(ValueError, match="invalid ref"):
        await _h(git_server, "git.show")({"repo_path": str(git_repo), "ref": "HEAD;ls"})


# ---------- fetch / search: smoke (handlers exist) ----------


def test_fetch_server_exposes_get() -> None:
    from capabledeputy.mcp_servers import fetch as fetch_server

    names = {t.name for t in fetch_server.tools()}
    assert "fetch.get" in names


def test_search_server_exposes_web() -> None:
    from capabledeputy.mcp_servers import search as search_server

    names = {t.name for t in search_server.tools()}
    assert "search.web" in names
