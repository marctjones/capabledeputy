#!/usr/bin/env python
"""ALLOW coverage — every native tool, exercised with the capability it
requires and a clean (non-tainting) label set, must be ALLOWED.

This sweeps the full tool surface: memory.{create,read,write,update,
delete}, inbox.{list,read}, calendar.{events_today,create_event,
update_event,delete_event}, web.fetch, policy.preview, email.send,
purchase.queue. The destructive tools are allowed here only because the
capability carries allows_destructive=True (the gate's sanctioned
bypass); without it they require approval — see policy_require_approval.

Run:  uv run python scripts/policy_allow.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
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

from capabledeputy.llm.types import LLMResponse
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import LabelState, tags_for_labels_strings
from capabledeputy.tools.native.inbox import InboundMessage

TITLE = "ALLOW paths (full tool sweep)"

K = CapabilityKind


def _seed_inbox(app: object) -> None:
    app.inbox.add(  # type: ignore[attr-defined]
        InboundMessage(
            id="m1",
            sender="colleague@example.com",
            subject="hi",
            body="hello",
            received_at=datetime.now(UTC),
        ),
    )


def _seed_mem(app: object) -> None:
    app.memory.write("doomed", "v", LabelState())  # type: ignore[attr-defined]
    app.memory.write("stale", "v", LabelState())  # type: ignore[attr-defined]


SCENARIOS: list[Scenario] = [
    Scenario(
        name="read-family-allow",
        why="Read-only tools with their READ/CALENDAR_READ/WEB_FETCH caps -> ALLOW.",
        caps=frozenset(
            {
                Capability(kind=K.CREATE_FS, pattern="*"),
                Capability(kind=K.READ_FS, pattern="*"),
                Capability(kind=K.CALENDAR_READ, pattern="*"),
                Capability(kind=K.WEB_FETCH, pattern="*"),
            },
        ),
        pre=_seed_inbox,
        responses=[
            tool_turn(
                "sweep reads",
                tc("c1", "memory.create", key="k", value="v"),
                tc("r1", "memory.read", key="k"),
                tc("il", "inbox.list"),
                tc("ir", "inbox.read", id="m1"),
                tc("cal", "calendar.events_today"),
                tc("pp", "policy.preview", kind="READ_FS"),
                tc("wf", "web.fetch", url="https://example.test/doc"),
            ),
            final(),
        ],
        expect=[
            Expect("memory.create", "allow"),
            Expect("memory.read", "allow"),
            Expect("inbox.list", "allow"),
            Expect("inbox.read", "allow"),
            Expect("calendar.events_today", "allow"),
            Expect("policy.preview", "allow"),
            Expect("web.fetch", "allow"),
        ],
    ),
    Scenario(
        name="write-family-allow",
        why="Non-destructive writes with WRITE_FS / CREATE_CAL caps -> ALLOW.",
        caps=frozenset(
            {
                Capability(kind=K.WRITE_FS, pattern="*"),
                Capability(kind=K.CREATE_CAL, pattern="*"),
            },
        ),
        responses=[
            tool_turn(
                "writes",
                tc("mw", "memory.write", key="note", value="hello"),
                tc(
                    "ce",
                    "calendar.create_event",
                    title="standup",
                    starts_at="2026-06-01T09:00:00+00:00",
                    ends_at="2026-06-01T09:15:00+00:00",
                ),
            ),
            final(),
        ],
        expect=[
            Expect("memory.write", "allow"),
            Expect("calendar.create_event", "allow"),
        ],
    ),
    Scenario(
        name="destructive-allow-with-bypass",
        why=(
            "MODIFY/DELETE tools are ALLOWED only because the capability "
            "sets allows_destructive=True (the sanctioned gate bypass)."
        ),
        caps=frozenset(
            {
                Capability(kind=K.MODIFY_FS, pattern="*", allows_destructive=True),
                Capability(kind=K.DELETE_FS, pattern="*", allows_destructive=True),
                Capability(kind=K.MODIFY_CAL, pattern="*", allows_destructive=True),
                Capability(kind=K.DELETE_CAL, pattern="*", allows_destructive=True),
            },
        ),
        pre=_seed_mem,
        responses=[
            tool_turn(
                "destructive ops",
                tc("mu", "memory.update", key="stale", value="fresh"),
                tc("md", "memory.delete", key="doomed"),
                tc(
                    "cu",
                    "calendar.update_event",
                    id="00000000-0000-0000-0000-000000000001",
                    title="moved",
                ),
                tc(
                    "cd",
                    "calendar.delete_event",
                    id="00000000-0000-0000-0000-000000000002",
                ),
            ),
            final(),
        ],
        expect=[
            Expect("memory.update", "allow"),
            Expect("memory.delete", "allow"),
            Expect("calendar.update_event", "allow"),
            Expect("calendar.delete_event", "allow"),
        ],
    ),
    Scenario(
        name="quarantined-extract-allow",
        why=(
            "quarantined.extract over labeled memory is READ_FS-gated -> "
            "ALLOW; the schema validation is the declassifier, so the "
            "planner never sees the raw labeled source."
        ),
        caps=frozenset({Capability(kind=K.READ_FS, pattern="*")}),
        pre=lambda app: app.memory.write(  # type: ignore[attr-defined]
            "briefing.source",
            "CALENDAR 3 events; INBOX 5 unread; top: 1:1 with Maria",
            tags_for_labels_strings(frozenset({"confidential.personal", "untrusted.external"})),
        ),
        quarantined=[
            LLMResponse(
                content=(
                    '{"date": "2026-05-16", '
                    '"n_calendar_events": 3, '
                    '"n_unread_emails": 5, '
                    '"top_priority": "1:1 with Maria at 10am", '
                    '"suggested_focus": "ship the policy harness"}'
                ),
            ),
        ],
        responses=[
            tool_turn(
                "extract briefing",
                tc(
                    "qe",
                    "quarantined.extract",
                    key="briefing.source",
                    schema="DailyBriefing",
                ),
            ),
            final(),
        ],
        expect=[Expect("quarantined.extract", "allow")],
    ),
    Scenario(
        name="clean-egress-allow",
        why="Egress tools with no trigger label in the session -> ALLOW.",
        caps=frozenset(
            {
                Capability(kind=K.SEND_EMAIL, pattern="*@example.com"),
                Capability(kind=K.QUEUE_PURCHASE, pattern="*", max_amount=1000),
            },
        ),
        responses=[
            tool_turn(
                "clean egress",
                tc("es", "email.send", to="me@example.com", subject="s", body="b"),
                tc("pq", "purchase.queue", vendor="amazon", item="pen", amount=3),
            ),
            final(),
        ],
        expect=[
            Expect("email.send", "allow"),
            Expect("purchase.queue", "allow"),
        ],
    ),
]


async def main() -> int:
    return await run_suite(TITLE, SCENARIOS)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
