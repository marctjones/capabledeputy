"""Destructive-operation gate (DESIGN.md §7.5).

Read = generally OK. Create (write that doesn't modify existing state)
= generally OK. Modify and delete = REQUIRE_APPROVAL by default. The
gate fires structurally on the action's CapabilityKind, regardless of
labels.

Verified properties:
  - Reads ALLOW unconditionally (no gate).
  - Creates (CREATE_FS / CREATE_CAL) ALLOW unconditionally.
  - Modifies (MODIFY_FS / MODIFY_CAL) → REQUIRE_APPROVAL by default.
  - Deletes (DELETE_FS / DELETE_CAL) → REQUIRE_APPROVAL by default.
  - `allows_destructive=True` on the matching capability bypasses the
    gate (user pre-authorized this compartment for destructive ops).
  - Backward-compat: a legacy `WRITE_FS` capability + `allows_destructive=True`
    matches MODIFY_FS / DELETE_FS actions and bypasses the gate.
  - The destructive gate composes with conflict rules: if a conflict
    rule fires (DENY or REQUIRE_APPROVAL), it short-circuits before
    the destructive gate is consulted.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.engine import DESTRUCTIVE_OP_RULE, decide
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier


def _action(kind: CapabilityKind, target: str = "/x") -> Action:
    return Action(kind=kind, target=target)


def test_read_allows_unconditionally() -> None:
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    result = decide(
        frozenset({cap}),
        _action(CapabilityKind.READ_FS),
    )
    assert result.decision == Decision.ALLOW


def test_create_allows_without_destructive_flag() -> None:
    """CREATE_FS is non-destructive — gate doesn't fire."""
    cap = Capability(kind=CapabilityKind.CREATE_FS, pattern="*")
    assert cap.allows_destructive is False  # default OFF
    result = decide(
        frozenset({cap}),
        _action(CapabilityKind.CREATE_FS),
    )
    assert result.decision == Decision.ALLOW


def test_modify_requires_approval_by_default() -> None:
    cap = Capability(kind=CapabilityKind.MODIFY_FS, pattern="*")
    result = decide(
        frozenset({cap}),
        _action(CapabilityKind.MODIFY_FS),
    )
    assert result.decision == Decision.REQUIRE_APPROVAL
    assert result.rule == DESTRUCTIVE_OP_RULE


def test_delete_requires_approval_by_default() -> None:
    cap = Capability(kind=CapabilityKind.DELETE_FS, pattern="*")
    result = decide(
        frozenset({cap}),
        _action(CapabilityKind.DELETE_FS),
    )
    assert result.decision == Decision.REQUIRE_APPROVAL
    assert result.rule == DESTRUCTIVE_OP_RULE


def test_allows_destructive_bypasses_gate() -> None:
    cap = Capability(
        kind=CapabilityKind.MODIFY_FS,
        pattern="*",
        allows_destructive=True,
    )
    result = decide(
        frozenset({cap}),
        _action(CapabilityKind.MODIFY_FS),
    )
    assert result.decision == Decision.ALLOW


def test_legacy_write_fs_capability_still_matches_granular_kinds() -> None:
    """Backward-compat: a Capability(WRITE_FS, allows_destructive=True)
    matches MODIFY_FS / DELETE_FS actions via the union semantics in
    Capability.matches."""
    cap = Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="*",
        allows_destructive=True,
    )
    for action_kind in (
        CapabilityKind.CREATE_FS,
        CapabilityKind.MODIFY_FS,
        CapabilityKind.DELETE_FS,
    ):
        result = decide(
            frozenset({cap}),
            _action(action_kind),
        )
        assert result.decision == Decision.ALLOW, f"WRITE_FS should cover {action_kind}"


def test_legacy_write_fs_without_destructive_flag_still_gates() -> None:
    """A WRITE_FS capability without allows_destructive=True hits the
    destructive gate when used for MODIFY/DELETE."""
    cap = Capability(kind=CapabilityKind.WRITE_FS, pattern="*")
    # CREATE — no gate.
    result_create = decide(
        frozenset({cap}),
        _action(CapabilityKind.CREATE_FS),
    )
    assert result_create.decision == Decision.ALLOW
    # MODIFY — gate fires.
    result_modify = decide(
        frozenset({cap}),
        _action(CapabilityKind.MODIFY_FS),
    )
    assert result_modify.decision == Decision.REQUIRE_APPROVAL


