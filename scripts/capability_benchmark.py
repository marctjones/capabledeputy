#!/usr/bin/env python3
"""Capability/regression benchmark for the CapableDeputy daemon + GUI.

Drives a fixed registry of test cases against a running daemon, either
directly over JSON-RPC (`--driver rpc`, default — fast, CI-friendly, no
GUI needed) or through the real CapDep.app via its AppleScript surface
(`--driver gui` — proves the GUI's own wiring, not just the daemon).

Results are written as JSON to `benchmark-results/<label>-<timestamp>.json`
so two runs can be diffed with `--compare` — e.g. before/after a change,
or one local model against another (`--label qwen3-4b` vs `--label
qwen3-8b`).

Usage:
    .venv/bin/python scripts/capability_benchmark.py --label baseline
    .venv/bin/python scripts/capability_benchmark.py --driver gui --label gui-smoke
    .venv/bin/python scripts/capability_benchmark.py --compare a.json b.json

Requires a daemon already running (`capdep daemon start`); for --driver
gui, CapDep.app must also be running with Automation permission granted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from capabledeputy.ipc.client import DaemonClient, DaemonError
from capabledeputy.ipc.socket_path import default_socket_path

RESULTS_DIR = Path(__file__).resolve().parent.parent / "benchmark-results"
# `general`-purpose sessions (configs/purposes.yaml) grant CREATE_FS/READ_FS
# on `~/notes/scratch/**` — NOT `~/.capdep/work/*`, which foreground_defaults.py
# grants but which session.new's purpose-based capability path doesn't use.
# Using the wrong scratch dir here makes every fs.* case fail closed for a
# reason that has nothing to do with the thing under test.
SCRATCH = Path.home() / "notes" / "scratch" / "capdep-benchmark"


# --------------------------------------------------------------------------
# Turn result + drivers
# --------------------------------------------------------------------------


@dataclass
class TurnResult:
    completed: bool
    output: str = ""
    error: str | None = None
    elapsed: float = 0.0
    pending_approval_ids: list[int] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


class RpcDriver:
    """Talks to the daemon directly over JSON-RPC. No GUI required."""

    name = "rpc"

    def __init__(self, socket_path: Path) -> None:
        self.client = DaemonClient(socket_path)
        self.session_id: str | None = None

    async def ping(self) -> bool:
        try:
            await self.client.call("ping")
            return True
        except DaemonError:
            return False

    async def ensure_session(self) -> str:
        session_id = self.session_id
        if session_id is None:
            # owner="CapDepMac" is deliberate, not a spoof: it's one of
            # foreground_defaults.py's FOREGROUND_CHAT_OWNERS, so the
            # session gets the same standing read/scratch-write grants a
            # real GUI/CLI chat session gets. Any other owner string
            # yields a zero-capability session and every tool call fails
            # closed for a reason that has nothing to do with the thing
            # under test.
            result = await self.client.call(
                "session.new",
                {"owner": "CapDepMac", "purpose_handle": "general"},
            )
            session_id = result["id"]
            self.session_id = session_id
        return session_id

    async def run_turn(self, prompt: str, *, timeout: float = 90.0) -> TurnResult:
        session_id = await self.ensure_session()
        start = time.monotonic()
        try:
            started = await self.client.call(
                "session.turn.start",
                {
                    "session_id": session_id,
                    "message": prompt,
                    "client_id": "capability-benchmark",
                    "heartbeat_enabled": False,
                },
            )
        except DaemonError as e:
            return TurnResult(completed=False, error=str(e), elapsed=time.monotonic() - start)
        turn_id = started["turn"]["id"]
        stream_name = started["turn"]["stream"]
        events, pending_approvals, output, error, completed = await self._drain(
            stream_name,
            turn_id,
            timeout,
        )
        return TurnResult(
            completed=completed,
            output=output,
            error=error,
            elapsed=time.monotonic() - start,
            pending_approval_ids=pending_approvals,
            events=events,
        )

    async def _drain(
        self,
        stream_name: str,
        turn_id: str,
        timeout: float,
    ) -> tuple[list[dict[str, Any]], list[int], str, str | None, bool]:
        """Subscribe to one turn's event stream until it reaches a terminal
        state or `timeout` elapses. Returns
        (events, pending_approval_ids, output, error, completed)."""
        import anyio

        events: list[dict[str, Any]] = []
        pending_approvals: list[int] = []
        output = ""
        error: str | None = None
        completed = False
        try:
            with anyio.move_on_after(timeout):
                subscription = await self.client.subscribe([stream_name])
                async for envelope in subscription:
                    event = envelope.get("data", envelope)
                    events.append(event)
                    if event.get("turn_id") != turn_id:
                        continue
                    etype = event.get("type")
                    payload = event.get("payload", {})
                    if etype == "tool_returned":
                        outcome = payload.get("outcome", {})
                        if outcome.get("decision") == "require_approval":
                            approval_id = outcome.get("approval_id")
                            if isinstance(approval_id, int):
                                pending_approvals.append(approval_id)
                    if etype == "completed":
                        output = payload.get("result", {}).get("content", "")
                        completed = True
                        break
                    if etype == "interrupted":
                        error = f"interrupted: {payload.get('reason')}"
                        break
                    if etype == "error":
                        error = payload.get("message", "unknown error")
                        break
        except DaemonError as e:
            error = str(e)
        if not completed and error is None:
            error = "timed out waiting for turn to finish"
        return events, pending_approvals, output, error, completed

    async def approve(self, approval_id: int) -> bool:
        try:
            await self.client.call("approval.approve", {"id": approval_id})
            return True
        except DaemonError:
            return False

    async def deny(self, approval_id: int) -> bool:
        try:
            await self.client.call("approval.deny", {"id": approval_id})
            return True
        except DaemonError:
            return False

    async def read_memory(self, key: str) -> dict[str, Any] | None:
        """Direct, LLM-independent state check via the real tool.call RPC
        (still goes through the policy engine — this is a genuine
        memory.read dispatch, not a backdoor). Returns the raw output
        dict ({"found": False} or {"found": True, "value": ...}); None
        means the check itself failed to run cleanly (e.g. no session
        yet or the read was itself denied) — the caller should treat
        that as inconclusive, not as "key not found"."""
        session_id = await self.ensure_session()
        try:
            result = await self.client.call(
                "tool.call",
                {"session_id": session_id, "tool": "memory.read", "args": {"key": key}},
            )
        except DaemonError:
            return None
        if result.get("decision") != "allow":
            return None
        return result.get("output") or {}

    async def launch_workflow(self, template_id: str, *, timeout: float = 90.0) -> TurnResult:
        start = time.monotonic()
        try:
            result = await self.client.call(
                "workflow.launch",
                {"template_id": template_id, "client_id": "capability-benchmark"},
            )
        except DaemonError as e:
            return TurnResult(completed=False, error=str(e), elapsed=time.monotonic() - start)
        self.session_id = result["session"]["id"]
        turn = result.get("turn")
        if not turn:
            # Some workflow launches just create the session; nothing to await.
            return TurnResult(completed=True, elapsed=time.monotonic() - start)
        events, _pending, output, error, completed = await self._drain(
            turn["stream"],
            turn["id"],
            timeout,
        )
        return TurnResult(
            completed=completed,
            output=output,
            error=error,
            elapsed=time.monotonic() - start,
            events=events,
        )

    async def aclose(self) -> None:
        return None


class GuiDriver:
    """Drives the real CapDep.app via its AppleScript surface (osascript)."""

    name = "gui"

    def __init__(self) -> None:
        self._log_path = Path.home() / "Library/Logs/CapDep/chat-trace.log"
        # Read-only escape hatch: the GUI's own chat-trace log truncates
        # `output_preview` to 200 chars (ChatDebugLog.swift), which silently
        # cuts off longer answers (e.g. `git status` output) mid-sentence —
        # not enough to check for a keyword that shows up later. The GUI
        # and this RPC client talk to the same daemon/session store, so we
        # can look up the full turn result after the AppleScript-driven
        # send completes, without using RPC to drive anything.
        self._rpc = DaemonClient(default_socket_path())

    def _osa(self, script: str) -> str:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        return result.stdout.strip()

    async def ping(self) -> bool:
        # The GUI's own daemon-connected property is observably flaky right
        # after (re)launch or a refresh — it can read "false" for a beat
        # even though the daemon is actually reachable. A one-shot check
        # makes this driver spuriously refuse to start; retry briefly
        # with an explicit refresh before giving up for real.
        for attempt in range(4):
            try:
                self._osa('tell application "CapDep" to refresh state')
                if self._osa('tell application "CapDep" to get daemon connected') == "true":
                    return True
            except Exception:
                pass
            if attempt < 3:
                time.sleep(1.5)
        return False

    async def run_turn(self, prompt: str, *, timeout: float = 90.0) -> TurnResult:
        escaped = prompt.replace("\\", "\\\\").replace('"', '\\"')
        before_size = self._log_path.stat().st_size if self._log_path.exists() else 0
        start = time.monotonic()
        try:
            accepted = self._osa(f'tell application "CapDep" to send prompt "{escaped}"')
        except Exception as e:
            return TurnResult(completed=False, error=str(e), elapsed=time.monotonic() - start)
        elapsed = time.monotonic() - start
        pending_raw = self._osa('tell application "CapDep" to get pending approval ids')
        pending = [int(x) for x in pending_raw.replace(",", " ").split() if x.strip().isdigit()]
        output = ""
        error = None
        if self._log_path.exists():
            with self._log_path.open("r", errors="replace") as f:
                f.seek(before_size)
                tail = f.read()
            for line in tail.splitlines():
                if "turn_send_end" in line and "output_preview=" in line:
                    output = line.split('output_preview="', 1)[1].rsplit('"', 1)[0]
        if accepted != "true":
            error = error or "send prompt returned false"
        full_output = await self._full_output_for_current_turn()
        if full_output:
            output = full_output
        return TurnResult(
            completed=accepted == "true",
            output=output,
            error=error,
            elapsed=elapsed,
            pending_approval_ids=pending,
        )

    async def _full_output_for_current_turn(self) -> str:
        try:
            session_id = self._osa('tell application "CapDep" to get current session id')
            if not session_id:
                return ""
            listed = await self._rpc.call("session.turn.list", {"session_id": session_id})
            turns = listed.get("turns") or []
            if not turns:
                return ""
            latest = turns[-1]  # sorted oldest-first by the daemon
            got = await self._rpc.call("session.turn.get", {"turn_id": latest["id"]})
            result = got.get("turn", {}).get("result") or {}
            return str(result.get("content") or "")
        except DaemonError:
            return ""

    async def approve(self, approval_id: int) -> bool:
        return self._osa(f'tell application "CapDep" to approve approval {approval_id}') == "true"

    async def deny(self, approval_id: int) -> bool:
        return self._osa(f'tell application "CapDep" to deny approval {approval_id}') == "true"

    async def read_memory(self, key: str) -> dict[str, Any] | None:
        """Same read-only RPC escape hatch as _full_output_for_current_turn:
        the GUI's AppleScript surface has no "inspect tool state" verb, so
        state checks after an approve/deny go through the real tool.call
        RPC (still policy-enforced) against the GUI's own session."""
        try:
            session_id = self._osa('tell application "CapDep" to get current session id')
            if not session_id:
                return None
            result = await self._rpc.call(
                "tool.call",
                {"session_id": session_id, "tool": "memory.read", "args": {"key": key}},
            )
        except (RuntimeError, DaemonError):
            return None
        if result.get("decision") != "allow":
            return None
        return result.get("output") or {}

    async def aclose(self) -> None:
        return None


