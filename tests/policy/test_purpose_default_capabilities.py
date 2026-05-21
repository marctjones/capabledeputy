"""Tests for per-purpose default_capabilities.

Operators declare default capabilities per purpose in
configs/purposes.yaml; SessionGraph.new auto-grants them at spawn
time. This reduces /grant friction for common workflows while
keeping the purpose-based admissibility check authoritative.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.purposes import (
    UNSET_PURPOSE_HANDLE,
    Purpose,
    PurposeError,
    Purposes,
    load,
)
from capabledeputy.session.graph import SessionGraph

# ---------- parser ----------


def test_parse_default_capabilities_minimal(tmp_path: Path) -> None:
    cfg = tmp_path / "purposes.yaml"
    cfg.write_text(
        """
purposes:
  - purpose_id: research
    label: "Research"
    admissible_categories: [research, public]
    default_capabilities:
      - kind: READ_FS
        pattern: "/home/me/research/**"
      - kind: CREATE_FS
        pattern: "/home/me/research/scratch/**"
        allows_destructive: false
""",
        encoding="utf-8",
    )
    purposes = load(cfg)
    p = purposes.get("research")
    assert p is not None
    assert len(p.default_capabilities) == 2
    kinds = {c.kind for c in p.default_capabilities}
    assert kinds == {CapabilityKind.READ_FS, CapabilityKind.CREATE_FS}


def test_parse_default_capabilities_unknown_kind_refused(tmp_path: Path) -> None:
    cfg = tmp_path / "purposes.yaml"
    cfg.write_text(
        """
purposes:
  - purpose_id: bad
    admissible_categories: [public]
    default_capabilities:
      - kind: NOT_A_REAL_KIND
        pattern: "*"
""",
        encoding="utf-8",
    )
    with pytest.raises(PurposeError, match="unknown kind"):
        load(cfg)


def test_parse_default_capabilities_missing_pattern_refused(tmp_path: Path) -> None:
    cfg = tmp_path / "purposes.yaml"
    cfg.write_text(
        """
purposes:
  - purpose_id: bad
    admissible_categories: [public]
    default_capabilities:
      - kind: READ_FS
""",
        encoding="utf-8",
    )
    with pytest.raises(PurposeError, match="missing 'pattern'"):
        load(cfg)


def test_parse_default_capabilities_optional_fields(tmp_path: Path) -> None:
    """All optional Capability fields parse through correctly."""
    cfg = tmp_path / "purposes.yaml"
    cfg.write_text(
        """
purposes:
  - purpose_id: shopping
    admissible_categories: [public]
    default_capabilities:
      - kind: QUEUE_PURCHASE
        pattern: "amazon.com/*"
        max_amount: 5000
""",
        encoding="utf-8",
    )
    purposes = load(cfg)
    cap = purposes.get("shopping").default_capabilities[0]
    assert cap.kind == CapabilityKind.QUEUE_PURCHASE
    assert cap.max_amount == 5000


def test_parse_no_default_capabilities_yields_empty_tuple(tmp_path: Path) -> None:
    cfg = tmp_path / "purposes.yaml"
    cfg.write_text(
        """
purposes:
  - purpose_id: minimal
    admissible_categories: [public]
""",
        encoding="utf-8",
    )
    purposes = load(cfg)
    assert purposes.get("minimal").default_capabilities == ()


# ---------- graph wiring ----------


@pytest.mark.asyncio
async def test_session_born_with_purpose_default_caps(tmp_path: Path) -> None:
    """Session.new applies purpose.default_capabilities at spawn time."""
    from capabledeputy.policy.capabilities import Capability

    purposes = Purposes(
        purposes={
            "research": Purpose(
                purpose_id="research",
                admissible_categories=frozenset({"research"}),
                default_capabilities=(
                    Capability(kind=CapabilityKind.READ_FS, pattern="/home/me/research/**"),
                    Capability(
                        kind=CapabilityKind.CREATE_FS, pattern="/home/me/research/scratch/**"
                    ),
                ),
            ),
        },
    )
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer, purposes=purposes)

    s = await graph.new(purpose_handle="research")
    assert len(s.capability_set) == 2
    kinds = {c.kind for c in s.capability_set}
    assert kinds == {CapabilityKind.READ_FS, CapabilityKind.CREATE_FS}


@pytest.mark.asyncio
async def test_unset_purpose_no_default_caps(tmp_path: Path) -> None:
    """Sessions spawned with UNSET purpose get no auto caps (fail-closed)."""
    from capabledeputy.policy.capabilities import Capability

    purposes = Purposes(
        purposes={
            "research": Purpose(
                purpose_id="research",
                admissible_categories=frozenset({"research"}),
                default_capabilities=(Capability(kind=CapabilityKind.READ_FS, pattern="*"),),
            ),
        },
    )
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer, purposes=purposes)

    s = await graph.new(purpose_handle=UNSET_PURPOSE_HANDLE)
    assert s.capability_set == frozenset()


@pytest.mark.asyncio
async def test_unknown_purpose_no_default_caps(tmp_path: Path) -> None:
    """Unknown purpose handle gets no caps (fail-closed)."""
    purposes = Purposes(purposes={})
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer, purposes=purposes)

    s = await graph.new(purpose_handle="not-a-real-purpose")
    assert s.capability_set == frozenset()


@pytest.mark.asyncio
async def test_default_cap_grant_audited(tmp_path: Path) -> None:
    """Each auto-granted cap emits a CAPABILITY_GRANTED event with
    source=purpose-default so the audit trail shows where it came from."""
    from capabledeputy.policy.capabilities import Capability

    purposes = Purposes(
        purposes={
            "research": Purpose(
                purpose_id="research",
                admissible_categories=frozenset({"research"}),
                default_capabilities=(
                    Capability(kind=CapabilityKind.READ_FS, pattern="/research/**"),
                    Capability(kind=CapabilityKind.CREATE_FS, pattern="/research/scratch/**"),
                ),
            ),
        },
    )
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer, purposes=purposes)

    await graph.new(purpose_handle="research")

    events = await writer.read_all()
    grant_events = [e for e in events if e.event_type == EventType.CAPABILITY_GRANTED]
    assert len(grant_events) == 2
    for e in grant_events:
        assert e.payload.get("source") == "purpose-default"
        assert e.payload.get("purpose_handle") == "research"


@pytest.mark.asyncio
async def test_no_purposes_registry_no_default_caps(tmp_path: Path) -> None:
    """SessionGraph without a Purposes registry behaves identically to
    the old single-arg new() — no auto-caps, no errors."""
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)  # no purposes argument

    s = await graph.new(purpose_handle="research")
    assert s.capability_set == frozenset()
