"""Daily-driver policy defaults and readiness contracts.

This module is intentionally data-oriented. It gives setup, docs, tests, and
clients one place to ask what the default desktop-assistant posture is without
moving any authority out of the daemon policy chokepoint.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from capabledeputy.policy.capabilities import CapabilityKind


class Gate(StrEnum):
    NO_APPROVAL = "no_approval"
    WARN = "warn"
    REQUIRE_APPROVAL = "require_approval"
    OVERRIDE_REQUIRED = "override_required"
    DENY = "deny"


class Retention(StrEnum):
    TRANSIENT = "transient"
    REDACTED = "redacted"
    METADATA = "metadata"
    ARTIFACT = "artifact"


@dataclass(frozen=True)
class PolicyMatrixEntry:
    workflow: str
    gate: Gate
    examples: tuple[str, ...]
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "gate": self.gate.value,
            "examples": list(self.examples),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class ToolCatalogEntry:
    tool_id: str
    family: str
    server_names: tuple[str, ...]
    capability_kinds: tuple[CapabilityKind, ...]
    inherent_labels: tuple[str, ...] = ()
    target_requirement: str = "tool-specific target metadata preferred"
    required: bool = False
    intentionally_disabled: bool = False
    repair_hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "family": self.family,
            "server_names": list(self.server_names),
            "capability_kinds": [kind.value for kind in self.capability_kinds],
            "inherent_labels": list(self.inherent_labels),
            "target_requirement": self.target_requirement,
            "required": self.required,
            "intentionally_disabled": self.intentionally_disabled,
            "repair_hint": self.repair_hint,
        }


@dataclass(frozen=True)
class ToolReadiness:
    entry: ToolCatalogEntry
    status: str
    configured_servers: tuple[str, ...] = ()
    missing_capability_kinds: tuple[str, ...] = ()
    missing_target_metadata: bool = False
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"available", "disabled_by_policy", "optional_missing"}

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.entry.as_dict(),
            "status": self.status,
            "ok": self.ok,
            "configured_servers": list(self.configured_servers),
            "missing_capability_kinds": list(self.missing_capability_kinds),
            "missing_target_metadata": self.missing_target_metadata,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RetentionRule:
    data_class: str
    default: Retention
    durable_metadata: tuple[str, ...]
    raw_payload_allowed_when: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "data_class": self.data_class,
            "default": self.default.value,
            "durable_metadata": list(self.durable_metadata),
            "raw_payload_allowed_when": self.raw_payload_allowed_when,
        }


POLICY_MATRIX: tuple[PolicyMatrixEntry, ...] = (
    PolicyMatrixEntry(
        workflow="allowed-root reads and summaries",
        gate=Gate.NO_APPROVAL,
        examples=("read Documents/notes/GitHub", "summarize mailbox or document content"),
        rationale="Read-only context gathering is the core useful daily-driver path.",
    ),
    PolicyMatrixEntry(
        workflow="web research and image generation",
        gate=Gate.NO_APPROVAL,
        examples=("fetch https content", "generate or fetch image artifacts"),
        rationale="External content is labeled untrusted; later egress gates catch unsafe reuse.",
    ),
    PolicyMatrixEntry(
        workflow="scratch and draft creation",
        gate=Gate.NO_APPROVAL,
        examples=("write notes/scratch", "create local draft artifact"),
        rationale="Reversible operator-owned draft locations should not create approval fatigue.",
    ),
    PolicyMatrixEntry(
        workflow="broad local scans or first context sources",
        gate=Gate.WARN,
        examples=("large directory scan", "clipboard read", "screen/current-window read"),
        rationale="Reads may be safe but surprising; warn without turning every read into a gate.",
    ),
    PolicyMatrixEntry(
        workflow="external or state-changing actions",
        gate=Gate.REQUIRE_APPROVAL,
        examples=(
            "send email or chat",
            "calendar create/modify/delete/respond",
            "browser form submit, upload, download, account mutation",
            "clipboard write",
            "office edit/export",
            "write outside scratch roots",
            "sandbox/devbox with network or mounted user files",
        ),
        rationale="The user must see target, payload/effect, and labels before real state changes.",
    ),
    PolicyMatrixEntry(
        workflow="sensitive declassification or generic automation",
        gate=Gate.OVERRIDE_REQUIRED,
        examples=(
            "confidential/financial/health egress to non-trusted destinations",
            "generic browser scripting",
            "generic AppleScript, VBA, macros, or shell",
            "broad persistent capabilities",
            "unclassified MCP tools",
        ),
        rationale="Ordinary approval is not enough when the request changes the trust boundary.",
    ),
    PolicyMatrixEntry(
        workflow="structural non-goals",
        gate=Gate.DENY,
        examples=(
            "credential exfiltration",
            "arbitrary shell outside sandbox",
            "silent destructive mutation",
            "model-sidecar authorization or declassification",
        ),
        rationale="These bypass the reference monitor or turn advisory components into authority.",
    ),
)


DAILY_DRIVER_TOOL_CATALOG: tuple[ToolCatalogEntry, ...] = (
    ToolCatalogEntry(
        tool_id="local-files",
        family="filesystem",
        server_names=("bundled-fs",),
        capability_kinds=(
            CapabilityKind.READ_FS,
            CapabilityKind.CREATE_FS,
            CapabilityKind.WRITE_FS,
            CapabilityKind.DELETE_FS,
        ),
        required=True,
        repair_hint="Run capdep-setup assistant-surface --apply.",
    ),
    ToolCatalogEntry(
        tool_id="web-search-fetch",
        family="web",
        server_names=("bundled-fetch", "bundled-search", "kagi"),
        capability_kinds=(CapabilityKind.WEB_FETCH,),
        inherent_labels=("untrusted.external",),
        required=True,
        repair_hint=(
            "Run capdep-setup assistant-surface --apply; optionally configure KAGI_API_KEY."
        ),
    ),
    ToolCatalogEntry(
        tool_id="memory",
        family="memory",
        server_names=("bundled-memory",),
        capability_kinds=(
            CapabilityKind.READ_FS,
            CapabilityKind.CREATE_FS,
            CapabilityKind.WRITE_FS,
        ),
        repair_hint="Run capdep-setup assistant-surface --apply.",
    ),
    ToolCatalogEntry(
        tool_id="git-read",
        family="code-workspace",
        server_names=("bundled-git",),
        capability_kinds=(CapabilityKind.READ_FS,),
        repair_hint="Run capdep-setup assistant-surface --apply.",
    ),
    ToolCatalogEntry(
        tool_id="browser",
        family="browser",
        server_names=("playwright", "browser", "bundled-browser"),
        capability_kinds=(
            CapabilityKind.BROWSER_READ,
            CapabilityKind.BROWSER_NAVIGATE,
            CapabilityKind.BROWSER_INTERACT,
            CapabilityKind.BROWSER_SCRIPT,
            CapabilityKind.BROWSER_FILE,
        ),
        inherent_labels=("untrusted.external",),
        target_requirement=(
            "read/navigate/interact/script/file tools must expose target_arg or target_template"
        ),
        repair_hint=(
            "Install or admit a curated browser MCP server; keep generic script tools "
            "override-gated."
        ),
    ),
    ToolCatalogEntry(
        tool_id="screen-context",
        family="desktop-context",
        server_names=("bundled-screen", "screen-context", "sourceports"),
        capability_kinds=(CapabilityKind.READ_FS,),
        inherent_labels=("confidential.personal",),
        target_requirement="screen/current-window source must be read-only and labeled",
        repair_hint="Enable a read-only SourcePort/screen context server when available.",
    ),
    ToolCatalogEntry(
        tool_id="gmail",
        family="messaging",
        server_names=("google-gmail",),
        capability_kinds=(CapabilityKind.GMAIL_READ, CapabilityKind.GMAIL_DRAFT),
        inherent_labels=("confidential.personal", "untrusted.user_input"),
        target_requirement="draft tools must extract recipient as target",
        repair_hint="Run capdep-setup google-workspace --services gmail --apply and connect OAuth.",
    ),
    ToolCatalogEntry(
        tool_id="apple-mail",
        family="messaging",
        server_names=("bundled-apple-mail",),
        capability_kinds=(CapabilityKind.APPLE_MAIL_READ, CapabilityKind.APPLE_MAIL_DRAFT),
        inherent_labels=("confidential.personal", "untrusted.user_input"),
        target_requirement="draft tools must extract recipient as target",
        repair_hint="Run capdep-setup office-automation --apply and grant macOS Automation.",
    ),
    ToolCatalogEntry(
        tool_id="outlook",
        family="messaging",
        server_names=("bundled-outlook",),
        capability_kinds=(CapabilityKind.OUTLOOK_READ, CapabilityKind.OUTLOOK_DRAFT),
        inherent_labels=("confidential.personal", "untrusted.user_input"),
        target_requirement="draft tools must extract recipient as target",
        repair_hint="Run capdep-setup office-automation --apply and grant macOS Automation.",
    ),
    ToolCatalogEntry(
        tool_id="direct-send",
        family="messaging",
        server_names=("google-gmail", "chat", "slack", "outlook"),
        capability_kinds=(CapabilityKind.SEND_EMAIL, CapabilityKind.SEND_MESSAGE),
        intentionally_disabled=True,
        repair_hint="Keep direct sends disabled by default; use draft/preview plus approval.",
    ),
    ToolCatalogEntry(
        tool_id="calendar",
        family="calendar",
        server_names=("google-calendar",),
        capability_kinds=(
            CapabilityKind.CALENDAR_READ,
            CapabilityKind.CREATE_CAL,
            CapabilityKind.MODIFY_CAL,
            CapabilityKind.DELETE_CAL,
        ),
        target_requirement="mutation tools must materialize calendar/event/attendee targets",
        repair_hint="Connect Google Calendar and verify mutation scopes before enabling writes.",
    ),
    ToolCatalogEntry(
        tool_id="drive-docs",
        family="documents",
        server_names=("google-drive",),
        capability_kinds=(CapabilityKind.DRIVE_READ,),
        inherent_labels=("confidential.personal", "untrusted.user_input"),
        repair_hint="Run capdep-setup google-workspace --services drive --apply and connect OAuth.",
    ),
    ToolCatalogEntry(
        tool_id="office-documents",
        family="documents",
        server_names=(
            "bundled-pages",
            "bundled-numbers",
            "bundled-keynote",
            "bundled-word",
            "bundled-powerpoint",
        ),
        capability_kinds=(
            CapabilityKind.PAGES_READ,
            CapabilityKind.PAGES_EDIT,
            CapabilityKind.PAGES_EXPORT,
            CapabilityKind.NUMBERS_READ,
            CapabilityKind.NUMBERS_EDIT,
            CapabilityKind.NUMBERS_EXPORT,
            CapabilityKind.KEYNOTE_READ,
            CapabilityKind.KEYNOTE_PRESENT,
            CapabilityKind.WORD_READ,
            CapabilityKind.WORD_EDIT,
            CapabilityKind.WORD_EXPORT,
            CapabilityKind.POWERPOINT_READ,
            CapabilityKind.POWERPOINT_EDIT,
            CapabilityKind.POWERPOINT_EXPORT,
            CapabilityKind.POWERPOINT_PRESENT,
        ),
        target_requirement="edit/export/present tools must use app-specific targets",
        repair_hint="Run capdep-setup office-automation --apply.",
    ),
    ToolCatalogEntry(
        tool_id="macos-bounded",
        family="desktop-control",
        server_names=("bundled-macos",),
        capability_kinds=(
            CapabilityKind.MACOS_APP_CONTROL,
            CapabilityKind.MACOS_CLIPBOARD_READ,
            CapabilityKind.MACOS_CLIPBOARD_WRITE,
            CapabilityKind.MACOS_NOTIFICATION,
        ),
        target_requirement="clipboard/app tools must expose explicit macos:// targets",
        repair_hint="Run capdep-setup office-automation --apply; avoid broad MACOS_AUTOMATION.",
    ),
    ToolCatalogEntry(
        tool_id="image-generation",
        family="media",
        server_names=("image-generate", "bundled-image", "capdep-image"),
        capability_kinds=(CapabilityKind.GENERATE_IMAGE, CapabilityKind.FETCH_IMAGE),
        repair_hint="Run capdep-setup images and capdep-setup models.",
    ),
    ToolCatalogEntry(
        tool_id="safe-scripting",
        family="execution",
        server_names=("sandbox", "devbox", "bundled-sandbox"),
        capability_kinds=(CapabilityKind.EXECUTE_SANDBOX, CapabilityKind.EXECUTE_DEVBOX),
        target_requirement="execution tools must declare region, mounts, network, and persistence",
        repair_hint="Run capdep-setup sandbox; keep shell outside sandbox denied.",
    ),
)


RETENTION_RULES: tuple[RetentionRule, ...] = (
    RetentionRule(
        data_class="policy decisions and approvals",
        default=Retention.METADATA,
        durable_metadata=(
            "decision",
            "rule",
            "action",
            "target",
            "labels",
            "capability",
            "audit_id",
        ),
        raw_payload_allowed_when=(
            "only redacted preview or explicit operator-saved approval artifact"
        ),
    ),
    RetentionRule(
        data_class="email, chat, document, browser, clipboard, and screen content",
        default=Retention.REDACTED,
        durable_metadata=("source_uri", "label_state", "content_hash", "artifact_id"),
        raw_payload_allowed_when="user explicitly saves a draft, note, or exported artifact",
    ),
    RetentionRule(
        data_class="secrets and credentials",
        default=Retention.TRANSIENT,
        durable_metadata=("credential_ref", "server_id", "scope_status"),
        raw_payload_allowed_when="never",
    ),
    RetentionRule(
        data_class="scratch outputs and generated media",
        default=Retention.ARTIFACT,
        durable_metadata=("artifact_path", "label_state", "model_profile", "content_hash"),
        raw_payload_allowed_when="artifact is created in a configured scratch/draft location",
    ),
)


_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
)


def _redact_text(value: str) -> tuple[str, bool]:
    redacted = value
    changed = False
    for pattern in _SECRET_PATTERNS:
        redacted, count = pattern.subn(
            lambda m: f"{m.group(1) if m.groups() else 'secret'}=<redacted>", redacted
        )
        changed = changed or count > 0
    return redacted, changed


def approval_preview(
    *,
    action: str,
    target: str = "",
    payload: str = "",
    tool: str = "",
    capability: str = "",
    labels: tuple[str, ...] = (),
    max_payload_chars: int = 500,
) -> dict[str, Any]:
    """Build an operator-facing approval preview contract.

    The return value is client-neutral: GUI, CLI, TUI, and MCP-control can
    render the same fields without re-deriving policy semantics.
    """

    redacted_payload, redacted = _redact_text(payload)
    truncated = len(redacted_payload) > max_payload_chars
    preview = redacted_payload[:max_payload_chars] + ("..." if truncated else "")
    lower = action.lower()
    state_changing = any(
        marker in lower
        for marker in ("send", "draft", "create", "modify", "delete", "write", "export", "present")
    )
    irreversible = any(marker in lower for marker in ("delete", "send", "purchase"))
    destination = target if "@" in target or "://" in target else ""
    return {
        "action": action,
        "target": target,
        "tool": tool,
        "capability": capability,
        "labels": list(labels),
        "destination": destination,
        "state_changing": state_changing,
        "irreversible": irreversible,
        "payload_preview": preview,
        "payload_redacted": redacted,
        "payload_truncated": truncated,
        "decision_options": ["approve", "deny", "edit_and_retry", "cancel"],
    }


def audit_minimized_payload(
    *,
    action: str,
    target: str = "",
    labels: tuple[str, ...] = (),
    payload: str = "",
    artifact_id: str = "",
) -> dict[str, Any]:
    """Return durable audit metadata without storing raw sensitive payloads."""

    redacted_payload, redacted = _redact_text(payload)
    return {
        "action": action,
        "target": target,
        "labels": list(labels),
        "artifact_id": artifact_id,
        "payload_present": bool(payload),
        "payload_redacted": redacted or bool(payload),
        "payload_preview": redacted_payload[:120] if payload else "",
    }


def load_daemon_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _server_maps(config: dict[str, Any]) -> tuple[set[str], dict[str, set[str]], dict[str, bool]]:
    names: set[str] = set()
    kinds_by_server: dict[str, set[str]] = {}
    target_metadata_by_server: dict[str, bool] = {}
    for server in config.get("upstream_servers", []) or []:
        if not isinstance(server, dict):
            continue
        name = str(server.get("name") or "")
        if not name:
            continue
        names.add(name)
        kinds: set[str] = set()
        has_target = False
        overrides = server.get("tool_overrides") or {}
        if isinstance(overrides, dict):
            for override in overrides.values():
                if not isinstance(override, dict):
                    continue
                kind = override.get("capability_kind")
                if kind:
                    kinds.add(str(kind))
                if override.get("target_arg") or override.get("target_template"):
                    has_target = True
        disabled = server.get("disabled_kinds") or []
        if isinstance(disabled, list):
            kinds.difference_update(str(kind) for kind in disabled)
        kinds_by_server[name] = kinds
        target_metadata_by_server[name] = has_target
    return names, kinds_by_server, target_metadata_by_server


def evaluate_tool_readiness(
    config_path: Path,
    *,
    catalog: tuple[ToolCatalogEntry, ...] = DAILY_DRIVER_TOOL_CATALOG,
) -> tuple[ToolReadiness, ...]:
    config = load_daemon_config(config_path)
    server_names, kinds_by_server, target_metadata_by_server = _server_maps(config)
    results: list[ToolReadiness] = []
    for entry in catalog:
        configured = tuple(name for name in entry.server_names if name in server_names)
        if entry.intentionally_disabled:
            status = "disabled_by_policy"
            detail = entry.repair_hint
            missing_kinds: tuple[str, ...] = ()
            missing_target = False
        elif not configured:
            status = "missing_required" if entry.required else "optional_missing"
            detail = entry.repair_hint
            missing_kinds = tuple(kind.value for kind in entry.capability_kinds)
            missing_target = False
        else:
            present_kinds: set[str] = set()
            target_ok = False
            for server_name in configured:
                present_kinds.update(kinds_by_server.get(server_name, set()))
                target_ok = target_ok or target_metadata_by_server.get(server_name, False)
            required_kinds = {kind.value for kind in entry.capability_kinds}
            missing = tuple(sorted(required_kinds - present_kinds))
            target_requirement = entry.target_requirement.lower()
            target_sensitive = "must" in target_requirement and any(
                word in target_requirement
                for word in ("target", "recipient", "attendee", "mutation")
            )
            missing_target = target_sensitive and not target_ok
            if missing:
                status = "degraded"
                detail = "Configured server is missing expected capability mappings."
            elif missing_target:
                status = "degraded"
                detail = "Configured server is missing target metadata for approval/audit."
            else:
                status = "available"
                detail = "Configured and admitted."
            missing_kinds = missing
        results.append(
            ToolReadiness(
                entry=entry,
                status=status,
                configured_servers=configured,
                missing_capability_kinds=missing_kinds,
                missing_target_metadata=missing_target,
                detail=detail,
            )
        )
    return tuple(results)


def readiness_summary(results: tuple[ToolReadiness, ...]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    blocking = [
        result.entry.tool_id
        for result in results
        if result.status in {"missing_required", "degraded"} and result.entry.required
    ]
    degraded = [result.entry.tool_id for result in results if result.status == "degraded"]
    return {
        "schema": "capdep.daily_driver_readiness.v1",
        "counts": counts,
        "blocking": blocking,
        "degraded": degraded,
        "ready": not blocking,
    }


def relationship_groups_yaml(
    *,
    self_addresses: tuple[str, ...] = (),
    trusted_draft_recipients: tuple[str, ...] = (),
    family_recipients: tuple[str, ...] = (),
    work_recipients: tuple[str, ...] = (),
) -> str:
    groups = [
        {"group_id": "self", "member_principal_ids": sorted(set(self_addresses))},
        {
            "group_id": "trusted-draft",
            "member_principal_ids": sorted(set(self_addresses + trusted_draft_recipients)),
        },
        {"group_id": "family", "member_principal_ids": sorted(set(family_recipients))},
        {"group_id": "work-team", "member_principal_ids": sorted(set(work_recipients))},
    ]
    return yaml.safe_dump({"groups": groups}, sort_keys=False)


def approval_patterns_yaml(
    *,
    self_addresses: tuple[str, ...] = (),
    trusted_draft_recipients: tuple[str, ...] = (),
    ttl_hours: int = 720,
) -> str:
    patterns: list[dict[str, Any]] = []
    for address in sorted(set(self_addresses)):
        patterns.append(
            {
                "name": f"self-gmail-draft-{address}",
                "action": "GMAIL_DRAFT",
                "target_pattern": address,
                "ttl_hours": ttl_hours,
                "created_by": "setup:daily-driver",
            }
        )
        patterns.append(
            {
                "name": f"self-apple-mail-draft-{address}",
                "action": "APPLE_MAIL_DRAFT",
                "target_pattern": address,
                "ttl_hours": ttl_hours,
                "created_by": "setup:daily-driver",
            }
        )
    for address in sorted(set(trusted_draft_recipients)):
        patterns.append(
            {
                "name": f"trusted-gmail-draft-{address}",
                "action": "GMAIL_DRAFT",
                "target_pattern": address,
                "ttl_hours": ttl_hours,
                "created_by": "setup:daily-driver",
            }
        )
    return yaml.safe_dump({"patterns": patterns}, sort_keys=False)


def write_daily_driver_identity_files(
    *,
    directory: Path,
    self_addresses: tuple[str, ...],
    trusted_draft_recipients: tuple[str, ...] = (),
    family_recipients: tuple[str, ...] = (),
    work_recipients: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    relationships_path = directory / "relationship_groups.yaml"
    approval_patterns_path = directory / "approval-patterns.yaml"
    relationships_path.write_text(
        relationship_groups_yaml(
            self_addresses=self_addresses,
            trusted_draft_recipients=trusted_draft_recipients,
            family_recipients=family_recipients,
            work_recipients=work_recipients,
        ),
        encoding="utf-8",
    )
    approval_patterns_path.write_text(
        approval_patterns_yaml(
            self_addresses=self_addresses,
            trusted_draft_recipients=trusted_draft_recipients,
        ),
        encoding="utf-8",
    )
    return relationships_path, approval_patterns_path


def policy_contract_json() -> str:
    return json.dumps(
        {
            "schema": "capdep.daily_driver_policy.v1",
            "policy_matrix": [entry.as_dict() for entry in POLICY_MATRIX],
            "tool_catalog": [entry.as_dict() for entry in DAILY_DRIVER_TOOL_CATALOG],
            "retention_rules": [rule.as_dict() for rule in RETENTION_RULES],
        },
        indent=2,
    )
