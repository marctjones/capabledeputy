"""Cookbook §4 #6 — first-action-of-kind prompt.

Tests cover:
  - Session field defaults False; dict round-trip preserves
  - default-tolerant from_dict for pre-cookbook sessions
  - Engine: ALLOW becomes REQUIRE_APPROVAL when first_use_prompt_enabled
    AND kind is promptable AND not in used_kinds
  - Subsequent dispatch (kind already in used_kinds) passes through
  - Read kinds (GMAIL_READ, READ_FS) NEVER prompt — too noisy
  - Flag OFF → no prompting even on first use
  - Non-ALLOW outcomes pass through unchanged (we don't make denies
    even stricter)
  - SessionGraph.set_first_use_prompts is idempotent
  - Cautious purpose auto-enables; balanced doesn't
  - Persists across reload
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.engine import (
    _PROMPTABLE_FIRST_USE_KINDS,
    FIRST_USE_OF_KIND_RULE,
    decide,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.model import Session

# --- Model ----------------------------------------------------------------


def test_session_first_use_default_false() -> None:
    assert Session.new().first_use_prompt_enabled is False


def test_session_round_trip_preserves_flag() -> None:
    s = Session.new(first_use_prompt_enabled=True)
    d = s.to_dict()
    assert d["first_use_prompt_enabled"] is True
    s2 = Session.from_dict(d)
    assert s2.first_use_prompt_enabled is True


def test_legacy_dict_defaults_to_false() -> None:
    """Pre-cookbook session rows have no flag — must default False
    to preserve back-compat."""
    d = Session.new().to_dict()
    d.pop("first_use_prompt_enabled")
    s = Session.from_dict(d)
    assert s.first_use_prompt_enabled is False


# --- Engine ---------------------------------------------------------------


def _wide_cap(kind: CapabilityKind = CapabilityKind.SEND_EMAIL) -> Capability:
    return Capability(kind=kind, pattern="*")


def test_first_use_escalates_allow_to_require_approval() -> None:
    """A session that would have ALLOW-ed a SEND_EMAIL gets
    REQUIRE_APPROVAL on the first use under the new rule. The rule
    name is the FIRST_USE_OF_KIND_RULE constant so tooling can
    recognize and explain the escalation."""
    result = decide(
        frozenset({_wide_cap(CapabilityKind.SEND_EMAIL)}),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        used_kinds=frozenset(),  # never used SEND_EMAIL before
        first_use_prompt_enabled=True,
    )
    assert result.decision == Decision.REQUIRE_APPROVAL
    assert result.rule == FIRST_USE_OF_KIND_RULE


def test_second_use_passes_through() -> None:
    """Once the kind has been used, the prompt doesn't fire again —
    no per-call friction on subsequent dispatches."""
    result = decide(
        frozenset({_wide_cap(CapabilityKind.SEND_EMAIL)}),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        used_kinds=frozenset({CapabilityKind.SEND_EMAIL}),
        first_use_prompt_enabled=True,
    )
    assert result.decision == Decision.ALLOW


def test_read_kind_never_prompts() -> None:
    """Read kinds (READ_FS, GMAIL_READ, etc.) are excluded from the
    promptable set — they don't change state and would create
    fatigue from every new mailbox label / file path."""
    assert CapabilityKind.READ_FS not in _PROMPTABLE_FIRST_USE_KINDS
    assert CapabilityKind.GMAIL_READ not in _PROMPTABLE_FIRST_USE_KINDS
    result = decide(
        frozenset({_wide_cap(CapabilityKind.READ_FS)}),
        Action(kind=CapabilityKind.READ_FS, target="/home/x"),
        used_kinds=frozenset(),
        first_use_prompt_enabled=True,
    )
    assert result.decision == Decision.ALLOW


def test_flag_off_no_prompt() -> None:
    """Default-off path: an opt-out session ALLOWs even on first
    use. Pre-existing back-compat preserved."""
    result = decide(
        frozenset({_wide_cap(CapabilityKind.SEND_EMAIL)}),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        used_kinds=frozenset(),
        first_use_prompt_enabled=False,
    )
    assert result.decision == Decision.ALLOW


def test_non_allow_outcomes_pass_through() -> None:
    """A first-use check only fires when the engine was about to
    ALLOW. If the rule composition already DENIES, the first-use
    rule doesn't make it 'more restrictive than DENY' — DENY
    stands."""
    # No capabilities → 'no matching capability' DENY
    result = decide(
        frozenset(),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        used_kinds=frozenset(),
        first_use_prompt_enabled=True,
    )
    assert result.decision == Decision.DENY
    assert result.rule != FIRST_USE_OF_KIND_RULE


def test_promptable_set_covers_destructive_and_egress() -> None:
    """Sanity-check the promptable set — every kind that has egress
    or destructive semantics is included so a misconfigured grant
    surfaces on first use."""
    for k in (
        CapabilityKind.SEND_EMAIL,
        CapabilityKind.QUEUE_PURCHASE,
        CapabilityKind.DELETE_FS,
        CapabilityKind.MODIFY_FS,
        CapabilityKind.DELETE_CAL,
        CapabilityKind.BROWSER_AUTOMATION,
        CapabilityKind.MACOS_AUTOMATION,
        CapabilityKind.EXECUTE_DEVBOX,
    ):
        assert k in _PROMPTABLE_FIRST_USE_KINDS


# --- SessionGraph mutator + auto-default from purpose --------------------


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_set_first_use_prompts_idempotent(tmp_path: Path) -> None:
    """Flipping to the same value is a no-op (no save churn)."""
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)
    s = await graph.new()
    assert s.first_use_prompt_enabled is False
    s2 = await graph.set_first_use_prompts(s.id, False)
    assert s2 is s or s2.first_use_prompt_enabled is False


@pytest.mark.anyio
async def test_set_first_use_prompts_flips_flag(tmp_path: Path) -> None:
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)
    s = await graph.new()
    s2 = await graph.set_first_use_prompts(s.id, True)
    assert s2.first_use_prompt_enabled is True
    s3 = graph.get(s.id)
    assert s3.first_use_prompt_enabled is True


@pytest.mark.anyio
async def test_cautious_purpose_auto_enables_flag(tmp_path: Path) -> None:
    """A session spawned from a cautious-dial Purpose auto-gets the
    flag. Balanced/aggressive purposes leave it off."""
    from capabledeputy.policy.purposes import Purpose, Purposes

    purposes = Purposes(
        purposes={
            "cautious-research": Purpose(
                purpose_id="cautious-research",
                admissible_categories=frozenset({"research"}),
                risk_preference_dial="cautious",
            ),
            "balanced-work": Purpose(
                purpose_id="balanced-work",
                admissible_categories=frozenset({"work"}),
                risk_preference_dial="balanced",
            ),
        },
    )
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer, purposes=purposes)

    s_cautious = await graph.new(purpose_handle="cautious-research")
    assert s_cautious.first_use_prompt_enabled is True

    s_balanced = await graph.new(purpose_handle="balanced-work")
    assert s_balanced.first_use_prompt_enabled is False


# --- Persistence ----------------------------------------------------------


@pytest.mark.anyio
async def test_flag_persists_across_reload(tmp_path: Path) -> None:
    from capabledeputy.session.store import SessionStore

    db_path = tmp_path / "state.db"
    audit_path = tmp_path / "audit.jsonl"
    store = SessionStore(db_path)
    await store.initialize()
    writer = AuditWriter(audit_path)
    graph = SessionGraph(audit=writer, store=store)
    s = await graph.new(intent="persist")
    await graph.set_first_use_prompts(s.id, True)

    store2 = SessionStore(db_path)
    await store2.initialize()
    graph2 = SessionGraph(audit=writer, store=store2)
    await graph2.load()
    reloaded = graph2.get(s.id)
    assert reloaded.first_use_prompt_enabled is True
