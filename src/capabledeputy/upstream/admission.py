"""MCP extension admission previews.

The production adapter already fails closed when it cannot classify an MCP
tool. This module makes the decision inspectable before registration so setup
and extension UIs can show which tools would be admitted, refused, disabled,
or missing target mapping.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from capabledeputy.policy.capabilities import DESTRUCTIVE_KINDS, CapabilityKind
from capabledeputy.upstream.adapter import _infer_capability_kind
from capabledeputy.upstream.config import UpstreamServerConfig

_EGRESS_KINDS: frozenset[CapabilityKind] = frozenset(
    {
        CapabilityKind.SEND_EMAIL,
        CapabilityKind.SEND_MESSAGE,
        CapabilityKind.WEB_FETCH,
        CapabilityKind.QUEUE_PURCHASE,
        CapabilityKind.GMAIL_DRAFT,
        CapabilityKind.APPLE_MAIL_DRAFT,
        CapabilityKind.CREATE_CAL,
        CapabilityKind.MODIFY_CAL,
        CapabilityKind.DELETE_CAL,
        CapabilityKind.PAGES_EDIT,
        CapabilityKind.PAGES_EXPORT,
        CapabilityKind.NUMBERS_EDIT,
        CapabilityKind.NUMBERS_EXPORT,
    },
)


def preview_tool_admission(
    config: UpstreamServerConfig,
    tool: dict[str, Any],
) -> dict[str, Any]:
    """Return an inspectable fail-closed admission decision for one MCP tool."""

    name = str(tool.get("name") or "").strip()
    if not name:
        return _refused("", "missing tool name")

    if name in config.disabled_tools:
        return _refused(name, "tool disabled by server config")

    override = config.tool_overrides.get(name)
    if override and override.capability_kind:
        kind: CapabilityKind | str | None = override.capability_kind
        inferred_from = "override"
    else:
        annotations = _coerce_annotations(tool.get("annotations"))
        kind = _infer_capability_kind(annotations, name)
        inferred_from = "annotations-and-name"

    if kind is None:
        if config.strict:
            return _refused(name, "unclassifiable tool under strict admission")
        kind = CapabilityKind.READ_FS
        inferred_from = "legacy-nonstrict-fallback"

    kind_name = getattr(kind, "value", str(kind))
    if kind_name in config.disabled_kinds:
        return _refused(name, f"capability kind {kind_name} disabled by server config")

    target_source = "target_arg:target"
    if override and override.target_template:
        target_source = "target_template"
    elif override and override.target_arg:
        target_source = f"target_arg:{override.target_arg}"

    warnings: list[str] = []
    if _requires_explicit_target(kind) and not (
        override and (override.target_arg or override.target_template)
    ):
        warnings.append("effectful tool uses default target_arg; add explicit target mapping")

    return {
        "name": name,
        "admitted": True,
        "status": "admitted",
        "capability_kind": kind_name,
        "inferred_from": inferred_from,
        "target_source": target_source,
        "warnings": warnings,
        "reasons": [],
    }


def preview_server_admission(
    config: UpstreamServerConfig,
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    decisions = [preview_tool_admission(config, tool) for tool in tools]
    return {
        "server": config.name,
        "strict": config.strict,
        "tool_count": len(decisions),
        "admitted_count": sum(1 for d in decisions if d["admitted"]),
        "refused_count": sum(1 for d in decisions if not d["admitted"]),
        "decisions": decisions,
    }


def _requires_explicit_target(kind: CapabilityKind | str) -> bool:
    if not isinstance(kind, CapabilityKind):
        return True
    return kind in DESTRUCTIVE_KINDS or kind in _EGRESS_KINDS


def _coerce_annotations(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "readOnlyHint") or hasattr(value, "destructiveHint"):
        return value
    if isinstance(value, dict):
        return SimpleNamespace(
            readOnlyHint=bool(value.get("readOnlyHint") or value.get("read_only")),
            destructiveHint=bool(value.get("destructiveHint") or value.get("destructive")),
        )
    return None


def _refused(name: str, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "admitted": False,
        "status": "refused",
        "capability_kind": "",
        "inferred_from": "",
        "target_source": "",
        "warnings": [],
        "reasons": [reason],
    }
