from __future__ import annotations

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.upstream.admission import preview_server_admission, preview_tool_admission
from capabledeputy.upstream.config import UpstreamServerConfig, UpstreamToolOverride


def test_preview_admits_readonly_tool() -> None:
    decision = preview_tool_admission(
        UpstreamServerConfig(name="files", command=("noop",)),
        {"name": "read_file", "annotations": {"readOnlyHint": True}},
    )

    assert decision["admitted"] is True
    assert decision["capability_kind"] == "READ_FS"
    assert decision["warnings"] == []


def test_preview_refuses_unclassifiable_tool_under_strict_mode() -> None:
    decision = preview_tool_admission(
        UpstreamServerConfig(name="mystery", command=("noop",), strict=True),
        {"name": "do_everything"},
    )

    assert decision["admitted"] is False
    assert "unclassifiable" in decision["reasons"][0]


def test_preview_refuses_disabled_kind_after_override() -> None:
    config = UpstreamServerConfig(
        name="gmail",
        command=("noop",),
        disabled_kinds=frozenset({"SEND_EMAIL"}),
        tool_overrides={
            "send_message": UpstreamToolOverride(capability_kind=CapabilityKind.SEND_EMAIL),
        },
    )

    decision = preview_tool_admission(config, {"name": "send_message"})

    assert decision["admitted"] is False
    assert "SEND_EMAIL" in decision["reasons"][0]


def test_preview_warns_effectful_tool_without_explicit_target_mapping() -> None:
    decision = preview_tool_admission(
        UpstreamServerConfig(name="web", command=("noop",)),
        {"name": "fetch_url"},
    )

    assert decision["admitted"] is True
    assert decision["capability_kind"] == "WEB_FETCH"
    assert "explicit target mapping" in decision["warnings"][0]


def test_preview_server_admission_summarizes_counts() -> None:
    summary = preview_server_admission(
        UpstreamServerConfig(name="mixed", command=("noop",)),
        [
            {"name": "read_file", "annotations": {"readOnlyHint": True}},
            {"name": "do_everything"},
        ],
    )

    assert summary["admitted_count"] == 1
    assert summary["refused_count"] == 1
