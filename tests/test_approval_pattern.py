from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.approval.model import ApprovalAction, ApprovalRequest
from capabledeputy.approval.pattern import (
    ApprovalPatternRegistry,
    ApprovalPatternRule,
    PatternValidationError,
)
from capabledeputy.approval.queue import ApprovalQueue
from capabledeputy.audit.writer import AuditWriter


def _request(target: str = "wife@example.com", payload: str = "x") -> ApprovalRequest:
    return ApprovalRequest(
        id=1,
        audit_id=uuid4(),
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        payload=payload,
        target=target,
        labels_in=frozenset(),
        labels_out=frozenset(),
        capability_requested=None,
        justification="",
    )


def test_create_validates_empty_pattern() -> None:
    with pytest.raises(PatternValidationError, match="empty"):
        ApprovalPatternRule.create(
            action=ApprovalAction.SEND_EMAIL,
            target_pattern="",
            ttl=timedelta(hours=1),
        )


def test_create_rejects_bare_star() -> None:
    with pytest.raises(PatternValidationError, match="too permissive"):
        ApprovalPatternRule.create(
            action=ApprovalAction.SEND_EMAIL,
            target_pattern="*",
            ttl=timedelta(hours=1),
        )


def test_create_rejects_unanchored_star() -> None:
    with pytest.raises(PatternValidationError, match="domain anchor"):
        ApprovalPatternRule.create(
            action=ApprovalAction.SEND_EMAIL,
            target_pattern="*foo",
            ttl=timedelta(hours=1),
        )


def test_create_accepts_domain_anchored_glob() -> None:
    rule = ApprovalPatternRule.create(
        action=ApprovalAction.SEND_EMAIL,
        target_pattern="*@example.com",
        ttl=timedelta(hours=1),
    )
    assert rule.target_pattern == "*@example.com"


def test_create_accepts_concrete_target() -> None:
    rule = ApprovalPatternRule.create(
        action=ApprovalAction.SEND_EMAIL,
        target_pattern="wife@example.com",
        ttl=timedelta(hours=1),
    )
    assert rule.target_pattern == "wife@example.com"


def test_ttl_must_be_positive() -> None:
    with pytest.raises(PatternValidationError, match="ttl must be positive"):
        ApprovalPatternRule.create(
            action=ApprovalAction.SEND_EMAIL,
            target_pattern="wife@example.com",
            ttl=timedelta(0),
        )


def test_ttl_capped_at_30_days() -> None:
    with pytest.raises(PatternValidationError, match="cannot exceed"):
        ApprovalPatternRule.create(
            action=ApprovalAction.SEND_EMAIL,
            target_pattern="wife@example.com",
            ttl=timedelta(days=31),
        )


def test_matches_concrete_target() -> None:
    rule = ApprovalPatternRule.create(
        action=ApprovalAction.SEND_EMAIL,
        target_pattern="wife@example.com",
        ttl=timedelta(hours=1),
    )
    assert rule.matches(_request("wife@example.com"))
    assert not rule.matches(_request("boss@example.com"))


def test_matches_domain_glob() -> None:
    rule = ApprovalPatternRule.create(
        action=ApprovalAction.SEND_EMAIL,
        target_pattern="*@example.com",
        ttl=timedelta(hours=1),
    )
    assert rule.matches(_request("anyone@example.com"))
    assert not rule.matches(_request("anyone@other.com"))


def test_does_not_match_different_action() -> None:
    rule = ApprovalPatternRule.create(
        action=ApprovalAction.SEND_EMAIL,
        target_pattern="wife@example.com",
        ttl=timedelta(hours=1),
    )
    purchase_req = _request()
    purchase_req = ApprovalRequest(
        **{**purchase_req.__dict__, "action": ApprovalAction.QUEUE_PURCHASE},
    )
    assert not rule.matches(purchase_req)


def test_expired_rule_does_not_match() -> None:
    rule = ApprovalPatternRule.create(
        action=ApprovalAction.SEND_EMAIL,
        target_pattern="wife@example.com",
        ttl=timedelta(seconds=1),
    )
    from dataclasses import replace as _replace

    expired = _replace(rule, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert not expired.matches(_request())


def test_revoked_rule_does_not_match() -> None:
    registry = ApprovalPatternRegistry()
    rule = ApprovalPatternRule.create(
        action=ApprovalAction.SEND_EMAIL,
        target_pattern="wife@example.com",
        ttl=timedelta(hours=1),
    )
    registry.add(rule)
    registry.revoke(rule.id)
    assert registry.find_match(_request("wife@example.com")) is None


async def test_queue_auto_approves_matching_request(tmp_path: Path) -> None:
    writer = AuditWriter(tmp_path / "audit.jsonl")
    registry = ApprovalPatternRegistry()
    registry.add(
        ApprovalPatternRule.create(
            action=ApprovalAction.SEND_EMAIL,
            target_pattern="wife@example.com",
            ttl=timedelta(hours=1),
        ),
    )
    queue = ApprovalQueue(audit=writer, pattern_registry=registry)

    sid = uuid4()
    submitted = await queue.submit(
        from_session=sid,
        action=ApprovalAction.SEND_EMAIL,
        payload="hi wife",
        target="wife@example.com",
        labels_in=frozenset(),
    )
    assert submitted.status.value == "approved"
    assert submitted.decided_by is not None
    assert submitted.decided_by.startswith("pattern:")


async def test_queue_does_not_auto_approve_non_matching(tmp_path: Path) -> None:
    writer = AuditWriter(tmp_path / "audit.jsonl")
    registry = ApprovalPatternRegistry()
    registry.add(
        ApprovalPatternRule.create(
            action=ApprovalAction.SEND_EMAIL,
            target_pattern="wife@example.com",
            ttl=timedelta(hours=1),
        ),
    )
    queue = ApprovalQueue(audit=writer, pattern_registry=registry)

    sid = uuid4()
    submitted = await queue.submit(
        from_session=sid,
        action=ApprovalAction.SEND_EMAIL,
        payload="x",
        target="boss@example.com",
        labels_in=frozenset(),
    )
    assert submitted.status.value == "pending"


async def test_queue_increments_use_count(tmp_path: Path) -> None:
    writer = AuditWriter(tmp_path / "audit.jsonl")
    registry = ApprovalPatternRegistry()
    rule = ApprovalPatternRule.create(
        action=ApprovalAction.SEND_EMAIL,
        target_pattern="wife@example.com",
        ttl=timedelta(hours=1),
    )
    registry.add(rule)
    queue = ApprovalQueue(audit=writer, pattern_registry=registry)

    sid = uuid4()
    for _ in range(3):
        await queue.submit(
            from_session=sid,
            action=ApprovalAction.SEND_EMAIL,
            payload="x",
            target="wife@example.com",
            labels_in=frozenset(),
        )

    after = next(r for r in registry.list() if r.id == rule.id)
    assert after.auto_approval_count == 3