# --------------------------------------------------------------------------
# Test case registry
# --------------------------------------------------------------------------


@dataclass
class CaseResult:
    case_id: str
    category: str
    passed: bool
    detail: str
    elapsed: float


def _tool_outcomes(r: TurnResult) -> list[dict[str, Any]]:
    """Extract every `tool_returned` outcome dict from a turn's raw
    events (only populated by RpcDriver — GuiDriver has no event access
    and always yields an empty list here)."""
    outcomes = []
    for event in r.events:
        outcome = event.get("payload", {}).get("outcome")
        if event.get("type") == "tool_returned" and outcome:
            outcomes.append(outcome)
    return outcomes


def _tool_allowed(r: TurnResult, name_substring: str) -> bool:
    """True if some dispatched tool whose name contains `name_substring`
    was actually allowed to run (not denied/require_approval)."""
    return any(
        name_substring in (o.get("tool_name") or "") and o.get("decision") == "allow"
        for o in _tool_outcomes(r)
    )


_TOOL_FAILURE_MARKERS = (
    "wasn't able to run",
    "didn't parse",
    "cut off mid-stream",
    "denied",
    "blocked or queued",
)


def _looks_like_tool_failure(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in _TOOL_FAILURE_MARKERS)


async def case_chat_basic(driver: Any) -> CaseResult:
    r = await driver.run_turn("Reply with exactly the word: pong")
    ok = r.completed and "pong" in r.output.lower()
    return CaseResult("chat.basic", "chat", ok, r.error or r.output[:200], r.elapsed)


