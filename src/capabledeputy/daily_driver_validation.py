"""User-facing daily-driver workflow validation.

The daily-driver preset is useful only if concrete workflows are ready without
surprising authority. This module validates the workflow catalog against a
purpose/source-binding preset and returns a client-neutral report.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from capabledeputy.daemon.workflow_templates import build_workflow_templates
from capabledeputy.daily_driver import Gate
from capabledeputy.policy.bindings import BindingError
from capabledeputy.policy.bindings import load as load_bindings
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.purposes import load as load_purposes

_MAIL_READ_KINDS = frozenset({CapabilityKind.GMAIL_READ.value, CapabilityKind.IMAP_READ.value})
_MUTATING_KINDS = frozenset(
    {
        CapabilityKind.GMAIL_DRAFT.value,
        CapabilityKind.APPLE_MAIL_DRAFT.value,
        CapabilityKind.OUTLOOK_DRAFT.value,
        CapabilityKind.CREATE_CAL.value,
        CapabilityKind.MODIFY_CAL.value,
        CapabilityKind.DELETE_CAL.value,
        CapabilityKind.CREATE_FS.value,
        CapabilityKind.MODIFY_FS.value,
        CapabilityKind.DELETE_FS.value,
        CapabilityKind.PAGES_EDIT.value,
        CapabilityKind.PAGES_EXPORT.value,
        CapabilityKind.NUMBERS_EDIT.value,
        CapabilityKind.NUMBERS_EXPORT.value,
        CapabilityKind.KEYNOTE_PRESENT.value,
        CapabilityKind.WORD_EDIT.value,
        CapabilityKind.WORD_EXPORT.value,
        CapabilityKind.POWERPOINT_EDIT.value,
        CapabilityKind.POWERPOINT_EXPORT.value,
        CapabilityKind.POWERPOINT_PRESENT.value,
        CapabilityKind.BROWSER_INTERACT.value,
        CapabilityKind.BROWSER_FILE.value,
        CapabilityKind.MACOS_CLIPBOARD_WRITE.value,
        CapabilityKind.MACOS_APP_CONTROL.value,
        CapabilityKind.EXECUTE_SANDBOX.value,
        CapabilityKind.EXECUTE_DEVBOX.value,
    }
)
_DENIED_DEFAULT_KINDS = frozenset(
    {
        CapabilityKind.SEND_EMAIL.value,
        CapabilityKind.SEND_MESSAGE.value,
        CapabilityKind.BROWSER_SCRIPT.value,
        CapabilityKind.MACOS_AUTOMATION.value,
        CapabilityKind.BROWSER_AUTOMATION.value,
    }
)
_SOURCE_PORT_EXAMPLES: dict[str, str] = {
    "gmail": "gmail://thread/example",
    "imap": "imap://inbox/message/example",
    "google-calendar": "gcal://primary/event/example",
    "google-drive": "gdrive://file/example",
    "browser.current-page": "browser://active/page",
    "macos.frontmost-app": "macos://app/com.apple.TextEdit",
    "apple-mail": "applemail://inbox/message/example",
    "finder": "file:///Users/example/Documents/example.txt",
    "pages": "pages://frontmost",
    "numbers": "numbers://frontmost",
    "keynote": "keynote://frontmost",
    "web": "https://example.com/source",
}


@dataclass(frozen=True)
class WorkflowValidation:
    workflow_id: str
    title: str
    purpose_handle: str
    status: str
    launch_gate: Gate
    mutation_gate: Gate
    egress_gate: Gate
    review: str
    missing_capabilities: tuple[str, ...] = ()
    unbound_source_ports: tuple[str, ...] = ()
    forbidden_default_capabilities: tuple[str, ...] = ()
    retention_ok: bool = True

    @property
    def ok(self) -> bool:
        return self.status == "pass"

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "title": self.title,
            "purpose_handle": self.purpose_handle,
            "status": self.status,
            "ok": self.ok,
            "launch_gate": self.launch_gate.value,
            "mutation_gate": self.mutation_gate.value,
            "egress_gate": self.egress_gate.value,
            "review": self.review,
            "missing_capabilities": list(self.missing_capabilities),
            "unbound_source_ports": list(self.unbound_source_ports),
            "forbidden_default_capabilities": list(self.forbidden_default_capabilities),
            "retention_ok": self.retention_ok,
        }


def validate_daily_driver_workflows(
    *,
    preset_dir: Path = Path("configs/personal-assistant"),
) -> dict[str, Any]:
    purposes = load_purposes(preset_dir / "purposes.yaml")
    bindings = load_bindings(preset_dir / "source_bindings.yaml")
    workflow_catalog = build_workflow_templates()["templates"]
    results: list[WorkflowValidation] = []

    for workflow in workflow_catalog:
        purpose_handle = str(workflow["purpose_handle"])
        purpose = purposes.get(purpose_handle)
        purpose_capabilities = (
            {capability.kind.value for capability in purpose.default_capabilities}
            if purpose
            else set()
        )
        missing_capabilities = _missing_capabilities(
            workflow_capabilities=tuple(workflow["capabilities"]),
            purpose_capabilities=purpose_capabilities,
        )
        unbound_source_ports = _unbound_source_ports(
            tuple(workflow["source_ports"]),
            bindings=bindings,
        )
        forbidden_default_capabilities = tuple(sorted(purpose_capabilities & _DENIED_DEFAULT_KINDS))
        retention = workflow["retention"]
        retention_ok = (
            retention.get("source_context") == "session"
            and retention.get("artifacts") == "session"
            and retention.get("audit") == "durable"
        )
        has_mutation = bool(set(workflow["capabilities"]) & _MUTATING_KINDS)
        review = (
            "foreground_review_required"
            if workflow["requires_foreground_review"] or has_mutation
            else "operator_visible"
        )
        status = (
            "pass"
            if not (
                purpose is None
                or missing_capabilities
                or unbound_source_ports
                or forbidden_default_capabilities
                or not retention_ok
            )
            else "blocked"
        )
        results.append(
            WorkflowValidation(
                workflow_id=str(workflow["id"]),
                title=str(workflow["title"]),
                purpose_handle=purpose_handle,
                status=status,
                launch_gate=Gate.NO_APPROVAL
                if not workflow["requires_foreground_review"]
                else Gate.WARN,
                mutation_gate=Gate.REQUIRE_APPROVAL if has_mutation else Gate.NO_APPROVAL,
                egress_gate=Gate.REQUIRE_APPROVAL,
                review=review,
                missing_capabilities=missing_capabilities,
                unbound_source_ports=unbound_source_ports,
                forbidden_default_capabilities=forbidden_default_capabilities,
                retention_ok=retention_ok,
            )
        )

    blocked = [result.workflow_id for result in results if not result.ok]
    foreground_review = [
        result.workflow_id for result in results if result.review == "foreground_review_required"
    ]
    return {
        "schema": "capdep.daily_driver_workflow_validation.v1",
        "ready": not blocked,
        "workflow_count": len(results),
        "blocked": blocked,
        "foreground_review": foreground_review,
        "results": [result.as_dict() for result in results],
    }


def _missing_capabilities(
    *,
    workflow_capabilities: tuple[str, ...],
    purpose_capabilities: set[str],
) -> tuple[str, ...]:
    required = set(workflow_capabilities)
    missing = required - purpose_capabilities
    if required & _MAIL_READ_KINDS and purpose_capabilities & _MAIL_READ_KINDS:
        missing -= _MAIL_READ_KINDS
    return tuple(sorted(missing))


def _unbound_source_ports(source_ports: tuple[str, ...], *, bindings: Any) -> tuple[str, ...]:
    unbound: list[str] = []
    for source_port in source_ports:
        example = _SOURCE_PORT_EXAMPLES.get(source_port)
        if not example:
            unbound.append(source_port)
            continue
        try:
            bindings.resolve(example)
        except BindingError:
            unbound.append(source_port)
    return tuple(sorted(unbound))
