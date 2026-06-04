"""Roadmap v2 #4 — per-(group, principal) reputation tier.

Three tiers — `unproven` (default), `well-tested`, `trusted` —
inform the chat REPL's approval-card UX. Tier promotion is
operator-only. Tests cover:

  - tier_for / set_tier round-trip
  - default tier is unproven
  - set_tier requires membership (Principle VI fail-closed)
  - invalid tier value raises
  - effective_tier_for takes the max across group memberships
  - YAML save/load round-trips tiers
  - tiers cleared when their member is removed
  - daemon RPCs: tier, effective_tier, promote, list (with tier
    payload), aggregate_audit
  - REPL tier-aware payload preview (60/200/subject-only)
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.relationships import (
    DEFAULT_TIER,
    RelationshipGroup,
    RelationshipGroupError,
    RelationshipGroups,
    load,
    save,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _registry_with(members: dict[str, list[str]]) -> RelationshipGroups:
    return RelationshipGroups(
        groups={
            gid: RelationshipGroup(
                group_id=gid,
                member_principal_ids=frozenset(pids),
            )
            for gid, pids in members.items()
        },
    )


# --- Core tier model -----------------------------------------------------


def test_default_tier_is_unproven() -> None:
    registry = _registry_with({"family": ["spouse@example.com"]})
    assert registry.tier_for("family", "spouse@example.com") == "unproven"
    assert DEFAULT_TIER == "unproven"


def test_set_tier_round_trip() -> None:
    registry = _registry_with({"family": ["spouse@example.com"]})
    registry.set_tier("family", "spouse@example.com", "well-tested")
    assert registry.tier_for("family", "spouse@example.com") == "well-tested"
    registry.set_tier("family", "spouse@example.com", "trusted")
    assert registry.tier_for("family", "spouse@example.com") == "trusted"


def test_set_tier_to_default_clears_the_entry() -> None:
    """Demotion to the default doesn't bloat the registry —
    storing the default explicitly would be a phantom row."""
    registry = _registry_with({"family": ["spouse@example.com"]})
    registry.set_tier("family", "spouse@example.com", "trusted")
    registry.set_tier("family", "spouse@example.com", "unproven")
    assert ("family", "spouse@example.com") not in registry.tiers


def test_set_tier_requires_membership() -> None:
    """Principle VI fail-closed: can't promote a non-member."""
    registry = _registry_with({"family": ["spouse@example.com"]})
    with pytest.raises(RelationshipGroupError, match="not a member"):
        registry.set_tier("family", "stranger@example.com", "trusted")


def test_set_tier_rejects_invalid_value() -> None:
    registry = _registry_with({"family": ["spouse@example.com"]})
    with pytest.raises(RelationshipGroupError, match="invalid reputation tier"):
        registry.set_tier("family", "spouse@example.com", "platinum")


def test_effective_tier_takes_max_across_groups() -> None:
    """When a principal is in two groups with differing tiers,
    the higher one wins. The operator already trusts them at
    each tier separately, so the most-trusted reading applies."""
    registry = _registry_with(
        {
            "family": ["alice@example.com"],
            "work": ["alice@example.com"],
        },
    )
    registry.set_tier("family", "alice@example.com", "trusted")
    registry.set_tier("work", "alice@example.com", "well-tested")
    assert registry.effective_tier_for("alice@example.com") == "trusted"


def test_effective_tier_for_non_member_is_default() -> None:
    registry = _registry_with({"family": ["spouse@example.com"]})
    assert registry.effective_tier_for("stranger@example.com") == "unproven"


def test_remove_member_drops_tier_entry() -> None:
    """Lingering tiers after removal would mislead a re-add —
    the operator might re-add with no intent to grant the old
    tier. Drop the row at removal."""
    registry = _registry_with({"family": ["spouse@example.com"]})
    registry.set_tier("family", "spouse@example.com", "trusted")
    assert registry.remove_member("family", "spouse@example.com")
    assert ("family", "spouse@example.com") not in registry.tiers


