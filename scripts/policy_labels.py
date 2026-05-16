#!/usr/bin/env python
"""LABEL coverage — every information-flow label and the policy effect
it has when the session reaches an egress sink.

The eight labels (policy/labels.py) split into:
  triggers   CONFIDENTIAL_HEALTH, CONFIDENTIAL_FINANCIAL,
             UNTRUSTED_EXTERNAL, UNTRUSTED_USER_INPUT
  sinks      EGRESS_EMAIL, EGRESS_PURCHASE  (added by email.send /
             purchase.queue themselves — that is what makes a conflict
             rule fire; never set by hand here)
  benign     CONFIDENTIAL_PERSONAL, TRUSTED_USER_DIRECT  (in no rule;
             they must NOT block egress — proving the engine is precise,
             not blanket-deny on anything "confidential")

Each scenario pins one trigger/benign label on the session and drives
an egress tool; the asserted decision is that label's defined effect.

Run:  uv run python scripts/policy_labels.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _policy_harness import (
    Expect,
    Scenario,
    final,
    run_suite,
    tc,
    tool_turn,
)

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label

TITLE = "LABEL effects (all 8 labels)"

K = CapabilityKind
_EMAIL = frozenset({Capability(kind=K.SEND_EMAIL, pattern="*")})
_PURCHASE = frozenset(
    {Capability(kind=K.QUEUE_PURCHASE, pattern="*", max_amount=10_000)},
)


def _email_turn() -> list:
    return [
        tool_turn(
            "email",
            tc("e", "email.send", to="me@example.com", subject="s", body="b"),
        ),
        final(),
    ]


def _purchase_turn() -> list:
    return [
        tool_turn(
            "buy",
            tc("p", "purchase.queue", vendor="amazon", item="x", amount=9),
        ),
        final(),
    ]


SCENARIOS: list[Scenario] = [
    Scenario(
        name="trusted-user-direct-benign",
        why="TRUSTED_USER_DIRECT is in no conflict rule -> egress ALLOWED.",
        caps=_EMAIL,
        session_labels=frozenset({Label.TRUSTED_USER_DIRECT}),
        responses=_email_turn(),
        expect=[Expect("email.send", "allow")],
    ),
    Scenario(
        name="confidential-personal-benign",
        why="CONFIDENTIAL_PERSONAL is in no conflict rule -> egress ALLOWED "
        "(not all 'confidential' labels block — the engine is precise).",
        caps=_EMAIL,
        session_labels=frozenset({Label.CONFIDENTIAL_PERSONAL}),
        responses=_email_turn(),
        expect=[Expect("email.send", "allow")],
    ),
    Scenario(
        name="confidential-health-blocks-email",
        why="CONFIDENTIAL_HEALTH x EGRESS_EMAIL -> DENY.",
        caps=_EMAIL,
        session_labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
        responses=_email_turn(),
        expect=[Expect("email.send", "deny", "health-meets-egress")],
    ),
    Scenario(
        name="confidential-health-blocks-purchase",
        why="CONFIDENTIAL_HEALTH x EGRESS_PURCHASE -> DENY.",
        caps=_PURCHASE,
        session_labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
        responses=_purchase_turn(),
        expect=[Expect("purchase.queue", "deny", "health-meets-egress")],
    ),
    Scenario(
        name="confidential-financial-blocks-email",
        why="CONFIDENTIAL_FINANCIAL x EGRESS_EMAIL -> DENY.",
        caps=_EMAIL,
        session_labels=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        responses=_email_turn(),
        expect=[Expect("email.send", "deny", "financial-meets-email")],
    ),
    Scenario(
        name="confidential-financial-gates-purchase",
        why="CONFIDENTIAL_FINANCIAL x EGRESS_PURCHASE -> REQUIRE_APPROVAL.",
        caps=_PURCHASE,
        session_labels=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        responses=_purchase_turn(),
        expect=[
            Expect("purchase.queue", "require_approval", "financial-meets-purchase"),
        ],
    ),
    Scenario(
        name="untrusted-external-blocks-email",
        why="UNTRUSTED_EXTERNAL x EGRESS_EMAIL -> DENY.",
        caps=_EMAIL,
        session_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
        responses=_email_turn(),
        expect=[Expect("email.send", "deny", "untrusted-meets-egress")],
    ),
    Scenario(
        name="untrusted-user-input-blocks-purchase",
        why="UNTRUSTED_USER_INPUT x EGRESS_PURCHASE -> DENY.",
        caps=_PURCHASE,
        session_labels=frozenset({Label.UNTRUSTED_USER_INPUT}),
        responses=_purchase_turn(),
        expect=[Expect("purchase.queue", "deny", "untrusted-meets-egress")],
    ),
]


async def main() -> int:
    return await run_suite(TITLE, SCENARIOS)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