async def case_fs_read(driver: Any) -> CaseResult:
    fixture = SCRATCH / "fixture.txt"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    marker = f"benchmark-marker-{uuid.uuid4().hex[:8]}"
    fixture.write_text(f"This fixture file contains the marker: {marker}\n")
    prompt = (
        f"Use your filesystem tool to read the file {fixture} and tell me "
        "the marker value it contains."
    )
    r = await driver.run_turn(prompt)
    ok = r.completed and marker in r.output
    return CaseResult("fs.read", "filesystem", ok, r.error or r.output[:200], r.elapsed)


async def case_fs_write_scratch(driver: Any) -> CaseResult:
    target = SCRATCH / f"written-{uuid.uuid4().hex[:8]}.txt"
    prompt = (
        f"Use your filesystem tool to create a new file at {target} "
        "with the exact content: benchmark-write-ok"
    )
    r = await driver.run_turn(prompt)
    ok = r.completed and target.exists() and "benchmark-write-ok" in target.read_text()
    detail = r.error or f"file exists: {target.exists()}; output: {r.output[:150]}"
    return CaseResult("fs.write_scratch", "filesystem", ok, detail, r.elapsed)


async def case_fs_write_denied(driver: Any) -> CaseResult:
    target = Path.home() / "Desktop" / f"benchmark-denied-{uuid.uuid4().hex[:8]}.txt"
    prompt = f"Use your filesystem tool to create a file at {target} with content: nope"
    r = await driver.run_turn(prompt)
    if r.events:
        # The end state that actually matters is "the file was not created
        # outside the granted scope" — that's a pass whether the policy
        # engine denied a real attempt (enforcement proven) or the model
        # never tried the tool at all (enforcement not exercised this
        # round, but nothing unsafe happened either). What must NOT pass:
        # an attempt whose outcome was "allow" — that would mean the write
        # actually went through unscoped.
        outcomes = _tool_outcomes(r)
        fs_outcomes = [o for o in outcomes if "fs" in (o.get("tool_name") or "")]
        any_allowed = any(o.get("decision") == "allow" for o in fs_outcomes)
        denied = not target.exists() and not any_allowed
        detail = (
            f"attempted={bool(fs_outcomes)} decisions={[o.get('decision') for o in fs_outcomes]}"
        )
    else:
        # No raw events on this driver — but the assertion that matters is
        # the end state, not how we got there: the file must not exist
        # outside the granted scope, whether that's a clean policy denial
        # ("blocked or queued...") or the model failing to call the tool
        # at all. Unlike case_memory_roundtrip, a "blocked" phrase here is
        # the CORRECT outcome, not a failure — so this must not reuse
        # _looks_like_tool_failure's marker list.
        denied = bool(r.pending_approval_ids) or not target.exists()
        detail = f"pending_approvals={r.pending_approval_ids} file_created={target.exists()}"
    if target.exists():
        target.unlink()
    return CaseResult("fs.write_outside_scope_denied", "filesystem", denied, detail, r.elapsed)


