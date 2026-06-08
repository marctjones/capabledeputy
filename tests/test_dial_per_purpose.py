"""Tests for per-Purpose risk_preference_dial (T118-T121, FR-030 / Q1).

The 2026-05-25 clarification (Q1) bound the risk-preference dial
to the Purpose Handle:
- Each Purpose entry in configs/purposes.yaml carries its own
  `risk_preference_dial: cautious | balanced | permissive`.
- Sessions spawned with a given purpose inherit that purpose's dial
  value as `risk_preference_at_spawn`.
- On `fork`, the child inherits the parent's *resolved* dial (not
  re-resolved from the parent's purpose) — so a dial change to the
  purpose mid-life doesn't retroactively apply to active children
  (SC-002 replayability).
- An entry without an explicit dial falls back to the legacy
  `configs/risk_preference.json`'s value (transitional migration),
  then to the safety default `cautious`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.purposes import (
    _DEFAULT_DIAL_VALUE,
    _VALID_DIAL_VALUES,
    Purpose,
    PurposeError,
    Purposes,
)
from capabledeputy.policy.purposes import (
    load as load_purposes,
)
from capabledeputy.session.graph import SessionGraph


def _write_purposes_yaml(directory: Path, body: str) -> Path:
    p = directory / "purposes.yaml"
    p.write_text(body)
    return p


# --- Loader behaviors ----------------------------------------------------


def test_default_dial_constants_are_canonical() -> None:
    """Q1 vocabulary is exactly the three documented values."""
    assert frozenset({"cautious", "balanced", "permissive"}) == _VALID_DIAL_VALUES
    assert _DEFAULT_DIAL_VALUE == "cautious"


def test_explicit_dial_value_honored(tmp_path: Path) -> None:
    """A Purpose entry with `risk_preference_dial: permissive` produces a
    Purpose whose dial is `permissive`."""
    p = _write_purposes_yaml(
        tmp_path,
        """
purposes:
  - purpose_id: daily-briefing
    label: Daily Briefing
    admissible_categories: [personal, work]
    risk_preference_dial: balanced
  - purpose_id: tax-prep
    label: Tax Prep
    admissible_categories: [financial]
    risk_preference_dial: cautious
""",
    )
    purposes = load_purposes(p)
    assert purposes.get("daily-briefing").risk_preference_dial == "balanced"
    assert purposes.get("tax-prep").risk_preference_dial == "cautious"


def test_dial_omitted_falls_back_to_safety_default(tmp_path: Path) -> None:
    """No dial declared + no legacy file ⇒ defaults to `cautious`."""
    p = _write_purposes_yaml(
        tmp_path,
        """
purposes:
  - purpose_id: chat
    label: General chat
    admissible_categories: [personal]
""",
    )
    purposes = load_purposes(p)
    assert purposes.get("chat").risk_preference_dial == "cautious"


def test_dial_invalid_value_refused(tmp_path: Path) -> None:
    """Bogus dial values (e.g. `whatever`) are refused at load time
    with a clear FR-030 reference in the error."""
    p = _write_purposes_yaml(
        tmp_path,
        """
purposes:
  - purpose_id: bad
    risk_preference_dial: whatever
""",
    )
    with pytest.raises(PurposeError, match="FR-030"):
        load_purposes(p)


# --- Legacy migration (T120) ---------------------------------------------


def test_legacy_risk_preference_supplies_fallback(tmp_path: Path) -> None:
    """When a purpose omits `risk_preference_dial` AND the legacy
    risk_preference.json is present with a valid value, the legacy
    value is used as the fallback default (transitional migration)."""
    legacy = tmp_path / "risk_preference.json"
    legacy.write_text(json.dumps({"value": "permissive", "version": 1}))

    p = _write_purposes_yaml(
        tmp_path,
        """
purposes:
  - purpose_id: legacy-default-applies
    label: Should inherit legacy permissive
    admissible_categories: [public]
  - purpose_id: explicit-wins
    label: Explicit balanced should win
    admissible_categories: [public]
    risk_preference_dial: balanced
""",
    )
    purposes = load_purposes(p, legacy_risk_preference_path=legacy)
    # No explicit dial → fallback to legacy value
    assert purposes.get("legacy-default-applies").risk_preference_dial == "permissive"
    # Explicit dial → wins over fallback
    assert purposes.get("explicit-wins").risk_preference_dial == "balanced"


def test_legacy_invalid_value_ignored(tmp_path: Path) -> None:
    """A legacy risk_preference.json with a bogus value doesn't poison
    the fallback — falls back to the safety default."""
    legacy = tmp_path / "risk_preference.json"
    legacy.write_text(json.dumps({"value": "nonsense"}))

    p = _write_purposes_yaml(
        tmp_path,
        """