def test_conflict_rule_short_circuits_destructive_gate() -> None:
    """A health-meets-egress rule fires before the destructive-op
    gate. The DENY decision wins; the gate never matters."""
    cap = Capability(
        kind=CapabilityKind.SEND_EMAIL,
        pattern="*",
    )
    result = decide(
        frozenset({cap}),
        Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com"),
        labels=LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )
    assert result.decision == Decision.DENY
    assert result.rule == "health-meets-egress"


async def test_agent_loop_modify_blocked_by_default(tmp_path: Path) -> None:
    """End-to-end: an LLM that tries memory.update without an
    allows_destructive=True capability gets REQUIRE_APPROVAL."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient(
            [
                LLMResponse(
                    content="Trying to update.",
                    tool_calls=(
                        ToolCall(
                            id="u1",
                            name="memory.update",
                            args={"key": "notes.x", "value": "new value"},
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(content="blocked.", finish_reason=FinishReason.STOP),
            ],
        ),
    )
    await app.startup()
    from capabledeputy.policy.labels import LabelState

    app.memory.write("notes.x", "original", LabelState())

    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.MODIFY_FS, pattern="*")  # destructive default
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "Update notes.x"},
    )
    [outcome] = result["tool_outcomes"]
    assert outcome["decision"] == "require_approval"
    assert outcome["rule"] == DESTRUCTIVE_OP_RULE
    # Memory unchanged — the gate fired before the handler ran.
    entry = app.memory.read("notes.x")
    assert entry is not None
    assert entry.value == "original"


async def test_agent_loop_modify_allowed_with_destructive_flag(tmp_path: Path) -> None:
    """Counterpart: same workflow but the capability has
    allows_destructive=True. The update succeeds without an approval."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient(
            [
                LLMResponse(
                    content="updating",
                    tool_calls=(
                        ToolCall(
                            id="u1",
                            name="memory.update",
                            args={"key": "notes.x", "value": "new value"},
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(content="updated", finish_reason=FinishReason.STOP),
            ],
        ),
    )
    await app.startup()
    from capabledeputy.policy.labels import LabelState

    app.memory.write("notes.x", "original", LabelState())

    s = await app.graph.new()
    cap = Capability(
        kind=CapabilityKind.MODIFY_FS,
        pattern="*",
        allows_destructive=True,
    )
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "Update notes.x"},
    )
    [outcome] = result["tool_outcomes"]
    assert outcome["decision"] == "allow"
    entry = app.memory.read("notes.x")
    assert entry is not None
    assert entry.value == "new value"


async def test_agent_loop_create_then_delete_path(tmp_path: Path) -> None:
    """Create succeeds without approval; subsequent delete needs it.

    This is the canonical "writing is OK if it doesn't modify, but
    deleting is suspect" workflow.
    """
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient(
            [
                LLMResponse(
                    content="creating",
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name="memory.create",
                            args={"key": "notes.draft", "value": "v1"},
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(
                    content="now deleting",
                    tool_calls=(
                        ToolCall(
                            id="d1",
                            name="memory.delete",
                            args={"key": "notes.draft"},
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(content="done", finish_reason=FinishReason.STOP),
            ],
        ),
    )
    await app.startup()

    s = await app.graph.new()
    caps = frozenset(
        {
            Capability(kind=CapabilityKind.CREATE_FS, pattern="*"),
            Capability(kind=CapabilityKind.DELETE_FS, pattern="*"),
        },
    )
    app.graph._sessions[s.id] = replace(s, capability_set=caps)

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "Create notes.draft and delete it."},
    )
    decisions = [o["decision"] for o in result["tool_outcomes"]]
    rules = [o["rule"] for o in result["tool_outcomes"] if o["rule"]]
    # Create allowed; delete required approval.
    assert decisions[0] == "allow"
    assert decisions[1] == "require_approval"
    assert DESTRUCTIVE_OP_RULE in rules
    # Memory still has the draft — delete didn't fire.
    assert app.memory.read("notes.draft") is not None
