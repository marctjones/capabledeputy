"""Hermetic daily-driver acceptance evidence for the live daemon (#451).

These tests use the Unix-socket RPC surface a client uses.  Test setup seeds
the daemon-owned memory store directly so no production RPC can mint a raw
reference handle or disclose the restricted fixture value.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier
from tests.daemon_integration import running_daemon


def _restricted_financial() -> LabelState:
    return LabelState(a=frozenset({CategoryTag("financial", Tier.RESTRICTED)}))


async def test_live_daemon_routes_restricted_memory_by_handle_only(tmp_path: Path) -> None:
    """A socket client can route, but never receive, a restricted value.

    ``memory.handle`` provides an opaque identifier.  The reference monitor
    approves the local create before the runtime binds the value, and the
    daemon security projection records the resulting Pattern-3 evidence.
    """
    secret = "acceptance-only restricted financial value"
    key = "acceptance/restricted-statement"
    destination = tmp_path / "routed-statement.txt"

    async with running_daemon(tmp_path) as daemon:
        created = await daemon.client.call(
            "session.new",
            {"owner": "acceptance", "intent": "route a restricted document"},
        )
        session_id = UUID(created["id"])
        await daemon.app.graph.grant_capability(
            session_id,
            Capability(kind=CapabilityKind.MEMORY_READ, pattern=key),
        )
        await daemon.app.graph.grant_capability(
            session_id,
            Capability(kind=CapabilityKind.CREATE_FS, pattern=str(destination)),
        )
        daemon.app.memory.write(key, secret, _restricted_financial())

        handle_result = await daemon.client.call(
            "tool.call",
            {"session_id": created["id"], "tool": "memory.handle", "args": {"key": key}},
        )
        assert handle_result["decision"] == "allow"
        assert secret not in json.dumps(handle_result)
        handle = str(handle_result["output"]["handle"])
        assert handle
        assert secret not in handle

        routed = await daemon.client.call(
            "tool.call",
            {
                "session_id": created["id"],
                "tool": "fs.create",
                "args": {"path": str(destination), "content": handle},
            },
        )
        assert routed["decision"] == "allow", routed
        assert secret not in json.dumps(routed)
        assert destination.read_text(encoding="utf-8") == secret

        context = await daemon.client.call(
            "session.security_context",
            {"session_id": created["id"]},
        )

    decisions = context["policy"]["recent_decisions"]
    assert [item["tool"] for item in decisions[-2:]] == ["memory.handle", "fs.create"]
    assert any(node["kind"] == "reference_handle" for node in context["provenance"]["nodes"])
    assert any(node["kind"] == "reference_bind" for node in context["provenance"]["nodes"])
    assert any(edge["kind"] == "bound" for edge in context["provenance"]["edges"])
    assert any(
        event["event_type"] == "pattern3.handle_bind"
        for event in context["audit_evidence"]
    )
