"""Tool definitions and registry.

A ToolDefinition pairs a tool name with the metadata the policy engine
needs (capability kind, inherent tags, how to extract target/amount
from call args) plus an async handler that does the actual work. The
registry holds these so the dispatcher can look them up by name.

Handlers receive a ToolContext with the calling session's id and label
set, and return a ToolResult whose `additional_tags` are unioned into
the session's label set after the call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from capabledeputy.approval.route import ApprovalRoute
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import LabelState


@dataclass(frozen=True)
class ToolContext:
    session_id: UUID
    label_state: LabelState


@dataclass(frozen=True)
class ToolResult:
    output: dict[str, Any]
    additional_tags: LabelState = field(default_factory=LabelState)


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    # Issue #35 — kind can be a built-in CapabilityKind enum member
    # OR a custom namespaced string registered via servers.d/*.yaml.
    # Both compare correctly to str at the chokepoint.
    capability_kind: CapabilityKind | str
    handler: ToolHandler
    target_arg: str = "target"
    amount_arg: str | None = None
    parameters_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []},
    )
    # How to authorize a REQUIRE_APPROVAL of this tool. None means the
    # tool is never expected to gate (or has no auto-submit path; the
    # user falls back to /submit).
    approval_route: ApprovalRoute | None = None
    # 003 US5 T012-partial — Pattern (3) Reference Handle opt-in.
    # When True, the dispatcher may substitute ReferenceHandle ids
    # into the named args. The handle store binds the real value
    # post-decide() (FR-047). Defaults False so tools opt into handle
    # substitution explicitly.
    accepts_handles: bool = False
    handle_arg_names: tuple[str, ...] = field(default_factory=tuple)
    # 003 T012-full — v2 four-axis decision fields.
    # When set, the v2 leg of engine.decide() consumes them.
    effect_class: str | None = None  # axis C; e.g., "data.read_file"
    default_reversibility: dict[str, str] | None = None  # {"degree": ..., "agent": ...}
    default_mutability_target_facets: tuple[str, ...] = field(default_factory=tuple)
    social_commitment: bool = False  # FR-019 hard-coded irreversible
    tool_provenance: str = "operator-curated"  # e.g., "operator-curated" | "mcp"
    surfaces_destination_id: bool = False  # FR-048 — port-backed canonical id
    risk_ids: tuple[str, ...] = field(default_factory=tuple)
    # `operations` is the set of Operations this tool performs (Axis C,
    # canonical EffectClass enum
    # + optional subtype + required_floor); `inherent_tags` are the Axis A
    # / Axis B labels its output inherently carries.
    operations: tuple[Operation, ...] = field(default_factory=tuple)
    inherent_tags: LabelState = field(default_factory=LabelState)
    # R7 — per-arg payload tags. Maps an arg name to a LabelState that
    # fires WHEN THAT ARG IS NON-EMPTY. Example: email.send.arg_inherent_tags = {
    #   "body": LabelState(a={CategoryTag("personal", Tier.REGULATED)}),
    #   "attachments": LabelState(b={ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
    # }
    # The chokepoint adds these per-arg tags in addition to the
    # tool-level inherent_tags. Lets a tool declare "the body field
    # carries personal data" without painting EVERY call with that tag.
    arg_inherent_tags: dict[str, LabelState] = field(default_factory=dict)
    # Pre-dispatch source labels. Tools that fetch a labeled object
    # indirectly (for example by memory key) can expose that object's
    # labels before the handler runs, so decide() sees the real source
    # label state instead of only the caller session's current taint.
    source_label_lookup: Callable[[dict[str, Any]], LabelState] | None = None
    # Pattern ② declassifiers are not valid for restricted/prohibited
    # source data. When true, the chokepoint refuses those sources before
    # calling the handler; callers must use Pattern ③/⑤ instead.
    forbid_restricted_source: bool = False

    def extract_target(self, args: dict[str, Any]) -> str:
        return str(args.get(self.target_arg, ""))

    def extract_amount(self, args: dict[str, Any]) -> int | None:
        if self.amount_arg is None:
            return None
        value = args.get(self.amount_arg)
        return int(value) if value is not None else None

    def extract_arg_inherent_tags(
        self,
        args: dict[str, Any],
    ) -> LabelState:
        """R7 — per-arg payload tags.

        For each (arg_name, tags) declaration: if the corresponding
        value in `args` is non-empty (truthy + not empty string/dict/list),
        include those tags. Result is the composition (most_restrictive_inherit)
        of all matching per-arg tags; empty if no declared arg is populated.
        """
        if not self.arg_inherent_tags:
            return LabelState()
        from capabledeputy.policy.labels import most_restrictive_inherit

        tags_to_compose: list[LabelState] = []
        for arg_name, tags in self.arg_inherent_tags.items():
            value = args.get(arg_name)
            if value is None:
                continue
            # Empty containers / strings don't trigger; truthy values do.
            if hasattr(value, "__len__"):
                if len(value) == 0:
                    continue
            elif not value:
                continue
            tags_to_compose.append(tags)
        return most_restrictive_inherit(*tags_to_compose) if tags_to_compose else LabelState()

    def extract_source_tags(self, args: dict[str, Any]) -> LabelState:
        """Return source labels known before the handler runs."""
        if self.source_label_lookup is None:
            return LabelState()
        return self.source_label_lookup(args)


class ToolValidationError(ValueError):
    """A ToolDefinition violates the registry-load contract
    (contracts/tool_definition.md). Fail-closed per Constitution VI:
    a malformed tool is refused, never registered with a usable
    capability."""


# EffectClasses that are mechanical execution (never a social commitment).
_MECHANICAL_EXECUTE: frozenset[EffectClass] = frozenset(
    {EffectClass.EXECUTE_HOST, EffectClass.EXECUTE_REMOTE, EffectClass.EXECUTE_DEPLOY},
)
# EffectClasses that neither write nor egress (no canonical destination needed).
_READ_ONLY_EFFECTS: frozenset[EffectClass] = frozenset(
    {EffectClass.OBSERVE, EffectClass.FETCH},
)


def validate_tool_definition(
    tool: ToolDefinition,
    *,
    known_risk_ids: frozenset[str] | None = None,
) -> None:
    """Enforce the contracts/tool_definition.md registry-load rules.
    Raises ToolValidationError on the first violation (fail-closed).

    Wired into `register()` in R3b once native tools declare the new
    fields; in R3a it is exercised by the invariant tests.

    `known_risk_ids`: if provided, every `risk_ids` entry MUST be a
    member (rule 5). Pass the loaded RiskRegister id set at daemon start.
    """
    effects = {op.effect_class for op in tool.operations}

    # Rule 1 — required fields present.
    if not tool.operations:
        raise ToolValidationError(f"{tool.name}: must declare >=1 operation (effect_class)")
    if not tool.risk_ids:
        raise ToolValidationError(f"{tool.name}: must cite >=1 risk_id (FR-015)")

    # Rule 2 — mechanical EXECUTE.* must not carry social commitment.
    if (effects & _MECHANICAL_EXECUTE) and tool.social_commitment:
        raise ToolValidationError(
            f"{tool.name}: mechanical EXECUTE.* effects must not declare social_commitment "
            "(social commitment lives on COMMUNICATE / TRANSACT)",
        )

    # Rule 3 — accepts_handles ⇒ handle_arg_names non-empty and in schema.
    if tool.accepts_handles:
        if not tool.handle_arg_names:
            raise ToolValidationError(
                f"{tool.name}: accepts_handles=True requires non-empty handle_arg_names",
            )
        props = (tool.parameters_schema or {}).get("properties", {})
        missing = [a for a in tool.handle_arg_names if a not in props]
        if missing:
            raise ToolValidationError(
                f"{tool.name}: handle_arg_names {missing} absent from parameters_schema",
            )

    # Rule 4 — no canonical destination ⇒ only read effects allowed (FR-048).
    if not tool.surfaces_destination_id and not (effects <= _READ_ONLY_EFFECTS):
        raise ToolValidationError(
            f"{tool.name}: write/egress effect {effects - _READ_ONLY_EFFECTS} requires "
            "surfaces_destination_id=True (FR-048)",
        )

    # Rule 5 — risk_ids must be known register entries.
    if known_risk_ids is not None:
        unknown = set(tool.risk_ids) - known_risk_ids
        if unknown:
            raise ToolValidationError(f"{tool.name}: cites unknown risk_ids {sorted(unknown)}")

    # Rule 6 (wrapper union) needs sub-tool composition info not present on a
    # bare ToolDefinition; enforced at the wrapper construction site (R3b).


# Best-effort capability-kind -> EffectClass for adapter-created tools
# (upstream MCP, skills). Unknown/custom kinds default to FETCH (read),
# matching the upstream adapter's conservative READ_FS default for
# unclassifiable tools.
_KIND_TO_EFFECT: dict[str, EffectClass] = {
    "READ_FS": EffectClass.FETCH,
    "GMAIL_READ": EffectClass.FETCH,
    "IMAP_READ": EffectClass.FETCH,
    "DRIVE_READ": EffectClass.FETCH,
    "CALENDAR_READ": EffectClass.FETCH,
    "WEB_FETCH": EffectClass.FETCH,
    "WRITE_FS": EffectClass.MUTATE_LOCAL,
    "CREATE_FS": EffectClass.MUTATE_LOCAL,
    "MODIFY_FS": EffectClass.MUTATE_LOCAL,
    "CALENDAR_WRITE": EffectClass.MUTATE_LOCAL,
    "CREATE_CAL": EffectClass.MUTATE_LOCAL,
    "MODIFY_CAL": EffectClass.MUTATE_LOCAL,
    "DELETE_FS": EffectClass.DESTROY,
    "DELETE_CAL": EffectClass.DESTROY,
    "SEND_EMAIL": EffectClass.COMMUNICATE,
    "QUEUE_PURCHASE": EffectClass.TRANSACT,
    "EXECUTE_SANDBOX": EffectClass.EXECUTE_SANDBOX,
    "EXECUTE_DEVBOX": EffectClass.EXECUTE_SANDBOX,
}
_EFFECT_DEFAULT_RISK: dict[EffectClass, tuple[str, ...]] = {
    EffectClass.OBSERVE: ("RISK-EXCESSIVE-AGENCY",),
    EffectClass.FETCH: ("RISK-INDIRECT-INJECTION",),
    EffectClass.MUTATE_LOCAL: ("RISK-DESTRUCTIVE-WRITE",),
    EffectClass.DESTROY: ("RISK-DESTRUCTIVE-WRITE",),
    EffectClass.COMMUNICATE: ("RISK-DATA-EXFIL-AGENT-TOOLS",),
    EffectClass.TRANSACT: ("RISK-IRREVERSIBLE-SEND",),
    EffectClass.EXECUTE_SANDBOX: ("RISK-UNSAFE-CODE-EXEC",),
}


def default_operation_for_kind(
    kind: CapabilityKind | str,
) -> tuple[Operation, tuple[str, ...], bool]:
    """Best-effort (Operation, risk_ids, surfaces_destination_id) for an
    adapter-created tool from its capability kind. Used by the upstream /
    skills adapters so their tools satisfy `validate_tool_definition`."""
    key = kind.value if isinstance(kind, CapabilityKind) else str(kind)
    effect = _KIND_TO_EFFECT.get(key, EffectClass.FETCH)
    risks = _EFFECT_DEFAULT_RISK[effect]
    surfaces = effect not in (EffectClass.OBSERVE, EffectClass.FETCH)
    return Operation(effect, subtype=key), risks, surfaces


class DuplicateToolError(ValueError):
    pass


class ToolNotFoundError(KeyError):
    def __init__(self, name: str) -> None:
        super().__init__(f"tool not found: {name}")
        self.name = name


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise DuplicateToolError(f"tool already registered: {tool.name}")
        # 003 R3d — fail-closed registry validation (Constitution VI). A tool
        # missing required fields (operations, risk_ids, ...) is refused, never
        # registered with a usable capability. Rule 5 (risk_ids subset of the
        # loaded register) is a separate daemon-start audit, not enforced here.
        validate_tool_definition(tool)
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as e:
            raise ToolNotFoundError(name) from e

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