purposes:
  - purpose_id: chat
    admissible_categories: [public]
""",
    )
    purposes = load_purposes(p, legacy_risk_preference_path=legacy)
    assert purposes.get("chat").risk_preference_dial == "cautious"


def test_legacy_file_absent_is_ok(tmp_path: Path) -> None:
    """Missing legacy file is not an error — fallback to safety default."""
    p = _write_purposes_yaml(
        tmp_path,
        """
purposes:
  - purpose_id: chat
    admissible_categories: [public]
""",
    )
    purposes = load_purposes(p, legacy_risk_preference_path=tmp_path / "nonexistent.json")
    assert purposes.get("chat").risk_preference_dial == "cautious"


# --- SessionGraph inheritance (T119) -------------------------------------


@pytest.fixture
async def graph_with_purposes(tmp_path: Path):
    """SessionGraph wired with a Purposes registry containing two
    purposes with distinct dial values."""
    audit = AuditWriter(tmp_path / "audit.jsonl")
    purposes = Purposes(
        purposes={
            "tax-prep": Purpose(
                purpose_id="tax-prep",
                admissible_categories=frozenset({"financial"}),
                risk_preference_dial="cautious",
            ),
            "daily-briefing": Purpose(
                purpose_id="daily-briefing",
                admissible_categories=frozenset({"personal", "work"}),
                risk_preference_dial="balanced",
            ),
        },
    )
    graph = SessionGraph(audit=audit, purposes=purposes)
    return graph


async def test_session_inherits_dial_from_purpose(graph_with_purposes) -> None:
    """A session spawned with purpose_handle='tax-prep' carries
    `risk_preference_at_spawn='cautious'` because that's what the
    purpose declares."""
    graph = graph_with_purposes
    s = await graph.new(purpose_handle="tax-prep", intent="returns")
    assert s.risk_preference_at_spawn == "cautious"


async def test_different_purposes_carry_different_dials(graph_with_purposes) -> None:
    """Two sessions with different purposes inherit different dials —
    not a single global value."""
    graph = graph_with_purposes
    cautious_session = await graph.new(purpose_handle="tax-prep")
    permissive_session = await graph.new(purpose_handle="daily-briefing")
    assert cautious_session.risk_preference_at_spawn == "cautious"
    assert permissive_session.risk_preference_at_spawn == "balanced"
    # Critical: they MUST differ — verifying that the dial is genuinely
    # per-purpose and not pulled from a single shared source.
    assert cautious_session.risk_preference_at_spawn != permissive_session.risk_preference_at_spawn


async def test_unknown_purpose_falls_back_to_safety_default(graph_with_purposes) -> None:
    """A session with an unregistered purpose_handle (typo or stale
    config) gets `risk_preference_at_spawn='cautious'` per
    Constitution VI fail-closed."""
    graph = graph_with_purposes
    s = await graph.new(purpose_handle="unknown-purpose")
    assert s.risk_preference_at_spawn == "cautious"


async def test_unset_purpose_falls_back_to_safety_default(graph_with_purposes) -> None:
    """A session without any declared purpose (UNSET_PURPOSE_HANDLE)
    also defaults to `cautious`."""
    graph = graph_with_purposes
    # No purpose_handle arg → defaults to UNSET_PURPOSE_HANDLE
    s = await graph.new()
    assert s.risk_preference_at_spawn == "cautious"


# --- Fork inheritance ----------------------------------------------------


async def test_fork_inherits_parents_resolved_dial(graph_with_purposes) -> None:
    """A child session from `fork` inherits the parent's resolved
    `risk_preference_at_spawn` — NOT re-resolved from the parent's
    purpose. This preserves SC-002 replayability if the operator
    flips the dial mid-life."""
    graph = graph_with_purposes
    parent = await graph.new(purpose_handle="daily-briefing")
    assert parent.risk_preference_at_spawn == "balanced"

    child = await graph.fork(parent.id, intent="follow-up")
    assert child.risk_preference_at_spawn == "balanced"
    assert child.purpose_handle == "daily-briefing"


# --- Session-cannot-mutate-dial-at-runtime invariant ---------------------


def test_session_dataclass_dial_is_frozen() -> None:
    """The Session dataclass MUST be frozen so a session can't
    mutate its own `risk_preference_at_spawn` post-construction.
    A control-plane operation (FR-014 ratification) is the only
    way to change the value, and that lives on the Purpose registry,
    not on individual Sessions."""
    from capabledeputy.session.model import Session

    s = Session.new(risk_preference_at_spawn="cautious")
    with pytest.raises((AttributeError, Exception)):
        # frozen dataclass refuses attribute assignment
        s.risk_preference_at_spawn = "permissive"  # type: ignore[misc]