# --- YAML round-trip -----------------------------------------------------


def test_yaml_round_trip_preserves_tiers(tmp_path: Path) -> None:
    registry = _registry_with(
        {
            "family": ["spouse@example.com", "kid@example.com"],
        },
    )
    registry.set_tier("family", "spouse@example.com", "trusted")
    registry.set_tier("family", "kid@example.com", "well-tested")
    path = tmp_path / "rg.yaml"
    save(registry, path)
    reloaded = load(path)
    assert reloaded.tier_for("family", "spouse@example.com") == "trusted"
    assert reloaded.tier_for("family", "kid@example.com") == "well-tested"


def test_yaml_load_drops_tier_for_non_member(tmp_path: Path) -> None:
    """A hand-edited YAML that has a tier for a non-member silently
    drops the tier row. The membership decides authority; a
    phantom tier without membership would be a footgun."""
    path = tmp_path / "rg.yaml"
    path.write_text(
        "groups:\n"
        "  - group_id: family\n"
        "    member_principal_ids:\n"
        "      - spouse@example.com\n"
        "    member_tiers:\n"
        "      stranger@example.com: trusted\n",
    )
    reloaded = load(path)
    assert reloaded.tier_for("family", "stranger@example.com") == "unproven"


def test_yaml_load_rejects_invalid_tier(tmp_path: Path) -> None:
    path = tmp_path / "rg.yaml"
    path.write_text(
        "groups:\n"
        "  - group_id: family\n"
        "    member_principal_ids:\n"
        "      - spouse@example.com\n"
        "    member_tiers:\n"
        "      spouse@example.com: platinum\n",
    )
    with pytest.raises(RelationshipGroupError, match="invalid reputation tier"):
        load(path)


# --- Daemon RPC surface --------------------------------------------------


def _stub_app(
    registry: RelationshipGroups,
    path: Path | None = None,
    audit: AuditWriter | None = None,
):
    """Minimal duck-typed App for the handler factory."""

    class _PCtx:
        relationship_groups = registry
        relationship_groups_path = path

    class _App:
        policy_context = _PCtx()

        def __init__(self) -> None:
            self.audit = audit

    return _App()


@pytest.mark.anyio
async def test_rpc_tier_returns_default_for_unset() -> None:
    from capabledeputy.daemon.relationship_handlers import (
        make_relationship_handlers,
    )

    registry = _registry_with({"family": ["spouse@example.com"]})
    handlers = make_relationship_handlers(_stub_app(registry))
    result = await handlers["relationship_group.tier"](
        {"group_id": "family", "principal_id": "spouse@example.com"},
    )
    assert result["tier"] == "unproven"


@pytest.mark.anyio
async def test_rpc_promote_persists_and_records_previous_tier(
    tmp_path: Path,
) -> None:
    from capabledeputy.daemon.relationship_handlers import (
        make_relationship_handlers,
    )

    registry = _registry_with({"family": ["spouse@example.com"]})
    yaml_path = tmp_path / "rg.yaml"
    save(registry, yaml_path)  # baseline persisted state
    handlers = make_relationship_handlers(_stub_app(registry, path=yaml_path))
    result = await handlers["relationship_group.promote"](
        {
            "group_id": "family",
            "principal_id": "spouse@example.com",
            "tier": "trusted",
        },
    )
    assert result["previous_tier"] == "unproven"
    assert result["tier"] == "trusted"
    assert result["persisted"] is True
    # Survives reload
    reloaded = load(yaml_path)
    assert reloaded.tier_for("family", "spouse@example.com") == "trusted"


@pytest.mark.anyio
async def test_rpc_promote_rejects_non_member() -> None:
    from capabledeputy.daemon.relationship_handlers import (
        make_relationship_handlers,
    )

    registry = _registry_with({"family": ["spouse@example.com"]})
    handlers = make_relationship_handlers(_stub_app(registry))
    result = await handlers["relationship_group.promote"](
        {
            "group_id": "family",
            "principal_id": "stranger@example.com",
            "tier": "trusted",
        },
    )
    assert "error" in result
    assert "not a member" in result["error"]
    assert result["persisted"] is False


