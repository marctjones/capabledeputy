"""Tests for the OPA sidecar adapter (spec 004 P3 follow-up)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest

from capabledeputy.policy.rules import Decision
from capabledeputy.substrate.decision_inspector_port import (
    DecisionRelax,
    DecisionTighten,
)
from capabledeputy.upstream.opa_adapter import OpaConsultingInspector


@dataclass
class _FakeKind:
    value: str


@dataclass
class _FakeAction:
    kind: _FakeKind
    target: str
    amount: int | None = None


@dataclass
class _FakeSession:
    id: str = "session-xyz"
    label_set: tuple = ()
    purpose_handle: str = "unset"
    clearance_profile_id: str | None = None


@dataclass
class _FakeProposed:
    decision: Decision
    rule: str = ""
    reason: str = ""


def _make_action(kind: str, target: str = "x") -> _FakeAction:
    return _FakeAction(kind=_FakeKind(kind), target=target)


def test_serialize_action_session_proposed() -> None:
    inspector = OpaConsultingInspector()
    doc = inspector._serialize(
        _make_action("READ_FS", target="/x"),
        _FakeSession(),
        _FakeProposed(decision=Decision.ALLOW),
    )
    assert doc["action"]["kind"] == "READ_FS"
    assert doc["action"]["target"] == "/x"
    assert doc["session"]["id"] == "session-xyz"
    assert doc["proposed_outcome"]["decision"] == "allow"


def test_serialize_with_extra_input() -> None:
    inspector = OpaConsultingInspector(extra_input={"env": "prod"})
    doc = inspector._serialize(
        _make_action("READ_FS"),
        _FakeSession(),
        _FakeProposed(decision=Decision.ALLOW),
    )
    assert doc["env"] == "prod"


def test_parse_response_relax() -> None:
    inspector = OpaConsultingInspector()
    result = inspector._parse_response(
        {
            "result": {
                "decision_inspector": "relax",
                "to": "allow",
                "rule": "operator-self-allow",
                "rationale": "ok",
            },
        },
    )
    assert isinstance(result, DecisionRelax)
    assert result.to == Decision.ALLOW
    assert result.rule == "operator-self-allow"


def test_parse_response_tighten() -> None:
    inspector = OpaConsultingInspector()
    result = inspector._parse_response(
        {
            "result": {
                "decision_inspector": "tighten",
                "to": "deny",
                "rule": "policy-x",
                "rationale": "blocked",
            },
        },
    )
    assert isinstance(result, DecisionTighten)
    assert result.to == Decision.DENY
    assert result.rule == "policy-x"


def test_parse_response_no_envelope() -> None:
    """OPA's response can also come without the 'result' envelope wrap."""
    inspector = OpaConsultingInspector()
    result = inspector._parse_response(
        {
            "decision_inspector": "relax",
            "to": "allow",
            "rule": "x",
        },
    )
    assert isinstance(result, DecisionRelax)


def test_parse_response_unknown_kind_abstains() -> None:
    inspector = OpaConsultingInspector()
    result = inspector._parse_response(
        {"result": {"decision_inspector": "explode", "to": "allow"}},
    )
    assert result is None


def test_parse_response_missing_to_abstains() -> None:
    inspector = OpaConsultingInspector()
    result = inspector._parse_response(
        {"result": {"decision_inspector": "relax"}},
    )
    assert result is None


def test_parse_response_unknown_decision_abstains() -> None:
    inspector = OpaConsultingInspector()
    result = inspector._parse_response(
        {
            "result": {
                "decision_inspector": "relax",
                "to": "not-a-real-decision",
            },
        },
    )
    assert result is None


def test_parse_response_non_dict_abstains() -> None:
    inspector = OpaConsultingInspector()
    result = inspector._parse_response({"result": ["a", "b"]})
    assert result is None


@pytest.mark.asyncio
async def test_inspect_returns_none_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network failures fail-closed (no opinion)."""

    class _FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", _FailingAsyncClient)
    inspector = OpaConsultingInspector()
    result = await inspector.inspect(
        action=_make_action("READ_FS"),
        session=_FakeSession(),
        proposed_outcome=_FakeProposed(decision=Decision.ALLOW),
    )
    assert result is None


@pytest.mark.asyncio
async def test_inspect_routes_through_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: post to OPA, parse the relax response."""
    captured_url = []

    class _MockResponse:
        status_code = 200

        def json(self):
            return {
                "result": {
                    "decision_inspector": "relax",
                    "to": "allow",
                    "rule": "ok",
                },
            }

    class _MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            captured_url.append(url)
            return _MockResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)
    inspector = OpaConsultingInspector(
        endpoint="http://localhost:8181",
        package="capabledeputy.policy",
    )
    result = await inspector.inspect(
        action=_make_action("READ_FS"),
        session=_FakeSession(),
        proposed_outcome=_FakeProposed(decision=Decision.REQUIRE_APPROVAL),
    )
    assert isinstance(result, DecisionRelax)
    assert result.to == Decision.ALLOW
    # URL was constructed correctly
    assert captured_url[0] == "http://localhost:8181/v1/data/capabledeputy/policy"


@pytest.mark.asyncio
async def test_inspect_returns_none_on_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resp500:
        status_code = 500

        def json(self):
            return {}

    class _ErrClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _Resp500()

    monkeypatch.setattr(httpx, "AsyncClient", _ErrClient)
    inspector = OpaConsultingInspector()
    result = await inspector.inspect(
        action=_make_action("READ_FS"),
        session=_FakeSession(),
        proposed_outcome=_FakeProposed(decision=Decision.ALLOW),
    )
    assert result is None
