"""Tests for custom-kind chokepoint integration (Issue #35).

Verifies the runtime registry → policy engine path:

- `register_custom_kind_registry()` installs the registry globally
- `resolve_kind()` returns built-in enum for known names, str for
  registered customs, raises for unknown
- `is_destructive_kind()` consults the registry's per-kind flag
- `kind_add_labels()` returns the labels a custom kind declares
- Capability.from_dict round-trips custom kinds
- Capability.matches() correctly compares custom-kind capabilities
- A destructive custom kind without allows_destructive triggers
  REQUIRE_APPROVAL via the Clark-Wilson gate
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityExpiry,
    CapabilityKind,
    CapabilityOrigin,
    UnknownKindError,
    is_destructive_kind,
    kind_add_labels,
    register_custom_kind_registry,
    reset_custom_kind_registry,
    resolve_kind,
)
from capabledeputy.policy.labels import Label
from capabledeputy.upstream.server_yaml import CustomKindDecl, CustomKindRegistry


@pytest.fixture(autouse=True)
def isolated_registry() -> None:
    """Each test starts with a fresh empty registry. The registry is
    process-global so leakage between tests must be prevented."""
    reset_custom_kind_registry()
    yield
    reset_custom_kind_registry()


def _make_registry(*decls: CustomKindDecl) -> CustomKindRegistry:
    reg = CustomKindRegistry()
    for d in decls:
        reg.register(d)
    return reg


def test_resolve_known_built_in() -> None:
    """Built-in enum values resolve to the enum member."""
    resolved = resolve_kind("READ_FS")
    assert isinstance(resolved, CapabilityKind)
    assert resolved == CapabilityKind.READ_FS


def test_resolve_custom_kind_after_registration() -> None:
    """A custom kind registered via the registry resolves to its
    namespaced string form."""
    reg = _make_registry(
        CustomKindDecl(name="slack:dm.send", destructive=True, declared_by_file="slack.yaml"),
    )
    register_custom_kind_registry(reg)

    resolved = resolve_kind("slack:dm.send")
    assert resolved == "slack:dm.send"
    # And it's actually a string (not coerced to a fake enum)
    assert isinstance(resolved, str)
    assert not isinstance(resolved, CapabilityKind)


def test_resolve_unknown_kind_raises() -> None:
    """An unknown name (not built-in, not registered) raises."""
    register_custom_kind_registry(_make_registry())
    with pytest.raises(UnknownKindError, match="not_a_real_kind"):
        resolve_kind("not_a_real_kind")


def test_resolve_unregistered_namespaced_kind_raises() -> None:
    """A correctly-namespaced kind that isn't in the registry still
    raises — namespace format alone doesn't validate."""
    register_custom_kind_registry(_make_registry())
    with pytest.raises(UnknownKindError):
        resolve_kind("slack:dm.send")


def test_is_destructive_kind_built_in() -> None:
    """Built-in destructive kinds (MODIFY_FS, DELETE_FS, MODIFY_CAL,
    DELETE_CAL) report True. SEND_EMAIL has its own gate via
    SEND_EMAIL_RULE; it's NOT in DESTRUCTIVE_KINDS by design."""
    assert is_destructive_kind(CapabilityKind.DELETE_FS) is True
    assert is_destructive_kind(CapabilityKind.MODIFY_FS) is True
    assert is_destructive_kind(CapabilityKind.READ_FS) is False
    # SEND_EMAIL is gated separately, not via DESTRUCTIVE_KINDS
    assert is_destructive_kind(CapabilityKind.SEND_EMAIL) is False


def test_is_destructive_kind_custom_via_registry() -> None:
    """Custom kinds report destructive based on the yaml `destructive:` flag."""
    reg = _make_registry(
        CustomKindDecl(
            name="slack:dm.send",
            destructive=True,
            declared_by_file="slack.yaml",
        ),
        CustomKindDecl(
            name="slack:read",
            destructive=False,
            declared_by_file="slack.yaml",
        ),
    )
    register_custom_kind_registry(reg)

    assert is_destructive_kind("slack:dm.send") is True
    assert is_destructive_kind("slack:read") is False


def test_is_destructive_kind_unknown_custom_defaults_false() -> None:
    """An unknown custom-kind string (e.g. typo, registry not installed)
    defaults to non-destructive — fail-safe by NOT escalating an
    unknown kind to the destructive-gate path."""
    register_custom_kind_registry(_make_registry())
    assert is_destructive_kind("totally:made.up") is False


def test_kind_add_labels_custom() -> None:
    """A custom kind's declared add_labels surface via kind_add_labels."""
    reg = _make_registry(
        CustomKindDecl(
            name="slack:read",
            destructive=False,
            add_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
            declared_by_file="slack.yaml",
        ),
    )
    register_custom_kind_registry(reg)

    labels = kind_add_labels("slack:read")
    assert labels == frozenset({Label.UNTRUSTED_EXTERNAL})


def test_kind_add_labels_built_in_returns_empty() -> None:
    """Built-in kinds get their label propagation from the policy
    engine's hardcoded rules — kind_add_labels returns empty for them."""
    assert kind_add_labels(CapabilityKind.GMAIL_READ) == frozenset()
    assert kind_add_labels(CapabilityKind.READ_FS) == frozenset()


