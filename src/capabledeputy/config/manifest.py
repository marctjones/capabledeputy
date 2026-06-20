"""Normalized runtime manifest for configured CapDep deployments.

The manifest is the architecture seam between user-facing YAML/Starlark/MCP
configuration and runtime services. It is intentionally inspectable and
side-effect free: compile it, validate it, show it to an operator, then start
the daemon from the already-normalized pieces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from capabledeputy.policy.context import PolicyContext
from capabledeputy.tools.descriptors import ToolDescriptor
from capabledeputy.tools.registry import ToolRegistry
from capabledeputy.upstream.config import UpstreamServerConfig


@dataclass(frozen=True)
class ManifestIssue:
    """One manifest validation finding."""

    severity: str
    subject: str
    message: str


@dataclass(frozen=True)
class ManifestValidation:
    """Validation report for a RuntimeManifest."""

    issues: tuple[ManifestIssue, ...] = ()

    @property
    def errors(self) -> tuple[ManifestIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[ManifestIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class RuntimeManifest:
    """Compiled, inspectable runtime configuration."""

    tools: tuple[ToolDescriptor, ...] = ()
    upstream_servers: tuple[UpstreamServerConfig, ...] = ()
    hook_names: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_runtime(
        cls,
        *,
        registry: ToolRegistry | None = None,
        policy_context: PolicyContext | None = None,
        upstream_servers: tuple[UpstreamServerConfig, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeManifest:
        hooks: tuple[str, ...] = ()
        if policy_context is not None:
            hooks = policy_context.effective_hook_registry().all_registered_hooks()
        return cls(
            tools=tuple(registry.descriptors()) if registry is not None else (),
            upstream_servers=tuple(upstream_servers),
            hook_names=hooks,
            metadata=dict(metadata or {}),
        )

    def validate(self) -> ManifestValidation:
        issues: list[ManifestIssue] = []
        seen_tools: set[str] = set()
        for tool in self.tools:
            if tool.name in seen_tools:
                issues.append(
                    ManifestIssue("error", tool.name, "duplicate registered tool name"),
                )
            seen_tools.add(tool.name)
            if not tool.policy.capability_kind:
                issues.append(
                    ManifestIssue("error", tool.name, "missing capability kind"),
                )
            if not tool.policy.operations:
                issues.append(
                    ManifestIssue("warning", tool.name, "no effect operations declared"),
                )
            if not tool.policy.risk_ids:
                issues.append(
                    ManifestIssue("warning", tool.name, "no risk IDs declared"),
                )
            if tool.flow.accepts_handles and not tool.flow.handle_arg_names:
                issues.append(
                    ManifestIssue("error", tool.name, "accepts handles without handle args"),
                )
            if tool.policy.target_template and tool.policy.target_arg != "target":
                issues.append(
                    ManifestIssue(
                        "warning",
                        tool.name,
                        "target_template makes target_arg redundant",
                    ),
                )

        seen_servers: set[str] = set()
        for server in self.upstream_servers:
            if server.name in seen_servers:
                issues.append(
                    ManifestIssue("error", server.name, "duplicate upstream server name"),
                )
            seen_servers.add(server.name)
            if not server.strict:
                issues.append(
                    ManifestIssue(
                        "warning",
                        server.name,
                        "upstream server is not fail-closed strict",
                    ),
                )
            if server.transport == "stdio" and not server.command:
                issues.append(
                    ManifestIssue("error", server.name, "stdio server has no command"),
                )
            if server.transport == "streamable_http" and not server.url:
                issues.append(
                    ManifestIssue("error", server.name, "HTTP server has no URL"),
                )
        return ManifestValidation(tuple(issues))

    def summary(self) -> dict[str, Any]:
        """Operator-facing summary without handlers or secrets."""
        validation = self.validate()
        return {
            "tools": len(self.tools),
            "upstream_servers": len(self.upstream_servers),
            "hooks": list(self.hook_names),
            "errors": [
                {"subject": i.subject, "message": i.message}
                for i in validation.errors
            ],
            "warnings": [
                {"subject": i.subject, "message": i.message}
                for i in validation.warnings
            ],
        }
