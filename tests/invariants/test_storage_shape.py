"""T016 invariant: every sessions row populates the four axis fields
(FR-045, SC-019). No flat-legacy rows post-migration. Verifies via the
audit_storage_shape() helper that the CLI consumes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.policy.labels import CategoryTag, LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.policy.storage_audit import audit_storage_shape
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.model import Session
from capabledeputy.session.store import SessionStore


@pytest.mark.invariant
async def test_storage_shape_clean_on_fresh_db(tmp_path: Path) -> None:
    """A fresh v6 db with no sessions: audit reports zero rows, ok."""
    store = SessionStore(tmp_path / "fresh.db")
    await store.initialize()
    report = audit_storage_shape(tmp_path / "fresh.db")
    assert report.n_total == 0
    assert report.ok


@pytest.mark.invariant
async def test_storage_shape_clean_on_new_session(tmp_path: Path) -> None:
    """A session created via Session.new() has the four-axis shape."""
    db_path = tmp_path / "withone.db"
    store = SessionStore(db_path)
    await store.initialize()
    session = Session.new(
        label_state=LabelState(
            a=frozenset({CategoryTag(category="health", tier=Tier.REGULATED, risk_ids=("R001",))}),
            b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
        ),
    )
    await store.upsert(session)

    report = audit_storage_shape(db_path)
    assert report.n_total == 1
    assert report.ok, f"unexpected violations: {report.bad_rows}"


@pytest.mark.invariant
async def test_storage_shape_default_session_passes(tmp_path: Path) -> None:
    """A Session.new() with no v0.9 args still has valid (empty) axis
    shapes — defaults are structurally valid, not just type-checked."""
    db_path = tmp_path / "default.db"
    store = SessionStore(db_path)
    await store.initialize()
    await store.upsert(Session.new())

    report = audit_storage_shape(db_path)
    assert report.n_total == 1
    assert report.ok, f"defaults must produce valid shape: {report.bad_rows}"