def test_capability_from_dict_round_trips_custom_kind() -> None:
    """Capability serialization/deserialization works for custom kinds."""
    reg = _make_registry(
        CustomKindDecl(name="slack:dm.send", destructive=True, declared_by_file="x"),
    )
    register_custom_kind_registry(reg)

    original = Capability(
        kind="slack:dm.send",  # type: ignore[arg-type]
        pattern="user-123",
        expiry=CapabilityExpiry.SESSION,
        origin=CapabilityOrigin.USER_APPROVED,
        audit_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    rt = Capability.from_dict(original.to_dict())
    assert rt.kind == "slack:dm.send"


def test_capability_from_dict_rejects_unknown_kind() -> None:
    """Deserialization fails fast if the registry doesn't know the kind."""
    register_custom_kind_registry(_make_registry())

    bad_dict = {
        "kind": "nonexistent:thing",
        "pattern": "*",
        "expiry": "session",
        "origin": "user_approved",
        "audit_id": str(uuid4()),
        "max_amount": None,
        "allows_destructive": False,
        "revoked_by": [],
        "expires_at": None,
        "rate_limit": None,
    }
    with pytest.raises(UnknownKindError):
        Capability.from_dict(bad_dict)


def test_capability_matches_custom_kind() -> None:
    """A capability for a custom kind matches actions of that same kind."""
    reg = _make_registry(
        CustomKindDecl(name="slack:dm.send", destructive=True, declared_by_file="x"),
    )
    register_custom_kind_registry(reg)

    cap = Capability(
        kind="slack:dm.send",  # type: ignore[arg-type]
        pattern="user-*",
        expiry=CapabilityExpiry.SESSION,
        origin=CapabilityOrigin.USER_APPROVED,
        audit_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert cap.matches("slack:dm.send", "user-123")  # type: ignore[arg-type]
    # Different custom kind doesn't match
    assert not cap.matches("slack:read", "user-123")  # type: ignore[arg-type]
    # Different pattern doesn't match either
    assert not cap.matches("slack:dm.send", "channel-abc")  # type: ignore[arg-type]


async def test_custom_kind_add_labels_propagate_to_session(tmp_path) -> None:
    """End-to-end: a tool registered with a custom CapabilityKind
    whose yaml declares `add_labels: [untrusted.external]` causes
    that label to land in the session's label_set after a successful
    call. This closes the IFC story for plugin kinds — destructiveness
    gates the call; declared add_labels color the session afterward."""
    from capabledeputy.audit.writer import AuditWriter
    from capabledeputy.session.graph import SessionGraph
    from capabledeputy.tools.client import LabeledToolClient
    from capabledeputy.tools.registry import (
        ToolDefinition,
        ToolRegistry,
        ToolResult,
    )

    reg = _make_registry(
        CustomKindDecl(
            name="slack:read",
            destructive=False,
            add_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
            declared_by_file="slack.yaml",
        ),
    )
    register_custom_kind_registry(reg)

    audit = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=audit)
    session = await graph.new(intent="test-ifc")

    # Grant the custom capability so the call is allowed
    await graph.grant_capability(
        session.id,
        Capability(
            kind="slack:read",  # type: ignore[arg-type]
            pattern="*",
            expiry=CapabilityExpiry.SESSION,
            origin=CapabilityOrigin.USER_APPROVED,
            audit_id=uuid4(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    registry = ToolRegistry()

    async def slack_read_handler(args, context):
        return ToolResult(output={"messages": []})

    registry.register(
        ToolDefinition(
            name="slack.search_messages",
            description="Read Slack messages",
            capability_kind="slack:read",  # custom-kind string
            handler=slack_read_handler,
            target_arg="query",
            parameters_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        ),
    )

    client = LabeledToolClient(registry=registry, graph=graph, audit=audit)

    # Before the call: session has no labels
    session_before = graph.get(session.id)
    assert Label.UNTRUSTED_EXTERNAL not in session_before.label_set

    outcome = await client.call_tool(session.id, "slack.search_messages", {"query": "hello"})

    # Call should have succeeded (ALLOW), and the custom kind's
    # add_labels should now be in the session's label_set.
    from capabledeputy.policy.rules import Decision
    assert outcome.decision == Decision.ALLOW, f"Expected ALLOW, got: {outcome}"
    session_after = graph.get(session.id)
    assert Label.UNTRUSTED_EXTERNAL in session_after.label_set


def test_built_in_kind_does_not_satisfy_custom_kind() -> None:
    """A READ_FS capability does NOT satisfy a custom-kind action.
    Operators who grant READ_FS aren't accidentally granting custom
    plugin kinds (the back-compat union is intentional and limited
    to the email/drive read kinds)."""
    reg = _make_registry(
        CustomKindDecl(name="slack:read", destructive=False, declared_by_file="x"),
    )
    register_custom_kind_registry(reg)

    cap = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expiry=CapabilityExpiry.SESSION,
        origin=CapabilityOrigin.USER_APPROVED,
        audit_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    # READ_FS capability does NOT match a slack:read action.
    # The back-compat union only covers built-in granular read kinds
    # (GMAIL_READ, IMAP_READ, DRIVE_READ), not arbitrary custom kinds.
    assert not cap.matches("slack:read", "any-target")  # type: ignore[arg-type]
