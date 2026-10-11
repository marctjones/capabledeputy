"""Native auth trusts a pinned daemon child, never an RPC marker."""

import hashlib
import json
import sys
from uuid import uuid4

import pytest

from capabledeputy.approval.model import ApprovalAction, ApprovalStatus
from capabledeputy.approval.strong_auth import authenticate_approval
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.daemon.settings_store import DaemonSettings
from capabledeputy.policy.labels import LabelState
from tests.test_approval_strong_auth import _request


def install_fixture_helper(tmp_path, monkeypatch, script):
    monkeypatch.setattr("capabledeputy.cli._managed_config.user_config_dir", lambda: tmp_path)
    monkeypatch.setattr("capabledeputy.approval.strong_auth.sys.platform", "darwin")
    helper = tmp_path / "auth-helper"
    helper.write_text(f"#!{sys.executable}\n" + script)
    helper.chmod(0o700)
    config = tmp_path / "approval-auth.json"
    config.write_text(
        json.dumps(
            {"helper_path": str(helper), "sha256": hashlib.sha256(helper.read_bytes()).hexdigest()}
        )
    )
    config.chmod(0o600)
    return helper


async def test_daemon_auth_checks_fresh_bound_response(tmp_path, monkeypatch):
    install_fixture_helper(
        tmp_path,
        monkeypatch,
        "import json,sys\nr=json.load(sys.stdin)\n"
        "print(json.dumps({'nonce':r['nonce'],'binding':r['binding'],'authenticated':True}))\n",
    )
    request = _request(ApprovalAction.QUEUE_PURCHASE)
    proof = await authenticate_approval(request)
    assert proof["method"] == "daemon-native-device-owner"
    expected = hashlib.sha256(
        json.dumps(request.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert proof["review_digest"] == expected


@pytest.mark.parametrize("script", ["print('{}')", "print('not a proof')", "raise SystemExit(4)"])
async def test_cancel_invalid_or_replayed_proof_fails_closed(tmp_path, monkeypatch, script):
    install_fixture_helper(tmp_path, monkeypatch, script)
    with pytest.raises(ValueError, match=r"cancelled|failed"):
        await authenticate_approval(_request(ApprovalAction.QUEUE_PURCHASE))


async def test_changed_helper_and_public_config_are_rejected(tmp_path, monkeypatch):
    helper = install_fixture_helper(tmp_path, monkeypatch, "print('{}')")
    helper.write_text(helper.read_text() + "\n# changed\n")
    with pytest.raises(ValueError, match="changed"):
        await authenticate_approval(_request(ApprovalAction.QUEUE_PURCHASE))
    (tmp_path / "approval-auth.json").chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        await authenticate_approval(_request(ApprovalAction.QUEUE_PURCHASE))


async def test_client_touch_id_marker_is_not_authentication(tmp_path, monkeypatch):
    from capabledeputy.app import App

    monkeypatch.setattr("capabledeputy.cli._managed_config.user_config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "capabledeputy.daemon.approval_handlers.load_settings",
        lambda: DaemonSettings(require_touch_id_for_high_risk=True),
    )
    app = App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")
    request = await app.approval_queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.QUEUE_PURCHASE,
        payload="buy",
        target="vendor",
        labels_in=LabelState(),
        justification="test",
    )
    with pytest.raises(ValueError, match="strong authentication"):
        await make_approval_handlers(app)["approval.approve"](
            {"id": request.id, "strong_auth": "touch_id"}
        )
    assert app.approval_queue.get(request.id).status == ApprovalStatus.PENDING


async def test_superseding_while_authenticating_prevents_authorization(tmp_path, monkeypatch):
    from capabledeputy.app import App

    monkeypatch.setattr(
        "capabledeputy.daemon.approval_handlers.load_settings",
        lambda: DaemonSettings(require_touch_id_for_high_risk=True),
    )
    app = App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")
    request = await app.approval_queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.QUEUE_PURCHASE,
        payload="buy",
        target="vendor",
        labels_in=LabelState(),
        justification="test",
    )

    async def authenticate(_request):
        app.approval_queue.supersede(request.id)
        return {"method": "test"}

    monkeypatch.setattr(
        "capabledeputy.daemon.approval_handlers.authenticate_approval", authenticate
    )
    with pytest.raises(ValueError, match="changed or expired"):
        await make_approval_handlers(app)["approval.approve"]({"id": request.id})
    assert app.approval_queue.get(request.id).status == ApprovalStatus.SUPERSEDED


async def test_a_valid_response_cannot_be_replayed_for_another_ceremony(tmp_path, monkeypatch):
    proof_file = tmp_path / "old-proof.json"
    install_fixture_helper(
        tmp_path,
        monkeypatch,
        "import json,sys,pathlib\nr=json.load(sys.stdin)\n"
        f"p=pathlib.Path({str(proof_file)!r})\n"
        "if not p.exists(): p.write_text(json.dumps({'nonce':r['nonce'],"
        "'binding':r['binding'],'authenticated':True}))\n"
        "print(p.read_text())\n",
    )
    request = _request(ApprovalAction.QUEUE_PURCHASE)
    await authenticate_approval(request)
    with pytest.raises(ValueError, match="failed"):
        await authenticate_approval(request)


@pytest.mark.parametrize("change", ["expiry", "draft", "account", "source", "deny", "labels"])
async def test_email_facts_are_revalidated_after_authentication(tmp_path, monkeypatch, change):
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from capabledeputy.policy.labels import CategoryTag
    from capabledeputy.policy.tiers import Tier
    from tests.test_email_mvp_acceptance import create_reply, email_daemon, review, summarize

    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        pending = await review(client, session_id, draft)
        monkeypatch.setattr(
            "capabledeputy.daemon.approval_handlers.load_settings",
            lambda: DaemonSettings(require_touch_id_for_high_risk=True),
        )
        monkeypatch.setattr(
            "capabledeputy.daemon.approval_handlers._approval_requires_strong_auth",
            lambda _: True,
        )
        handlers = make_approval_handlers(app)

        async def authenticate(request):
            if change == "expiry":
                app.approval_queue.record(
                    replace(request, expires_at=datetime.now(UTC) - timedelta(seconds=1))
                )
            elif change == "draft":
                app.email_drafts.update(UUID(draft["id"]), body="Changed during authentication")
            elif change == "account":
                config = app._email_smtp_config
                assert config is not None
                app._email_smtp_config = replace(config, from_addr="changed@example.test")
            elif change == "source":
                saved = app.email_drafts.get(UUID(draft["id"]))
                assert saved is not None
                source = app.email_sources.get(saved.source["source_id"], UUID(session_id))
                assert source is not None
                app.email_sources.observe_mailbox(
                    source["provider_tool"], source["folder"], "new-mailbox-epoch"
                )
            elif change == "deny":
                await handlers["approval.deny"]({"id": request.id})
            else:
                await app.graph.add_tags(
                    UUID(session_id),
                    LabelState(a=frozenset({CategoryTag("business", Tier.REGULATED)})),
                )
            return {"method": "test", "review_digest": "synthetic"}

        monkeypatch.setattr(
            "capabledeputy.daemon.approval_handlers.authenticate_approval", authenticate
        )
        with pytest.raises(ValueError, match=r"changed|expired|unavailable"):
            await handlers["approval.approve"]({"id": pending["id"]})
        assert app.approval_queue.get(pending["id"]).status != ApprovalStatus.APPROVED
        assert transport.attempts == []
        assert app.email_operations is not None and app.email_operations.all() == []
