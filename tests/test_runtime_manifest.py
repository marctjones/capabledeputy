from typing import Any

from capabledeputy.config.manifest import RuntimeManifest
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.substrate.hook_registry import HookRegistry
from capabledeputy.tools.descriptors import (
    ToolDescriptor,
    ToolFlowDescriptor,
    ToolPolicyDescriptor,
    ToolRuntimeDescriptor,
)
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolRegistry, ToolResult
from capabledeputy.upstream.config import UpstreamServerConfig


async def _noop_handler(args: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(output={})


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="memory.read",
            description="read memory",
            capability_kind=CapabilityKind.READ_FS,
            handler=_noop_handler,
            target_arg="key",
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
        )
    )
    return registry


def test_runtime_manifest_compiles_registry_hooks_and_upstreams() -> None:
    hooks = HookRegistry()
    hooks.register("at_chokepoint.decision", object())
    manifest = RuntimeManifest.from_runtime(
        registry=_registry(),
        policy_context=PolicyContext(hook_registry=hooks),
        upstream_servers=(
            UpstreamServerConfig(name="fetch", command=("python", "-m", "fetch")),
        ),
    )

    assert manifest.validate().ok
    assert manifest.summary()["tools"] == 1
    assert manifest.summary()["upstream_servers"] == 1
    assert manifest.summary()["hooks"] == ["at_chokepoint.decision"]


def test_runtime_manifest_warns_about_non_strict_upstream() -> None:
    manifest = RuntimeManifest.from_runtime(
        registry=_registry(),
        upstream_servers=(
            UpstreamServerConfig(name="legacy", command=("legacy",), strict=False),
        ),
    )

    validation = manifest.validate()
    assert validation.ok
    assert any("not fail-closed" in issue.message for issue in validation.warnings)


def test_runtime_manifest_errors_on_handle_tool_without_handle_args() -> None:
    manifest = RuntimeManifest(
        tools=(
            ToolDescriptor(
                runtime=ToolRuntimeDescriptor(
                    name="bad.handle",
                    description="bad",
                    parameters_schema={},
                ),
                policy=ToolPolicyDescriptor(
                    capability_kind=CapabilityKind.READ_FS.value,
                    target_arg="target",
                    operations=(Operation(EffectClass.FETCH),),
                    risk_ids=("RISK-INDIRECT-INJECTION",),
                ),
                flow=ToolFlowDescriptor(accepts_handles=True),
            ),
        )
    )

    validation = manifest.validate()
    assert not validation.ok
    assert any(
        "accepts handles without handle args" in issue.message
        for issue in validation.errors
    )
