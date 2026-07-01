"""Deterministic per-turn MLX model role resolution."""

from __future__ import annotations

from dataclasses import dataclass

from capabledeputy.llm.models_config import ModelsConfig, RoutingRule
from capabledeputy.mode.dispatcher import ExecutionMode


@dataclass(frozen=True)
class ModelRoutingContext:
    purpose_handle: str
    execution_mode: ExecutionMode
    n_visible_tools: int
    n_selected_tools: int = 0
    user_message_chars: int = 0
    model_role_override: str | None = None


@dataclass(frozen=True)
class ModelRoutingResult:
    role: str
    reason: str
    mlx_model: str


def _rule_matches(rule: RoutingRule, ctx: ModelRoutingContext) -> bool:
    if rule.purpose_handle and ctx.purpose_handle not in rule.purpose_handle:
        return False
    if rule.execution_mode is not None:
        rule_token = rule.execution_mode.upper().replace("-", "_")
        if ctx.execution_mode.name.upper() != rule_token and ctx.execution_mode.value.upper() != rule_token:
            return False
    if rule.n_visible_tools_gte is not None:
        if ctx.n_visible_tools < rule.n_visible_tools_gte:
            return False
    if rule.n_selected_tools_gte is not None:
        if ctx.n_selected_tools < rule.n_selected_tools_gte:
            return False
    if rule.user_message_chars_gte is not None:
        if ctx.user_message_chars < rule.user_message_chars_gte:
            return False
    return True


def resolve_model_role(config: ModelsConfig, ctx: ModelRoutingContext) -> ModelRoutingResult:
    if ctx.model_role_override:
        role = ctx.model_role_override
        spec = config.role_spec(role)
        return ModelRoutingResult(role=role, reason="session_override", mlx_model=spec.mlx)

    for rule in config.routing:
        has_condition = (
            rule.purpose_handle
            or rule.execution_mode
            or rule.n_visible_tools_gte is not None
            or rule.n_selected_tools_gte is not None
            or rule.user_message_chars_gte is not None
        )
        if has_condition:
            if _rule_matches(rule, ctx):
                spec = config.role_spec(rule.role)
                return ModelRoutingResult(
                    role=rule.role,
                    reason=rule.reason,
                    mlx_model=spec.mlx,
                )
        else:
            spec = config.role_spec(rule.role)
            return ModelRoutingResult(
                role=rule.role,
                reason=rule.reason,
                mlx_model=spec.mlx,
            )

    spec = config.role_spec("planner.fast")
    return ModelRoutingResult(role="planner.fast", reason="fallback", mlx_model=spec.mlx)