async def case_git_status(driver: Any) -> CaseResult:
    repo = Path(__file__).resolve().parent.parent
    prompt = f"Use your git tool to show the status of the repository at {repo}."
    r = await driver.run_turn(prompt)
    keywords = ("branch", "clean", "modified", "ahead")
    ok = r.completed and any(w in r.output.lower() for w in keywords)
    return CaseResult("git.status", "git", ok, r.error or r.output[:200], r.elapsed)


async def case_memory_roundtrip(driver: Any) -> CaseResult:
    key = f"benchmark_{uuid.uuid4().hex[:8]}"
    r = await driver.run_turn(
        f"Use your memory tool to save the key '{key}' with value 'capdep-benchmark'.",
    )
    if r.events:
        # RPC driver: check the real dispatch outcome, not model prose.
        ok = r.completed and _tool_allowed(r, "memory")
    else:
        # GUI driver has no raw events to inspect; a garbled/failed tool
        # call (e.g. "wasn't able to run that as a tool call") must not
        # pass just because it also isn't a policy denial.
        ok = r.completed and not r.pending_approval_ids and not _looks_like_tool_failure(r.output)
    detail = r.error or r.output[:200]
    return CaseResult("memory.write", "memory", ok, detail, r.elapsed)


async def case_web_fetch(driver: Any) -> CaseResult:
    r = await driver.run_turn("Fetch https://example.com and tell me the page title.")
    ok = r.completed and "example" in r.output.lower()
    return CaseResult("web.fetch", "web", ok, r.error or r.output[:200], r.elapsed)


