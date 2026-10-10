"""Revoking a capability over the real socket stops the next dispatch (002 US2).

Complements the unit tests in tests/policy: this drives a live daemon through the
same RPCs a client uses, and asserts both the denial and its audit trail.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import anyio

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from tests.daemon_integration import build_test_daemon, daemon_test_paths, wait_for_socket


async def test_revoked_capability_denies_the_next_dispatch_and_is_audited(tmp_path: Path) -> None:
    paths = daemon_test_paths(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    first = workdir / "first.txt"
    second = workdir / "second.txt"
    daemon, app = await build_test_daemon(paths)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await wait_for_socket(paths.socket)
        client = DaemonClient(paths.socket)
        try:
            created = await client.call("session.new", {"owner": "acceptance", "intent": "revoke"})
            session_id = created["id"]
            await app.graph.grant_capability(
                UUID(session_id),
                Capability(kind=CapabilityKind.CREATE_FS, pattern=str(workdir / "*.txt")),
            )

            allowed = await client.call(
                "tool.call",
                {
                    "session_id": session_id,
                    "tool": "fs.create",
                    "args": {"path": str(first), "content": "one"},
                },
            )
            assert allowed["decision"] == "allow"
            assert first.read_text(encoding="utf-8") == "one"

            context = await client.call("session.security_context", {"session_id": session_id})
            active = [c for c in context["capabilities"]["active"] if c["kind"] == "CREATE_FS"]
            assert len(active) == 1
            await client.call(
                "capability.revoke",
                {"session_id": session_id, "audit_id": active[0]["audit_id"]},
            )

            denied = await client.call(
                "tool.call",
                {
                    "session_id": session_id,
                    "tool": "fs.create",
                    "args": {"path": str(second), "content": "two"},
                },
            )
            assert denied["decision"] != "allow", denied
            assert not second.exists(), "a revoked capability must not produce a file"

            after = await client.call("session.security_context", {"session_id": session_id})
            # `active` is the raw capability set; revocation is recorded alongside it, so a
            # client must subtract `revoked_audit_ids` (the daemon does so lazily at decide()).
            assert active[0]["audit_id"] in after["capabilities"]["revoked_audit_ids"]
        finally:
            await client.call("shutdown")

    events = await app.audit.read_all()
    assert any("revok" in str(event).lower() for event in events), "revocation must be audited"
