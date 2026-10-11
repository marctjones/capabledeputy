"""Privacy and fail-closed prerequisites of the opt-in provider recorder."""

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_spec = importlib.util.spec_from_file_location(
    "mail_live_recorder", Path(__file__).parents[1] / "scripts/test-supervised-mail-live.py"
)
assert _spec and _spec.loader
recorder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recorder)
SHA = "a" * 40


class Client:
    def __init__(self, *, revision=SHA, modes=None, fail=False):
        self.calls = []
        self.revision = revision
        self.modes = modes
        self.fail = fail

    async def call(self, method, params=None) -> dict[str, Any]:
        self.calls.append((method, params))
        if method == "daemon.code_version":
            return {
                "git_rev": self.revision,
                "git_dirty": "clean",
                "source_content_hash": recorder.source_content_hash(),
            }
        if method == "session.new":
            return {
                "id": "session",
                "required_modes": self.modes,
                "capability_set": [],
                "enforcement_mode": "strict",
            }
        if method == "session.send":
            if self.fail:
                raise RuntimeError("PRIVATE-MAIL-CONTENT")
            return {"tool_outcomes": [{"output": {"summarized_count": 2}}]}
        if method == "audit.list":
            return {"events": [{"event_type": "tool.returned", "payload": payload()}]}
        return {}


def payload():
    return {"output_redacted": True, "output_digest": "a" * 64, "output_size_bytes": 100}


def args(**changes):
    return argparse.Namespace(
        **dict(
            {
                "expected_revision": SHA,
                "live": True,
                "retention_reviewed": True,
                "required_mode": ["dual_llm"],
            },
            **changes,
        )
    )


def test_provider_strings_cannot_escape_metadata_projection():
    secret = "PRIVATE-MAIL-CONTENT"
    result = {
        "tool_outcomes": [
            {
                "tool_name": secret,
                "decision": secret,
                "error": secret,
                "output": {
                    "ok": secret,
                    "complete": secret,
                    "summarized_count": secret,
                    "matched_count": True,
                    "fetched_count": -1,
                    "body": secret,
                },
            }
        ]
    }
    projected = recorder.project_outcomes(result)
    assert secret not in json.dumps(projected)
    assert projected == [{"tool": "other", "decision": "other", "error": True}]


@pytest.mark.parametrize(
    "field,value",
    [
        ("output", "PRIVATE-MAIL-CONTENT"),
        ("output_redacted", False),
        ("output_digest", "PRIVATE-MAIL-CONTENT"),
        ("output_size_bytes", True),
    ],
)
def test_audit_content_or_invalid_commitment_fails(field, value):
    assert not recorder.audit_is_redacted(
        [{"event_type": "tool.returned", "payload": {**payload(), field: value}}]
    )
    assert not recorder.audit_is_redacted([])


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"live": False}, "preflight_only"),
        ({"retention_reviewed": False}, "blocked_privacy_or_exposure_review"),
        ({"required_mode": None}, "blocked_privacy_or_exposure_review"),
    ],
)
async def test_prerequisites_block_before_session_creation(changes, status):
    client = Client()
    report = {}
    await recorder.run(client, args(**changes), report)
    assert report["status"] == status
    assert [c[0] for c in client.calls] == ["daemon.code_version"]


async def test_old_daemon_blocks_real_data():
    client = Client(revision="b" * 40)
    report = {}
    await recorder.run(client, args(), report)
    assert report["status"] == "blocked_daemon_revision"
    assert len(client.calls) == 1


async def test_missing_constraint_aborts_before_read_grant():
    client = Client(modes=None)
    report = {}
    await recorder.run(client, args(), report)
    assert report["status"] == "blocked_exposure_constraint"
    assert [c[0] for c in client.calls] == ["daemon.code_version", "session.new", "session.abort"]


@pytest.mark.parametrize("fail", [False, True])
async def test_read_only_session_is_aborted_even_when_provider_fails(fail):
    client = Client(modes=["dual_llm"], fail=fail)
    report = {}
    if fail:
        with pytest.raises(RuntimeError):
            await recorder.run(client, args(), report)
    else:
        await recorder.run(client, args(), report)
        assert report["status"] == "read_completed_quality_unreviewed"
    assert client.calls[-1][0] == "session.abort"
    grants = [p for m, p in client.calls if m == "operator.grant_capability"]
    assert len(grants) == 1 and grants[0]["capability"]["kind"] == "IMAP_READ"
    assert all(m != "approval.approve" for m, _ in client.calls)


async def test_report_is_private_and_exception_text_is_not_exported(tmp_path, monkeypatch, capsys):
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "recorder",
            "--expected-revision",
            SHA,
            "--report",
            str(report_path),
            "--live",
            "--retention-reviewed",
            "--required-mode",
            "dual_llm",
        ],
    )
    monkeypatch.setattr(recorder, "DaemonClient", lambda _: Client(modes=["dual_llm"], fail=True))
    assert await recorder.main() == 1
    assert report_path.stat().st_mode & 0o777 == 0o600
    report = json.loads(report_path.read_text())
    assert report["status"] == "failed" and report["session_aborted"]
    assert "PRIVATE-MAIL-CONTENT" not in report_path.read_text() + capsys.readouterr().out
    original = report_path.read_bytes()
    with pytest.raises(FileExistsError):
        await recorder.main()
    assert report_path.read_bytes() == original


@pytest.mark.parametrize("posture", ["shadow", None])
async def test_non_strict_enforcement_prevents_live_read(posture):
    client = Client(modes=["dual_llm"])
    call = client.call

    async def altered(method, params=None):
        result = await call(method, params)
        if method == "session.new":
            result["enforcement_mode"] = posture
        return result

    client.call = altered
    report = {}
    await recorder.run(client, args(), report)
    assert report["status"] == "blocked_enforcement_posture"
    assert [c[0] for c in client.calls] == ["daemon.code_version", "session.new", "session.abort"]


async def test_matching_revision_with_changed_source_blocks_before_ingestion():
    client = Client()
    call = client.call

    async def stale(method, params=None):
        result = await call(method, params)
        if method == "daemon.code_version":
            result["source_content_hash"] = "b" * 64
        return result

    client.call = stale
    report = {}
    await recorder.run(client, args(), report)
    assert report["status"] == "blocked_daemon_source_content"
    assert len(client.calls) == 1


async def test_inherited_unbounded_read_authority_is_revoked_before_timed_grant():
    client = Client(modes=["dual_llm"])
    call = client.call

    async def inherited(method, params=None):
        result = await call(method, params)
        if method == "session.new":
            result["capability_set"] = [
                {"kind": "IMAP_READ", "audit_id": "inherited", "expires_at": None}
            ]
        return result

    client.call = inherited
    report = {}
    await recorder.run(client, args(), report)
    methods = [m for m, _ in client.calls]
    assert methods.index("capability.revoke") < methods.index("operator.grant_capability")
    assert report["inherited_read_grants_revoked"] == 1
    revoke = next(p for m, p in client.calls if m == "capability.revoke")
    assert revoke == {"session_id": "session", "audit_id": "inherited", "eager_teardown": True}
    grant = next(p for m, p in client.calls if m == "operator.grant_capability")
    assert grant["capability"]["expires_at"] is not None