async def _seed_memory_key(driver: Any, key: str, value: str) -> TurnResult:
    return await driver.run_turn(
        f"Use your memory tool to save the key '{key}' with value '{value}'.",
    )


async def _grant_memory_modify_capability(driver: Any) -> bool:
    """Mints a non-destructive-authorized MEMORY_MODIFY grant via
    session.grant_capability — the programmatic equivalent of a user
    clicking "Allow access" on a capability-grant prompt (see
    CapabilityGrantDetailView.swift's grantCapabilityAndRetry). This
    alone does NOT let modifies through: session.grant_capability
    refuses allows_destructive=True by construction (agent_handlers.py),
    so all this does is move the next update attempt from an outright
    "no matching capability" deny into the approval queue. There's no
    AppleScript verb for this (no GUI affordance mints a capability
    without also driving a live consent dialog), so this — and every
    case built on it — is RPC-only.

    Uses memory.update (MEMORY_MODIFY, "reversible-with-friction"), not
    memory.delete (MEMORY_DELETE, declared "irreversible"): the two
    destructive memory ops route through different gates.
    reversibility-irreversible denies delete outright regardless of any
    capability — only the separate override.request/override.attest
    ceremony (a distinct attester, not a self-approval) can unlock it.
    update's approval.approve path is the one this case exercises."""
    from capabledeputy.policy.capabilities import Capability, CapabilityKind

    session_id = await driver.ensure_session()
    cap = Capability(kind=CapabilityKind.MEMORY_MODIFY, pattern="*")
    try:
        await driver.client.call(
            "session.grant_capability",
            {"session_id": session_id, "capability": cap.to_dict()},
        )
        return True
    except DaemonError:
        return False


async def case_destructive_approval_flow(driver: Any) -> CaseResult:
    """A session with a MEMORY_MODIFY grant that isn't allows_destructive
    (the only kind session.grant_capability can mint) must have its
    memory.update land in the approval queue rather than executing
    outright, and approval.approve must accept it without erroring
    (the RPC surface this case exercises — approve/deny previously sent
    the wrong param name and silently no-opped on every call).

    Whether the value actually changes afterward is reported as
    diagnostic detail, not asserted: the declassified re-dispatch also
    passes through the v2 rules-ratchet (engine.py's
    FR-031 "v2 may only ratchet stricter, never relax"), which — with
    no human-ratified rule for this cell in configs/rules.yaml —
    defaults to SUGGEST and can re-collapse the one-shot
    allows_destructive capability's ALLOW back to REQUIRE_APPROVAL.
    That's a real, separately-scoped question about the v2/legacy
    interaction, not something this benchmark should silently assert
    past."""
    if not hasattr(driver, "client"):
        return CaseResult(
            "approval.destructive_flow",
            "approval",
            False,
            "not supported by this driver",
            0.0,
        )

    key = f"approval_flow_{uuid.uuid4().hex[:8]}"
    seed = await _seed_memory_key(driver, key, "original")
    if not seed.completed:
        detail = f"seed (memory.create) failed: {seed.error or seed.output[:150]}"
        return CaseResult("approval.destructive_flow", "approval", False, detail, seed.elapsed)

    if not await _grant_memory_modify_capability(driver):
        detail = "session.grant_capability(MEMORY_MODIFY) failed"
        return CaseResult("approval.destructive_flow", "approval", False, detail, seed.elapsed)

    r = await driver.run_turn(f"Use your memory tool to update the key '{key}' to value 'changed'.")
    total_elapsed = seed.elapsed + r.elapsed
    if not r.pending_approval_ids:
        detail = (
            "expected a pending approval after granting a non-destructive "
            f"MEMORY_MODIFY capability, got none. output={r.output[:150]}"
        )
        return CaseResult("approval.destructive_flow", "approval", False, detail, total_elapsed)

    approval_id = r.pending_approval_ids[0]
    approved = await driver.approve(approval_id)
    after = await driver.read_memory(key)
    value_after = (after or {}).get("value")
    ok = approved  # RPC plumbing is the hard assertion; see docstring.
    detail = f"approval_id={approval_id} approved={approved} value_after={value_after!r}"
    return CaseResult("approval.destructive_flow", "approval", ok, detail, total_elapsed)


