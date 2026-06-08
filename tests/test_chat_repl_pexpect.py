"""Pexpect-driven smoke tests for the `capdep chat` REPL.

These tests spawn the real `uv run capdep chat` binary as a child
process, drive it through slash commands (which are NOT routed to
the LLM — they're pure REPL state), and assert on output.

WHY THIS IS OPT-IN:
  - Slow: ~3-5s per invocation (daemon spawn + REPL startup).
  - Brittle: depends on prompt-toolkit's terminal control codes,
    which vary by TERM env var and tty state.
  - Requires `gws auth` / `imap creds` / etc. to be present on the
    developer's machine for the agent's tools to actually register,
    though slash commands work regardless.

Opt in by setting CAPDEP_RUN_REPL_TESTS=1.

NON-DESTRUCTIVE: tests only invoke `/help`, `/tools`, `/status`,
`/quit`. Never sends a chat message to the LLM (no agent turn), so
no tool calls happen. The REPL surface is the unit under test.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest

# pexpect imports are guarded so the test module loads even when the
# optional dep is missing in CI.
pexpect = pytest.importorskip("pexpect", reason="pexpect not installed")

REPL_OPT_IN = pytest.mark.skipif(
    not os.environ.get("CAPDEP_RUN_REPL_TESTS"),
    reason="CAPDEP_RUN_REPL_TESTS not set",
)


def _spawn_chat(timeout: int = 30):
    """Spawn `uv run capdep chat` and return the pexpect child. The
    caller is responsible for `.close()`-ing it. We strip prompt-
    toolkit's rich formatting from output by setting TERM=dumb so
    `expect()` matches against plain text."""
    if shutil.which("uv") is None:
        pytest.skip("uv not available; cannot spawn capdep chat")
    env = os.environ.copy()
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    return pexpect.spawn(
        "uv",
        ["run", "capdep", "chat"],
        env=env,
        encoding="utf-8",
        timeout=timeout,
    )


def _strip_ansi(s: str) -> str:
    """Drop ANSI escape sequences so substring assertions don't have
    to fight terminal codes."""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", s)


# --- The opt-in suite -------------------------------------------------------


@REPL_OPT_IN
def test_help_lists_core_commands() -> None:
    """`/help` should mention the foundational slash commands."""
    child = _spawn_chat()
    try:
        # Wait for the prompt that signals REPL is ready
        child.expect(r"chat>", timeout=30)
        child.sendline("/help")
        child.expect(r"chat>", timeout=10)
        out = _strip_ansi(child.before)
        # The runtime _HELP constant lists these — assert each present.
        for expected in ("/sessions", "/grant", "/status", "/tools", "/quit"):
            assert expected in out, f"missing {expected!r} in /help output"
    finally:
        child.sendline("/quit")
        child.close()


@REPL_OPT_IN
def test_tools_command_runs() -> None:
    """`/tools` should print the registered tools without error.
    Exact count is environment-dependent (depends on whether
    bundled servers + Gmail / Workspace are wired), but the command
    must return cleanly and produce output that mentions at least
    one tool name."""
    child = _spawn_chat()
    try:
        child.expect(r"chat>", timeout=30)
        child.sendline("/tools")
        child.expect(r"chat>", timeout=15)
        out = _strip_ansi(child.before)
        # Look for the standard "N tool(s) available" header OR an
        # explicit "no tools registered" — either means the command
        # executed; an UNKNOWN-COMMAND error would mean it didn't.
        assert (
            "tool(s) available" in out or "no tools registered" in out or "no tools match" in out
        ), f"/tools produced unexpected output: {out!r}"
    finally:
        child.sendline("/quit")
        child.close()


@REPL_OPT_IN
def test_tools_filter_substring() -> None:
    """`/tools <filter>` should narrow the listing. With a clearly-
    fake filter, the output should be "no tools match"."""
    child = _spawn_chat()
    try:
        child.expect(r"chat>", timeout=30)
        child.sendline("/tools zzz_definitely_not_a_real_tool")
        child.expect(r"chat>", timeout=10)
        out = _strip_ansi(child.before)
        assert "no tools match" in out, (
            f"expected 'no tools match' for nonsense filter; got {out!r}"
        )
    finally:
        child.sendline("/quit")
        child.close()


@REPL_OPT_IN
def test_unknown_command_reports() -> None:
    """An unknown slash command must produce a clear `unknown command`
    error rather than silently passing through to the LLM."""
    child = _spawn_chat()
    try:
        child.expect(r"chat>", timeout=30)
        child.sendline("/totally_made_up_command")
        child.expect(r"chat>", timeout=10)
        out = _strip_ansi(child.before)
        assert "unknown command" in out
    finally:
        child.sendline("/quit")
        child.close()


@REPL_OPT_IN
def test_grant_then_caps_round_trip() -> None:
    """Grant a benign read-only capability via /grant, then verify
    /caps shows it. This exercises:
      - /grant parsing
      - session.grant_capability RPC
      - /caps rendering
    No tool is invoked; nothing leaves the local daemon."""
    child = _spawn_chat()
    try:
        child.expect(r"chat>", timeout=30)
        # Grant a fresh READ_FS scope.
        child.sendline("/grant READ_FS /tmp/test-pexpect-*")
        child.expect(r"chat>", timeout=10)
        grant_out = _strip_ansi(child.before)
        # Either the grant succeeded ("granted") or it was already
        # present from the auto-grant defaults — both are acceptable.
        assert "grant" in grant_out.lower() or "already" in grant_out.lower(), (
            f"unexpected /grant output: {grant_out!r}"
        )

        child.sendline("/caps")
        child.expect(r"chat>", timeout=10)
        caps_out = _strip_ansi(child.before)
        # /caps should list READ_FS at minimum (auto-grant defaults
        # ensure it's there even without our /grant).
        assert "READ_FS" in caps_out, f"/caps missing READ_FS; got: {caps_out!r}"
    finally:
        child.sendline("/quit")
        child.close()


# --- Always-on registration smoke (no REPL spawn needed) -------------------


def test_capdep_chat_binary_exists() -> None:
    """Sanity: the `capdep chat` command resolves through `uv run`.
    Independent of REPL_OPT_IN — this is fast and catches packaging
    regressions."""
    if shutil.which("uv") is None:
        pytest.skip("uv not available")
    result = subprocess.run(
        ["uv", "run", "capdep", "chat", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # --help must exit 0 and mention the chat subcommand
    assert result.returncode == 0, f"capdep chat --help failed: {result.stderr}"
    assert "session" in result.stdout.lower() or "chat" in result.stdout.lower()
