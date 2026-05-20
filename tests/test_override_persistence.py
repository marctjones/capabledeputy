"""Override grant persistence — survives a daemon restart.

The contract: a grant created in process A is still active in process
B reading the same SQLite store. Without this, every daemon restart
loses every active grant — a hostile operational gotcha.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.overrides import (
    FrictionLevel,
    GrantState,
    HardFloor,
    OverrideGrant,
    OverrideGrantStore,
    OverridePolicy,
    OverridePolicyEntry,
)


def _init_v6_schema(db: Path) -> None:
    """Bootstrap a minimal sessions store with the override_grants
    table including the state column."""
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS override_grants (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            action_kind TEXT NOT NULL,
            target TEXT NOT NULL,
            target_category_tier TEXT NOT NULL,
            hard_floor_crossed TEXT NOT NULL,
            invoker_principal TEXT NOT NULL,
            attester_principal TEXT NULL,
            override_policy_at_grant TEXT NOT NULL,
            friction_level TEXT NOT NULL,
            audit_id TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT NULL,
            state TEXT NOT NULL DEFAULT 'active',
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );
        """,
    )
    # Insert a placeholder session so the FK is satisfied.
    conn.commit()
    conn.close()


def _make_grant(*, session_id):
    return OverrideGrant(
        id=uuid4(),
        session_id=session_id,
        action_kind=CapabilityKind.SEND_EMAIL,
        target="alice@example.com",
        target_category_tier=("personal", "restricted"),
        hard_floor_crossed=HardFloor.MAX_TIER_CLEARANCE,
        invoker_principal="alice",
        attester_principal=None,
        policy_at_grant=OverridePolicyEntry(
            floor=HardFloor.MAX_TIER_CLEARANCE,
            policy=OverridePolicy.SINGLE_AUTHORIZED,
            authorized_principal_ids=frozenset({"alice"}),
            expiry_seconds=300,
        ),
        friction_level=FrictionLevel.MEDIUM,
        state=GrantState.ACTIVE,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def test_in_memory_only_store_does_not_persist(tmp_path: Path) -> None:
    """Without a db_path, the store is pure in-memory; a new store
    instance sees nothing."""
    store_a = OverrideGrantStore()
    sid = uuid4()
    store_a.add(_make_grant(session_id=sid))
    store_b = OverrideGrantStore()
    assert store_b.list_all() == []


def test_persistent_store_survives_restart(tmp_path: Path) -> None:
    """A grant added in store_a is loaded by store_b reading the same
    DB path — the daemon-restart scenario."""
    db = tmp_path / "state.db"
    _init_v6_schema(db)
    sid = uuid4()
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO sessions (id) VALUES (?)", (str(sid),))
    conn.commit()
    conn.close()

    store_a = OverrideGrantStore(db_path=db)
    grant = _make_grant(session_id=sid)
    store_a.add(grant)
    assert store_a.get(grant.id) is grant

    # Simulate daemon restart by creating a fresh store against same DB.
    store_b = OverrideGrantStore(db_path=db)
    revived = store_b.get(grant.id)
    assert revived is not None
    assert revived.id == grant.id
    assert revived.session_id == grant.session_id
    assert revived.action_kind == grant.action_kind
    assert revived.target == grant.target
    assert revived.state == GrantState.ACTIVE
    assert revived.invoker_principal == "alice"
    assert revived.policy_at_grant.policy == OverridePolicy.SINGLE_AUTHORIZED
    assert revived.policy_at_grant.authorized_principal_ids == frozenset({"alice"})


def test_persistent_store_update_round_trips(tmp_path: Path) -> None:
    """A state transition (active → consumed) on store_a is visible
    on store_b after reload."""
    from dataclasses import replace

    db = tmp_path / "state.db"
    _init_v6_schema(db)
    sid = uuid4()
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO sessions (id) VALUES (?)", (str(sid),))
    conn.commit()
    conn.close()

    store_a = OverrideGrantStore(db_path=db)
    grant = _make_grant(session_id=sid)
    store_a.add(grant)
    consumed = replace(
        grant,
        state=GrantState.CONSUMED,
        consumed_at=datetime.now(UTC),
    )
    store_a.update(consumed)

    store_b = OverrideGrantStore(db_path=db)
    revived = store_b.get(grant.id)
    assert revived is not None
    assert revived.state == GrantState.CONSUMED
    assert revived.consumed_at is not None
