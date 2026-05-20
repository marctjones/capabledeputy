"""Composition Sub-phase C — `capdep override` CLI (T080, Demo #2).

End-to-end via Typer's CliRunner. Exercises:
  - request → state ACTIVE for single-authorized
  - request → state PENDING_ATTESTATION for dual-control
  - attest distinct → state ACTIVE
  - attest self ⇒ refused
  - list / show subcommands

The grant store is module-level in override_cmd; tests reset it via
`_set_test_doubles` so each test starts clean.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from typer.testing import CliRunner

from capabledeputy.cli.override_cmd import (
    _set_test_doubles,
    override_app,
)
from capabledeputy.policy.overrides import (
    HardFloor,
    OverrideGrantStore,
    OverridePolicies,
    OverridePolicy,
    OverridePolicyEntry,
)


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    """Per-test clean store + policies."""
    _set_test_doubles(
        store=OverrideGrantStore(),
        policies=OverridePolicies(
            by_floor={
                HardFloor.MAX_TIER_CLEARANCE: OverridePolicyEntry(
                    floor=HardFloor.MAX_TIER_CLEARANCE,
                    policy=OverridePolicy.SINGLE_AUTHORIZED,
                    authorized_principal_ids=frozenset({"alice"}),
                    expiry_seconds=300,
                ),
                HardFloor.ADMISSIBILITY_EXCLUSION: OverridePolicyEntry(
                    floor=HardFloor.ADMISSIBILITY_EXCLUSION,
                    policy=OverridePolicy.DUAL_CONTROL,
                    authorized_principal_ids=frozenset({"alice"}),
                    attester_principal_ids=frozenset({"bob", "carol"}),
                    expiry_seconds=120,
                ),
                HardFloor.PROHIBITED: OverridePolicyEntry(
                    floor=HardFloor.PROHIBITED,
                    policy=OverridePolicy.DISALLOWED,
                ),
            },
        ),
    )


def _runner() -> CliRunner:
    return CliRunner()


def _request_single_authorized() -> str:
    """Issue a single-authorized grant, return its id (stdout-parsed)."""
    runner = _runner()
    result = runner.invoke(
        override_app,
        [
            "request",
            "--session-id",
            str(uuid4()),
            "--action-kind",
            "SEND_EMAIL",
            "--target",
            "alice@example.com",
            "--floor",
            "max-tier-clearance",
            "--invoker",
            "alice",
            "--friction-confirmed",
        ],
    )
    assert result.exit_code == 0, result.output
    # Output contains "grant issued: <UUID>"
    for line in result.output.splitlines():
        if "grant issued:" in line:
            return line.split(":")[-1].strip()
    raise AssertionError(f"could not parse grant id from output: {result.output}")


def test_single_authorized_request_succeeds() -> None:
    runner = _runner()
    sid = uuid4()
    result = runner.invoke(
        override_app,
        [
            "request",
            "--session-id",
            str(sid),
            "--action-kind",
            "SEND_EMAIL",
            "--target",
            "alice@example.com",
            "--floor",
            "max-tier-clearance",
            "--invoker",
            "alice",
            "--friction-confirmed",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "grant issued" in result.output
    assert "active" in result.output  # state


def test_unauthorized_invoker_refused() -> None:
    runner = _runner()
    result = runner.invoke(
        override_app,
        [
            "request",
            "--session-id",
            str(uuid4()),
            "--action-kind",
            "SEND_EMAIL",
            "--target",
            "alice@example.com",
            "--floor",
            "max-tier-clearance",
            "--invoker",
            "mallory",
            "--friction-confirmed",
        ],
    )
    assert result.exit_code == 1
    assert "REFUSED" in result.output
    assert "unauthorized_invoker" in result.output


def test_disallowed_floor_refuses_authorized_invoker() -> None:
    """PROHIBITED floor is DISALLOWED — even alice can't get a grant."""
    runner = _runner()
    result = runner.invoke(
        override_app,
        [
            "request",
            "--session-id",
            str(uuid4()),
            "--action-kind",
            "SEND_EMAIL",
            "--target",
            "weapons-spec",
            "--floor",
            "prohibited",
            "--invoker",
            "alice",
            "--friction-confirmed",
        ],
    )
    assert result.exit_code == 1
    assert "policy_disallowed" in result.output


def test_dual_control_request_pending_then_attested() -> None:
    runner = _runner()
    sid = uuid4()
    request_result = runner.invoke(
        override_app,
        [
            "request",
            "--session-id",
            str(sid),
            "--action-kind",
            "SEND_EMAIL",
            "--target",
            "secret-recipient",
            "--floor",
            "admissibility-exclusion",
            "--invoker",
            "alice",
            "--friction-confirmed",
        ],
    )
    assert request_result.exit_code == 0, request_result.output
    assert "pending_attestation" in request_result.output
    # Extract grant id
    grant_id = None
    for line in request_result.output.splitlines():
        if "grant issued:" in line:
            grant_id = line.split(":")[-1].strip()
            break
    assert grant_id is not None

    attest_result = runner.invoke(
        override_app,
        [
            "attest",
            "--grant-id",
            grant_id,
            "--attester",
            "bob",
            "--confirm",
        ],
    )
    assert attest_result.exit_code == 0, attest_result.output
    assert "active" in attest_result.output


def test_dual_control_self_attest_refused() -> None:
    """SC-014 — invoker cannot self-attest."""
    runner = _runner()
    request_result = runner.invoke(
        override_app,
        [
            "request",
            "--session-id",
            str(uuid4()),
            "--action-kind",
            "SEND_EMAIL",
            "--target",
            "x",
            "--floor",
            "admissibility-exclusion",
            "--invoker",
            "alice",
            "--friction-confirmed",
        ],
    )
    grant_id = None
    for line in request_result.output.splitlines():
        if "grant issued:" in line:
            grant_id = line.split(":")[-1].strip()
            break
    assert grant_id is not None

    attest_result = runner.invoke(
        override_app,
        [
            "attest",
            "--grant-id",
            grant_id,
            "--attester",
            "alice",  # same as invoker
            "--confirm",
        ],
    )
    assert attest_result.exit_code == 1
    assert "attester_same_as_invoker" in attest_result.output


def test_list_empty_then_populated() -> None:
    runner = _runner()
    result = runner.invoke(override_app, ["list"])
    assert "no grants" in result.output

    _request_single_authorized()
    result = runner.invoke(override_app, ["list"])
    assert "no grants" not in result.output


def test_show_unknown_grant_fails() -> None:
    runner = _runner()
    result = runner.invoke(override_app, ["show", str(uuid4())])
    assert result.exit_code == 1
    assert "unknown grant" in result.output


def test_refuse_marks_grant_refused() -> None:
    runner = _runner()
    grant_id = _request_single_authorized()
    result = runner.invoke(override_app, ["refuse", grant_id])
    assert result.exit_code == 0
    assert "refused" in result.output
