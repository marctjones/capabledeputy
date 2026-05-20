"""T040 invariants for 003 US2 (FR-029).

Expectation bindings are a deterministic registry — pure match,
no heuristic inference (Principle I). The match function consumes
(initiator, effect_kind, params, now) and returns bool.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from capabledeputy.policy.expectations import (
    ExpectationBinding,
    ExpectationBindings,
    ExpectationError,
    load,
)


def test_match_basic() -> None:
    b = ExpectationBinding(
        binding_id="backup-nightly",
        initiator="cron-configured-by-principal",
        effect_kind="MUTATE_LOCAL",
    )
    assert b.matches(
        initiator="cron-configured-by-principal",
        effect_kind="MUTATE_LOCAL",
    )
    assert not b.matches(initiator="other-initiator", effect_kind="MUTATE_LOCAL")
    assert not b.matches(
        initiator="cron-configured-by-principal",
        effect_kind="COMMUNICATE",
    )


def test_match_time_window() -> None:
    b = ExpectationBinding(
        binding_id="nightly",
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        time_window=(2, 4),
    )
    # 03:00 UTC is inside window.
    assert b.matches(
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        now=datetime(2026, 5, 19, 3, 0, tzinfo=UTC),
    )
    # 10:00 UTC is outside.
    assert not b.matches(
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        now=datetime(2026, 5, 19, 10, 0, tzinfo=UTC),
    )


def test_match_time_window_wraps_midnight() -> None:
    b = ExpectationBinding(
        binding_id="late-night",
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        time_window=(22, 6),
    )
    # 23:00 inside.
    assert b.matches(
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        now=datetime(2026, 5, 19, 23, 0, tzinfo=UTC),
    )
    # 02:00 inside.
    assert b.matches(
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        now=datetime(2026, 5, 19, 2, 0, tzinfo=UTC),
    )
    # 14:00 outside.
    assert not b.matches(
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        now=datetime(2026, 5, 19, 14, 0, tzinfo=UTC),
    )


def test_match_param_constraints_exact() -> None:
    b = ExpectationBinding(
        binding_id="backup-to-tmp",
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        param_constraints={"target": "/var/backups"},
    )
    assert b.matches(
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        params={"target": "/var/backups"},
    )
    assert not b.matches(
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        params={"target": "/etc/passwd"},
    )
    assert not b.matches(initiator="cron", effect_kind="MUTATE_LOCAL", params={})


def test_match_param_constraints_allow_list() -> None:
    b = ExpectationBinding(
        binding_id="backup-allowed-dirs",
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        param_constraints={"target": ["/var/backups", "/var/snapshots"]},
    )
    assert b.matches(
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        params={"target": "/var/backups"},
    )
    assert b.matches(
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        params={"target": "/var/snapshots"},
    )
    assert not b.matches(
        initiator="cron",
        effect_kind="MUTATE_LOCAL",
        params={"target": "/etc/passwd"},
    )


def test_is_expected_aggregates_bindings() -> None:
    """ExpectationBindings.is_expected returns True iff any binding matches."""
    bs = ExpectationBindings(
        bindings=(
            ExpectationBinding(binding_id="a", initiator="cron-a", effect_kind="FETCH"),
            ExpectationBinding(binding_id="b", initiator="cron-b", effect_kind="OBSERVE"),
        ),
    )
    assert bs.is_expected(initiator="cron-a", effect_kind="FETCH")
    assert bs.is_expected(initiator="cron-b", effect_kind="OBSERVE")
    assert not bs.is_expected(initiator="cron-c", effect_kind="FETCH")


def test_load_missing_file_fails_closed() -> None:
    with pytest.raises(ExpectationError, match="missing"):
        load(Path("/nonexistent.yaml"))


def test_load_duplicate_binding_id(tmp_path: Path) -> None:
    (tmp_path / "dup.yaml").write_text(
        "bindings:\n"
        "  - binding_id: a\n"
        "    initiator: x\n"
        "    effect_kind: FETCH\n"
        "  - binding_id: a\n"
        "    initiator: y\n"
        "    effect_kind: FETCH\n",
    )
    with pytest.raises(ExpectationError, match="duplicate"):
        load(tmp_path / "dup.yaml")