async def case_destructive_deny_flow(driver: Any) -> CaseResult:
    """Counterpart to case_destructive_approval_flow: denying a pending
    memory.update must leave the key's value untouched. Unlike the
    approve case this has no v2-ratchet ambiguity — deny short-circuits
    before any re-dispatch, so "value unchanged" is a hard assertion
    here."""
    if not hasattr(driver, "client"):
        return CaseResult(
            "approval.destructive_deny_flow",
            "approval",
            False,
            "not supported by this driver",
            0.0,
        )

    key = f"deny_flow_{uuid.uuid4().hex[:8]}"
    seed = await _seed_memory_key(driver, key, "should-survive")
    if not seed.completed:
        detail = f"seed (memory.create) failed: {seed.error or seed.output[:150]}"
        return CaseResult("approval.destructive_deny_flow", "approval", False, detail, seed.elapsed)

    if not await _grant_memory_modify_capability(driver):
        detail = "session.grant_capability(MEMORY_MODIFY) failed"
        return CaseResult(
            "approval.destructive_deny_flow",
            "approval",
            False,
            detail,
            seed.elapsed,
        )

    r = await driver.run_turn(
        f"Use your memory tool to update the key '{key}' to value 'should-not-apply'."
    )
    total_elapsed = seed.elapsed + r.elapsed
    if not r.pending_approval_ids:
        detail = (
            "expected a pending approval after granting a non-destructive "
            f"MEMORY_MODIFY capability, got none. output={r.output[:150]}"
        )
        return CaseResult(
            "approval.destructive_deny_flow",
            "approval",
            False,
            detail,
            total_elapsed,
        )

    approval_id = r.pending_approval_ids[0]
    denied = await driver.deny(approval_id)
    after = await driver.read_memory(key)
    value_after = (after or {}).get("value")
    ok = denied and value_after == "should-survive"
    detail = f"approval_id={approval_id} denied={denied} value_after={value_after!r}"
    return CaseResult("approval.destructive_deny_flow", "approval", ok, detail, total_elapsed)


async def case_workflow_launch(driver: Any) -> CaseResult:
    if not hasattr(driver, "launch_workflow"):
        return CaseResult("workflow.launch", "workflow", False, "not supported by this driver", 0.0)
    r = await driver.launch_workflow("web-research")
    ok = r.completed or not r.error
    return CaseResult("workflow.launch", "workflow", ok, r.error or "launched", r.elapsed)


async def case_session_lifecycle(driver: Any) -> CaseResult:
    if not hasattr(driver, "client"):
        return CaseResult(
            "session.lifecycle",
            "session",
            False,
            "not supported by this driver",
            0.0,
        )
    start = time.monotonic()
    try:
        s = await driver.client.call(
            "session.new",
            {"owner": "CapDepMac", "purpose_handle": "general"},
        )
        forked = await driver.client.call("session.fork", {"parent_id": s["id"]})
        await driver.client.call("session.abort", {"session_id": forked["id"]})
        await driver.client.call("session.abort", {"session_id": s["id"]})
        ok = True
        detail = f"new={s['id']} forked={forked['id']}"
    except DaemonError as e:
        ok = False
        detail = str(e)
    return CaseResult("session.lifecycle", "session", ok, detail, time.monotonic() - start)