@pytest.mark.anyio
async def test_rpc_list_includes_member_tiers() -> None:
    from capabledeputy.daemon.relationship_handlers import (
        make_relationship_handlers,
    )

    registry = _registry_with({"family": ["spouse@example.com"]})
    registry.set_tier("family", "spouse@example.com", "trusted")
    handlers = make_relationship_handlers(_stub_app(registry))
    result = await handlers["relationship_group.list"]({})
    assert result["groups"][0]["member_tiers"] == {
        "spouse@example.com": "trusted",
    }


@pytest.mark.anyio
async def test_rpc_effective_tier_resolves_across_groups() -> None:
    from capabledeputy.daemon.relationship_handlers import (
        make_relationship_handlers,
    )

    registry = _registry_with(
        {
            "family": ["alice@example.com"],
            "work": ["alice@example.com"],
        },
    )
    registry.set_tier("work", "alice@example.com", "well-tested")
    handlers = make_relationship_handlers(_stub_app(registry))
    result = await handlers["relationship_group.effective_tier"](
        {"principal_id": "alice@example.com"},
    )
    assert result["tier"] == "well-tested"
    assert sorted(result["groups"]) == ["family", "work"]


@pytest.mark.anyio
async def test_rpc_aggregate_audit_counts_approved_and_denied(
    tmp_path: Path,
) -> None:
    """The aggregate joins APPROVAL_REQUESTED (for target match)
    with APPROVAL_APPROVED/DENIED (for the decision tally)."""
    from capabledeputy.daemon.relationship_handlers import (
        make_relationship_handlers,
    )

    audit_path = tmp_path / "audit.jsonl"
    writer = AuditWriter(audit_path)
    session = uuid4()
    # Two approval-request cycles for alice@example.com — one
    # approved, one denied — plus one for someone else.
    for approval_id, target, decision in [
        (1, "alice@example.com", "approved"),
        (2, "alice@example.com", "denied"),
        (3, "bob@example.com", "approved"),
    ]:
        await writer.write(
            Event(
                event_type=EventType.APPROVAL_REQUESTED,
                session_id=session,
                payload={
                    "approval_id": approval_id,
                    "action": "SEND_EMAIL",
                    "target": target,
                    "labels_in": [],
                    "justification": "",
                },
            ),
        )
        etype = EventType.APPROVAL_APPROVED if decision == "approved" else EventType.APPROVAL_DENIED
        await writer.write(
            Event(
                event_type=etype,
                session_id=session,
                payload={"approval_id": approval_id, "decided_by": "user"},
            ),
        )

    registry = _registry_with({"family": ["alice@example.com"]})
    handlers = make_relationship_handlers(_stub_app(registry, audit=writer))
    result = await handlers["relationship_group.aggregate_audit"](
        {"principal_id": "alice@example.com"},
    )
    assert result == {
        "principal_id": "alice@example.com",
        "approved": 1,
        "denied": 1,
    }


# --- REPL tier-aware payload preview -------------------------------------


def test_tier_payload_preview_unproven_caps_at_60() -> None:
    from capabledeputy.cli.chat import _tier_payload_preview

    payload = "x" * 200
    preview = _tier_payload_preview(payload, "unproven")
    assert preview.endswith("…")
    # 60-char head + ellipsis
    assert len(preview) == 61


def test_tier_payload_preview_well_tested_caps_at_200() -> None:
    from capabledeputy.cli.chat import _tier_payload_preview

    payload = "x" * 500
    preview = _tier_payload_preview(payload, "well-tested")
    assert preview.endswith("…")
    assert len(preview) == 201


def test_tier_payload_preview_trusted_shows_first_line_only() -> None:
    from capabledeputy.cli.chat import _tier_payload_preview

    payload = "Subject: hi alice\nBody body body body body."
    preview = _tier_payload_preview(payload, "trusted")
    assert "Subject: hi alice" in preview
    assert "Body" not in preview
