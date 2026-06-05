"""Roadmap v2 #7 — first-use approvals carry the rule name and
the REPL renders them with a distinct banner instead of a plain
table row.

The pre-existing first-use mechanism (cookbook §4 #6, shipped
74d4265) escalates ALLOW → REQUIRE_APPROVAL on the first time a
session uses a promptable kind. The escalation looked
identical to a rule-driven REQUIRE_APPROVAL in the REPL, which
hid the fact that the operator was being asked a different
question. This commit plumbs `policy_decision.rule` through
`ApprovalQueue.submit` onto `ApprovalRequest.rule`, exposes it
via `to_dict()`, and exercises the REPL's distinct-banner
branch by feeding `_render_approvals` synthetic approval dicts.
"""

from __future__ import annotations

import asyncio
from io import StringIO
from uuid import uuid4

import pytest
from rich.console import Console

import capabledeputy.cli.chat as chat
from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.queue import ApprovalQueue
from capabledeputy.policy.labels import Label


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- Plumbing: ApprovalRequest.rule survives submit + to_dict ----------


@pytest.mark.anyio
async def test_submit_with_rule_persists_on_request() -> None:
    """The queue forwards the caller's `rule` arg onto the
    persisted ApprovalRequest. None when omitted (back-compat)."""
    q = ApprovalQueue()
    session = uuid4()
    req = await q.submit(
        from_session=session,
        action=ApprovalAction.SEND_EMAIL,
        payload="hi",
        target="alice@example.com",
        labels_in=frozenset({Label.CONFIDENTIAL_PERSONAL}),
        justification="user requested",
        rule="first-use-of-kind",
    )
    assert req.rule == "first-use-of-kind"
    snapshot = req.to_dict()
    assert snapshot["rule"] == "first-use-of-kind"


@pytest.mark.anyio
async def test_submit_without_rule_remains_none() -> None:
    """Pre-existing callers that don't pass `rule` see None — the
    plumbing is opt-in, not retroactive."""
    q = ApprovalQueue()
    session = uuid4()
    req = await q.submit(
        from_session=session,
        action=ApprovalAction.SEND_EMAIL,
        payload="x",
        target="bob@example.com",
        labels_in=frozenset({Label.CONFIDENTIAL_PERSONAL}),
    )
    assert req.rule is None
    assert req.to_dict()["rule"] is None


# --- REPL rendering: first-use cards split from the standard table -----


def _capture(approvals: list[dict]) -> str:
    """Run `_render_approvals` against a recording Console and
    return the captured output. The chat module's module-level
    `console` is the same singleton inside `_render_approvals`,
    so we patch it for the call window."""
    buf = StringIO()
    recording = Console(file=buf, width=120, force_terminal=False, no_color=True)
    saved = chat.console
    chat.console = recording
    try:
        chat._render_approvals(approvals)
    finally:
        chat.console = saved
    return buf.getvalue()


def _approval_dict(
    *,
    approval_id: int,
    action: str,
    target: str,
    rule: str | None = None,
    sibling_group_id: str | None = None,
) -> dict:
    return {
        "id": approval_id,
        "action": action,
        "target": target,
        "payload": "<payload>",
        "rule": rule,
        "sibling_group_id": sibling_group_id,
    }


def test_first_use_renders_distinct_banner() -> None:
    """A single first-use approval renders the ⚠ banner with the
    action and target in the header line, plus a friendlier
    explainer below."""
    out = _capture(
        [
            _approval_dict(
                approval_id=42,
                action="SEND_EMAIL",
                target="alice@example.com",
                rule="first-use-of-kind",
            ),
        ],
    )
    assert "first use of" in out
    assert "SEND_EMAIL" in out
    assert "confirm intent" in out
    # The friendlier explainer mentions that subsequent uses won't re-prompt
    assert "subsequent" in out or "re-prompt" in out
    # No standard "Pending approvals" table when ALL approvals are first-use
    assert "Pending approvals" not in out


def test_standard_approval_renders_in_table_not_banner() -> None:
    """A standard (non-first-use) approval renders in the regular
    table, not the ⚠ banner."""
    out = _capture(
        [
            _approval_dict(
                approval_id=7,
                action="QUEUE_PURCHASE",
                target="amazon.com",
                rule="purchase-needs-approval",
            ),
        ],
    )
    assert "Pending approvals" in out
    assert "first use of" not in out


def test_mixed_first_use_and_standard_renders_both_sections() -> None:
    """When the queue mixes first-use and standard approvals,
    BOTH sections render — first-use banners on top, standard
    table below. The operator sees them all but can tell which
    is which."""
    out = _capture(
        [
            _approval_dict(
                approval_id=1,
                action="SEND_EMAIL",
                target="alice@example.com",
                rule="first-use-of-kind",
            ),
            _approval_dict(
                approval_id=2,
                action="QUEUE_PURCHASE",
                target="amazon.com",
                rule="purchase-needs-approval",
            ),
        ],
    )
    assert "first use of" in out
    assert "Pending approvals" in out
    # Both ids surface
    assert "1" in out
    assert "2" in out


def test_empty_queue_still_shows_no_pending() -> None:
    """Back-compat: empty input still prints the friendly
    no-pending hint instead of an empty render."""
    out = _capture([])
    assert "no pending approvals" in out


def test_first_use_without_rule_field_treated_as_standard() -> None:
    """An approval dict from an OLDER daemon (no `rule` field at
    all) renders as standard, not first-use. We rely on the
    daemon to surface the rule explicitly — silent inference
    would surface the wrong UX on legacy queues."""
    legacy = {
        "id": 99,
        "action": "SEND_EMAIL",
        "target": "x@y.com",
        "payload": "p",
        "sibling_group_id": None,
        # NO "rule" key at all
    }
    out = _capture([legacy])
    assert "first use of" not in out
    assert "Pending approvals" in out


# --- End-to-end: dispatcher plumbs rule from PolicyDecision to queue ---


def test_policy_decision_rule_constant_matches_renderer() -> None:
    """The REPL branch keys on the literal string
    'first-use-of-kind'. That string is also the engine's
    FIRST_USE_OF_KIND_RULE constant — keep them in lockstep so a
    rename of one fails this test loudly instead of silently
    breaking the UX path."""
    from capabledeputy.policy.engine import FIRST_USE_OF_KIND_RULE

    assert FIRST_USE_OF_KIND_RULE == "first-use-of-kind"


# Keep an asyncio-runner alias so this file can be invoked standalone
# without anyio's pytest plugin (e.g. `python -m tests.test_first_use_repl_ux`).
def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)