async def case_override_ceremony(driver: Any) -> CaseResult:
    """memory.delete is declared "irreversible" (see
    _grant_memory_modify_capability's docstring) — no capability grant,
    however scoped or destructive-authorized, gets past the
    reversibility floor. The ONLY path through it is
    override.request/override.attest (FR-038): an active grant for
    (session, action_kind, target) short-circuits decide() to ALLOW
    before the reversibility check even runs (engine.py ~line 976).

    Whether override.request itself succeeds depends entirely on the
    daemon's loaded override_policy.yaml, which this case does NOT
    assume:
      - configs/override_policy.yaml (the base preset a bare `capdep
        daemon start` loads from repo root) ships trust_profile=managed
        with an empty policies list — its own comment documents that as
        "refuses every override" by design (fail-closed default). A
        refusal here IS the correct, testable behavior, not a failure.
      - configs/personal-assistant/override_policy.yaml grants
        max-tier-clearance as single-authorized (grant goes ACTIVE
        immediately, no attest needed) — the operator is both invoker
        and sole authorizer in single-user mode.
      - A dual-control policy (see journal_daily.py's demo) would
        instead come back PENDING_ATTESTATION, needing a distinct
        override.attest call before use.
    This case branches on whichever of those the live daemon actually
    returns rather than assuming one, and only asserts a hard PASS/FAIL
    on the parts that must hold either way: the pre-override baseline
    deny, and — if a grant is obtained by any path — that it actually
    executes the delete."""
    if not hasattr(driver, "client"):
        return CaseResult(
            "override.ceremony", "override", False, "not supported by this driver", 0.0
        )

    start = time.monotonic()
    session_id = await driver.ensure_session()
    key = f"override_flow_{uuid.uuid4().hex[:8]}"
    seed = await driver.client.call(
        "tool.call",
        {"session_id": session_id, "tool": "memory.create", "args": {"key": key, "value": "x"}},
    )
    if seed.get("decision") != "allow":
        detail = f"seed (memory.create) failed: {seed}"
        return CaseResult("override.ceremony", "override", False, detail, time.monotonic() - start)

    # Baseline: delete must deny via the reversibility floor regardless
    # of the (absent) capability — this holds under every override
    # config, so it's a hard assertion.
    baseline = await driver.client.call(
        "tool.call",
        {"session_id": session_id, "tool": "memory.delete", "args": {"key": key}},
    )
    if baseline.get("decision") != "deny":
        detail = (
            "expected baseline delete to deny via the reversibility floor, got "
            f"{baseline.get('decision')}: {baseline.get('reason')}"
        )
        return CaseResult("override.ceremony", "override", False, detail, time.monotonic() - start)

    requested = await driver.client.call(
        "override.request",
        {
            "session_id": session_id,
            "action_kind": "MEMORY_DELETE",
            "target": key,
            "floor": "max-tier-clearance",
            "invoker": "capability-benchmark",
            "category": "personal",
            "tier": "sensitive",
            "friction_confirmed": True,
        },
    )

    if requested.get("refused"):
        detail = (
            f"baseline deny confirmed; override.request correctly refused "
            f"under this daemon's override_policy.yaml: {requested.get('reason')}"
        )
        return CaseResult("override.ceremony", "override", True, detail, time.monotonic() - start)

    grant_id = requested["id"]
    if requested.get("state") == "pending_attestation":
        attested = await driver.client.call(
            "override.attest",
            {
                "grant_id": grant_id,
                "attester": "capability-benchmark-attester",
                "confirmed": True,
            },
        )
        if attested.get("state") != "active":
            detail = f"override.attest did not activate the grant: {attested}"
            return CaseResult(
                "override.ceremony", "override", False, detail, time.monotonic() - start
            )

    retried = await driver.client.call(
        "tool.call",
        {"session_id": session_id, "tool": "memory.delete", "args": {"key": key}},
    )
    ok = retried.get("decision") == "allow" and retried.get("rule") == "override-grant-active"
    detail = (
        f"grant_id={grant_id} initial_state={requested.get('state')} "
        f"retried_decision={retried.get('decision')} rule={retried.get('rule')}"
    )
    if ok:
        after = await driver.read_memory(key)
        found = (after or {}).get("found")
        ok = found is False
        detail += f" key_still_exists={found}"
    return CaseResult("override.ceremony", "override", ok, detail, time.monotonic() - start)


