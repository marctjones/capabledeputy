"""Load operator-owned models.yaml for MLX role routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_MODELS_PATH = Path("configs/models.yaml")


@dataclass(frozen=True)
class ModelRoleSpec:
    mlx: str
    max_tokens: int = 2048


@dataclass(frozen=True)
class RoutingRule:
    role: str
    reason: str
    purpose_handle: frozenset[str] = field(default_factory=frozenset)
    execution_mode: str | None = None
    n_visible_tools_gte: int | None = None
    n_selected_tools_gte: int | None = None
    user_message_chars_gte: int | None = None


@dataclass(frozen=True)
class ToolSelectionConfig:
    mode: str = "retrieve"
    retrieval_top_k: int = 20
    ai_top_k: int = 12
    ai_gate_visible_gte: int = 15
    max_selected: int = 15


@dataclass(frozen=True)
class ModelsConfig:
    default_backend: str
    roles: dict[str, ModelRoleSpec]
    routing: tuple[RoutingRule, ...]
    tool_selection: ToolSelectionConfig

    def role_spec(self, role: str) -> ModelRoleSpec:
        spec = self.roles.get(role)
        if spec is None:
            raise KeyError(f"unknown model role {role!r}")
        return spec


def _parse_routing(raw_rules: list[Any]) -> tuple[RoutingRule, ...]:
    rules: list[RoutingRule] = []
    for entry in raw_rules:
        if not isinstance(entry, dict):
            continue
        if "default" in entry:
            default = entry["default"]
            if isinstance(default, dict):
                rules.append(
                    RoutingRule(
                        role=str(default.get("role", "planner.fast")),
                        reason=str(default.get("reason", "default")),
                    ),
                )
            continue
        when = entry.get("when")
        body = entry.get("role")
        reason = entry.get("reason", "rule_match")
        if not isinstance(when, dict) or not body:
            continue
        purpose_raw = when.get("purpose_handle")
        purpose: frozenset[str] = frozenset()
        if isinstance(purpose_raw, str):
            purpose = frozenset({purpose_raw})
        elif isinstance(purpose_raw, list):
            purpose = frozenset(str(v) for v in purpose_raw)
        rules.append(
            RoutingRule(
                role=str(body),
                reason=str(reason),
                purpose_handle=purpose,
                execution_mode=(
                    str(when["execution_mode"]) if when.get("execution_mode") else None
                ),
                n_visible_tools_gte=(
                    int(when["n_visible_tools_gte"])
                    if when.get("n_visible_tools_gte") is not None
                    else None
                ),
                n_selected_tools_gte=(
                    int(when["n_selected_tools_gte"])
                    if when.get("n_selected_tools_gte") is not None
                    else None
                ),
                user_message_chars_gte=(
                    int(when["user_message_chars_gte"])
                    if when.get("user_message_chars_gte") is not None
                    else None
                ),
            ),
        )
    if not rules:
        rules.append(RoutingRule(role="planner.fast", reason="builtin_default"))
    return tuple(rules)


def load_models_config(path: Path | None = None) -> ModelsConfig:
    config_path = path or DEFAULT_MODELS_PATH
    if not config_path.is_file():
        return _builtin_defaults()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{config_path}: expected a YAML mapping")

    roles_raw = raw.get("roles") or {}
    roles: dict[str, ModelRoleSpec] = {}
    if isinstance(roles_raw, dict):
        for role_name, spec in roles_raw.items():
            if not isinstance(spec, dict):
                continue
            mlx = spec.get("mlx")
            if not mlx:
                continue
            roles[str(role_name)] = ModelRoleSpec(
                mlx=str(mlx),
                max_tokens=int(spec.get("max_tokens") or 2048),
            )
    if not roles:
        return _builtin_defaults()

    ts_raw = raw.get("tool_selection") or {}
    tool_selection = ToolSelectionConfig(
        mode=str(ts_raw.get("mode", "retrieve")),
        retrieval_top_k=int(ts_raw.get("retrieval_top_k", 20)),
        ai_top_k=int(ts_raw.get("ai_top_k", 12)),
        ai_gate_visible_gte=int(ts_raw.get("ai_gate_visible_gte", 15)),
        max_selected=int(ts_raw.get("max_selected", 15)),
    )

    return ModelsConfig(
        default_backend=str(raw.get("default_backend", "mlx")),
        roles=roles,
        routing=_parse_routing(list(raw.get("routing") or [])),
        tool_selection=tool_selection,
    )


def _builtin_defaults() -> ModelsConfig:
    from capabledeputy.llm.mlx_client import DEFAULT_MLX_MODEL

    return ModelsConfig(
        default_backend="mlx",
        roles={
            "planner.fast": ModelRoleSpec(mlx=DEFAULT_MLX_MODEL, max_tokens=2048),
            "planner.tools": ModelRoleSpec(
                mlx="mlx-community/Qwen3-14B-4bit",
                max_tokens=4096,
            ),
            "planner.quality": ModelRoleSpec(
                mlx="mlx-community/Qwen3-30B-A3B-4bit",
                max_tokens=4096,
            ),
            "planner.coder": ModelRoleSpec(
                mlx="mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit",
                max_tokens=4096,
            ),
            "extractor": ModelRoleSpec(
                mlx="mlx-community/Phi-3.5-mini-instruct-4bit",
                max_tokens=1024,
            ),
        },
        routing=(
            RoutingRule(
                role="planner.quality",
                reason="long_quality_purpose",
                purpose_handle=frozenset({"research", "writing"}),
                user_message_chars_gte=900,
            ),
            RoutingRule(
                role="planner.tools",
                reason="purpose_handle_tier",
                purpose_handle=frozenset({"research", "writing"}),
            ),
            RoutingRule(
                role="planner.coder",
                reason="programmatic_mode",
                execution_mode="PROGRAMMATIC",
            ),
            RoutingRule(
                role="planner.tools",
                reason="large_tool_surface",
                n_visible_tools_gte=10,
            ),
            RoutingRule(
                role="planner.tools",
                reason="multi_tool_turn",
                n_selected_tools_gte=4,
            ),
            RoutingRule(role="planner.fast", reason="default_fast"),
        ),
        tool_selection=ToolSelectionConfig(),
    )