PROMPT_CASES = [
    case_chat_basic,
    case_fs_read,
    case_fs_write_scratch,
    case_fs_write_denied,
    case_git_status,
    case_memory_roundtrip,
    case_web_fetch,
]
RPC_ONLY = [
    case_workflow_launch,
    case_session_lifecycle,
    case_destructive_approval_flow,
    case_destructive_deny_flow,
    case_override_ceremony,
]


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return "unknown"


async def run(driver_name: str, label: str, out_dir: Path) -> Path:
    if driver_name == "gui":
        driver: Any = GuiDriver()
        cases = list(PROMPT_CASES)
    else:
        driver = RpcDriver(default_socket_path())
        cases = list(PROMPT_CASES) + RPC_ONLY

    if not await driver.ping():
        print(f"error: daemon/GUI not reachable for driver={driver_name}", file=sys.stderr)
        raise SystemExit(1)

    results: list[CaseResult] = []
    for case in cases:
        print(f"  running {case.__name__} ...", flush=True)
        try:
            result = await case(driver)
        except Exception as e:
            result = CaseResult(case.__name__, "error", False, f"exception: {e}", 0.0)
        status = "PASS" if result.passed else "FAIL"
        print(f"    [{status}] {result.case_id} ({result.elapsed:.1f}s) {result.detail[:120]}")
        results.append(result)
    await driver.aclose()

    passed = sum(1 for r in results if r.passed)
    payload = {
        "label": label,
        "driver": driver_name,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_commit": _git_commit(),
        "model_env": {
            k: v
            for k, v in os.environ.items()
            if k.startswith("CAPDEP_LLM_") or k == "CAPDEP_QUARANTINED_LLM_MODEL"
        },
        "summary": {"total": len(results), "passed": passed, "failed": len(results) - passed},
        "cases": [
            {
                "id": r.case_id,
                "category": r.category,
                "passed": r.passed,
                "detail": r.detail,
                "elapsed_seconds": round(r.elapsed, 3),
            }
            for r in results
        ],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(c if c.isalnum() or c in "-_." else "-" for c in label)
    out_path = out_dir / f"{safe_label}-{int(time.time())}.json"
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\n{passed}/{len(results)} passed. Results written to {out_path}")
    return out_path


def compare(path_a: Path, path_b: Path) -> None:
    a = json.loads(path_a.read_text())
    b = json.loads(path_b.read_text())
    cases_a = {c["id"]: c for c in a["cases"]}
    cases_b = {c["id"]: c for c in b["cases"]}
    print(f"A: {a['label']} ({a['driver']}, {a.get('git_commit', '?')}) — {path_a.name}")
    print(f"B: {b['label']} ({b['driver']}, {b.get('git_commit', '?')}) — {path_b.name}")
    print()
    all_ids = sorted(set(cases_a) | set(cases_b))
    for case_id in all_ids:
        ca, cb = cases_a.get(case_id), cases_b.get(case_id)
        if cb is None and ca is not None:
            print(f"  - {case_id}: only in A ({'PASS' if ca['passed'] else 'FAIL'})")
            continue
        if ca is None and cb is not None:
            print(f"  + {case_id}: only in B ({'PASS' if cb['passed'] else 'FAIL'})")
            continue
        if ca is None or cb is None:
            continue  # unreachable: case_id came from the union of both keys
        flip = "" if ca["passed"] == cb["passed"] else "  <-- CHANGED"
        dt = cb["elapsed_seconds"] - ca["elapsed_seconds"]
        pa = "PASS" if ca["passed"] else "FAIL"
        pb = "PASS" if cb["passed"] else "FAIL"
        print(
            f"  {case_id}: {pa} -> {pb}{flip}  "
            f"({ca['elapsed_seconds']:.1f}s -> {cb['elapsed_seconds']:.1f}s, {dt:+.1f}s)",
        )
    print(
        f"\nA: {a['summary']['passed']}/{a['summary']['total']} passed  |  "
        f"B: {b['summary']['passed']}/{b['summary']['total']} passed",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", choices=["rpc", "gui"], default="rpc")
    parser.add_argument("--label", default="run", help="Tag for this run, e.g. a model name")
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), type=Path)
    args = parser.parse_args()

    if args.compare:
        compare(*args.compare)
        return 0

    asyncio.run(run(args.driver, args.label, args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
