"""Turn-level agent loop (DESIGN.md §5.1).

run_turn() implements the simplest of the three execution modes:
turn-level inheritance. The agent processes one user message at a
time, looping LLM calls and tool dispatches until the LLM produces
a final answer (no tool calls). Every tool result's labels accumulate
into the session via the LabeledToolClient, so subsequent policy
checks within the same turn see the up-to-date label set — that is
the inheritance.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from capabledeputy.agent.context import build_llm_context
from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.llm.client import LLMClient
from capabledeputy.llm.types import (
    FinishReason,
    LLMResponse,
    Message,
    Role,
    ToolDescription,
)
from capabledeputy.mode.dispatcher import (
    ExecutionMode,
    ModeSelectionError,
    filter_tools_for_mode,
    select_mode,
    visible_tools,
)
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.rules import Decision
from capabledeputy.session.foreground_defaults import FOREGROUND_CHAT_OWNERS
from capabledeputy.session.graph import SessionGraph, SessionStateError
from capabledeputy.session.model import Session, Turn, make_generated_image_artifact
from capabledeputy.tools.aliasing import build_alias_map, build_reverse_map
from capabledeputy.tools.client import LabeledToolClient, ToolCallOutcome
from capabledeputy.tools.registry import ToolDefinition, ToolNotFoundError, ToolRegistry

if TYPE_CHECKING:
    from capabledeputy.agent.tool_families import ToolFamiliesConfig
    from capabledeputy.llm.pool import ModelPool

_TOOL_CALL_INSTRUCTIONS_NATIVE = """CRITICAL — how you call tools:

- The ONLY way to invoke a tool is via the API's native `tool_use` mechanism.
  Tools available to you on this turn appear in the system-provided
  tool list. If a tool is not in that list, it does not exist for you,
  full stop.
- NEVER write a tool call as text or code (e.g. backticked ```inbox.search(...)```).
  That is not an invocation — it is fabrication. The runtime cannot see
  it, no policy is evaluated, no real action happens."""

_TOOL_CALL_INSTRUCTIONS_JSON = """CRITICAL — how you call tools:

- The ONLY way to invoke a tool is to emit a single JSON object with this shape:
  `{"tool_calls": [{"id": "<unique_id>", "name": "<tool_name>", "args": {...}}]}`
  Tools available to you on this turn appear in the system-provided tool list.
  If a tool is not in that list, it does not exist for you, full stop.
- If no tool call is needed, respond with plain natural-language text only.
- NEVER write a tool call as prose or a code block — that is fabrication."""

_SHARED_POLICY_PROMPT = """
You operate inside a runtime that gates every tool call by capability and
information-flow policy. The runtime enforces these rules — you cannot
bypass them, but you should understand them so you can give the user
useful, accurate answers.
"""

DEFAULT_SYSTEM_PROMPT = f"""You are CapableDeputy, a structurally secure personal AI assistant.
{_SHARED_POLICY_PROMPT}
{_TOOL_CALL_INSTRUCTIONS_NATIVE}
- NEVER invent tool names. If the user asks for "forward email" and your
  tool list has only `email.send`, `inbox.read`, `inbox.list`, say so —
  do not invent `email.forward`.
- If your tool list is EMPTY for this turn, you have no tools at all.
  Tell the user explicitly: "I have no capabilities granted in this
  session. Run `/grant <KIND> <pattern>` to give me one (for example,
  `/grant SEND_EMAIL recipient@example.com --one-shot`)." Do not pretend.
- After calling a tool, the runtime returns a real result. Report that
  result accurately. Do not fabricate decisions or outputs.

How the policy works (high-level):

- Every tool you call reads or writes labelled data. Reading an inbox
  message adds `untrusted.external` to the session. Reading the calendar
  adds `confidential.personal`. Financial notes carry `confidential.financial`.
- Outbound channels (email send, purchase) carry an egress label.
- Conflict rules block flows: e.g. `untrusted.external` + `egress.email` → DENY,
  `confidential.financial` + `egress.purchase` → REQUIRE_APPROVAL.
- Labels are sticky: once the session has read untrusted content, every
  later outbound attempt — including ones you compose "from scratch" —
  is still tagged with the prior untrusted read and is blocked.

Before any outbound or destructive tool call — `email.send`,
`purchase.queue`, `memory.update`, `memory.delete`,
`calendar.update_event`, `calendar.delete_event` — and especially when
the session has accumulated multiple labels, **first call `policy.preview`
with the same kind and target you intend to use**. If preview returns
`decision="deny"`, do not attempt the real call; tell the user what
would have been blocked and why, and suggest the appropriate escape
hatch below.

When a tool call comes back DENY:

- This is a hard block, not "ask again nicely." The same call from the
  same session will fail the same way; do not retry.
- If the user genuinely wants the action, tell them about the escape
  hatches the *human* (not you) controls:
    * `/spawn <intent>` — they can create a fresh clean session that
      doesn't carry this session's labels, then grant a one-shot
      capability for the specific action. This is the right path when
      the current session is "tainted" by an untrusted read.
    * `/extract <key> from <message-id>` (when available) — runs the
      quarantined extractor against a single untrusted message and
      produces a labelled-clean fact that a clean session can use.
- Match the recovery to the rule in the denial:
    * `capability-expired` — the capability's deadline passed. The
      user can `/grant` a fresh capability, optionally with a longer
      `--ttl SECONDS`.
    * `rate-limit-exceeded` — too many uses in the window. The user
      can wait for the window to slide, or `/grant` a capability with
      a higher `--rate MAX/WINDOW`.
    * `capability-revoked-by-prior-use` — a prior tool use revoked it;
      a fresh `/spawn`-ed session has not used the revoking tool.
- Never claim "no approval mechanism exists." There IS a human-in-the-loop
  path; you just can't invoke it yourself. Tell the user how *they* can.

When a tool call comes back REQUIRE_APPROVAL:

- The action is held pending. The user reviews the verbatim payload and
  decides. Tell the user clearly what you want to do and why, so the
  approval prompt is meaningful.

When you have completed the task, respond with a final answer and no
tool calls. Be concise and honest about what you did and didn't do.
"""

MODEL_ROLE_ALIASES: dict[str, str | None] = {
    "auto": None,
    "default": None,
    "fast": "planner.fast",
    "snappy": "planner.fast",
    "tools": "planner.tools",
    "tool": "planner.tools",
    "quality": "planner.quality",
    "better": "planner.quality",
    "coder": "planner.coder",
    "code": "planner.coder",
    "scripting": "planner.coder",
    "script": "planner.coder",
}


def _extract_model_role_directive(user_message: str) -> tuple[str, str | None]:
    """Return message text and optional per-turn planner role override.

    Operator-facing clients can prefix a turn with `/fast`, `/quality`,
    `/coder`, or `/model <mode>`. The directive is stripped before the message
    is stored in session history so chat remains readable and future context is
    not polluted by UI control syntax.
    """
    stripped = user_message.lstrip()
    leading_ws = user_message[: len(user_message) - len(stripped)]
    lowered = stripped.lower()
    for alias, role in MODEL_ROLE_ALIASES.items():
        marker = f"/{alias}"
        if lowered == marker:
            return "", role
        if lowered.startswith(marker + " "):
            return leading_ws + stripped[len(marker) :].lstrip(), role
    if lowered.startswith("/model "):
        rest = stripped[len("/model ") :].lstrip()
        if not rest:
            return user_message, None
        parts = rest.split(maxsplit=1)
        role = MODEL_ROLE_ALIASES.get(parts[0].lower())
        if parts[0].lower() not in MODEL_ROLE_ALIASES:
            return user_message, None
        return (parts[1] if len(parts) > 1 else ""), role
    return user_message, None


class AgentLoopExceededError(RuntimeError):
    pass


# Issue #2 — loop-thrash detection. When the agent proposes the *same*
# tool with the *same* arguments this many times within a single turn,
# we stop early with an AGENT_LOOP_THRASHING audit event rather than
# burning the rest of the iteration budget on a confirmed non-converging
# loop. 3 is deliberately conservative: a couple of legitimate retries
# (e.g. a transient tool error) stay under it.
_THRASH_REPEAT_THRESHOLD = 3
# How many of the most-recent (tool, args) calls to retain in the
# abnormal-termination audit payload so the pathological turn is
# replayable. Bounded so the audit record stays scannable.
_LAST_CALLS_RETAINED = 12

# Operator-facing recovery hint appended to the AgentLoopExceededError
# message so the RPC error in chat tells the user what to do, not just
# that the cap fired.
_LOOP_RECOVERY_HINT = (
    " — increase max_iterations (/spawn --max-iters N or "
    "CAPDEP_AGENT_MAX_ITERATIONS), refine your intent, or split the "
    "request into smaller sub-turns"
)

_PARSE_RETRY_NOTICE = (
    "NOTICE: your previous reply was not a valid tool-call JSON envelope. "
    'Respond with ONLY {"tool_calls": [...]} to call tools, or plain text if done.'
)


def planner_uses_json_tools(llm: LLMClient) -> bool:
    name = type(llm).__name__
    return name in {"MLXLLMClient", "ClaudeCliClient"}


def tool_call_instructions_for_llm(llm: LLMClient) -> str:
    if planner_uses_json_tools(llm):
        return _TOOL_CALL_INSTRUCTIONS_JSON
    return _TOOL_CALL_INSTRUCTIONS_NATIVE


def _looks_like_failed_tool_parse(content: str) -> bool:
    lowered = content.lower()
    if "tool_calls" in content or "```json" in lowered:
        return True
    return "{" in content and ("tool_call" in lowered or '"name"' in content)


_SLASH_RECOVERY_TOKENS = ("/grant", "/spawn", "/override", "/extract")


def _foreground_gui_session(session: Session) -> bool:
    return (session.owner or "").strip() in FOREGROUND_CHAT_OWNERS


def _has_capability(session: Session, kind: CapabilityKind, pattern: str) -> bool:
    return any(cap.kind is kind and cap.pattern == pattern for cap in session.capability_set)


def _repair_foreground_recovery_leak(
    content: str,
    *,
    session: Session,
    outcomes: list[ToolCallOutcome] | tuple[ToolCallOutcome, ...],
) -> str:
    """Keep terminal recovery commands from leaking into GUI chat.

    CapDepMac renders structured recovery/approval metadata from tool outcomes.
    A model-authored "The runtime suggests: `/grant ...`" paragraph is plain
    text, so the GUI cannot turn it into the approval card the user expects.
    """
    if not content or not _foreground_gui_session(session):
        return content
    lowered = content.lower()
    if not any(token in lowered for token in _SLASH_RECOVERY_TOKENS):
        return content
    if "/grant web_fetch" in lowered and _has_capability(session, CapabilityKind.WEB_FETCH, "*"):
        return (
            "Web search is already granted for this CapDepMac session. I was "
            "not able to complete the search through the current provider/tool "
            "path, so this is a provider or tool-call issue rather than a GUI "
            "permission issue."
        )
    blocked = [
        outcome
        for outcome in outcomes
        if outcome.decision in {Decision.DENY, Decision.REQUIRE_APPROVAL}
    ]
    if blocked:
        names = ", ".join(sorted({o.tool_name or "tool" for o in blocked}))
        return (
            f"The runtime blocked or queued {names}. CapDepMac should show the "
            "structured approval or recovery control for that action; I will "
            "not ask you to type slash commands into chat."
        )
    return (
        "I do not have a valid runtime recovery action for this turn. "
        "CapDepMac permissions and approvals should be handled through the GUI; "
        "if the request still fails after retrying, check the daemon/provider "
        "readiness rather than typing slash commands into chat."
    )


def _call_signature(tool_name: str, args: dict) -> str:
    """Stable (tool_name, args) signature for thrash detection.

    Serializes args deterministically (sorted keys, `default=str` so
    non-JSON values still hash) and digests them so the signature is a
    short, comparable string regardless of arg size.
    """
    try:
        payload = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr(args)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{tool_name}:{digest}"


class ContextOverflowError(RuntimeError):
    """Issue #36 — raised when the assembled LLM context would exceed
    the hard limit (default 90% of model window). Carries the
    estimated token count + the model's window so callers can render
    a clear "your context is too large; spawn a fresh session"
    recovery message."""

    def __init__(self, estimated_tokens: int, window: int) -> None:
        super().__init__(
            f"context size ~{estimated_tokens:,} tokens exceeds "
            f"{int(window * 0.9):,} (90% of {window:,}-token window). "
            f"Spawn a fresh session for a more targeted query.",
        )
        self.estimated_tokens = estimated_tokens
        self.window = window


# Issue #36 — model context windows. Heuristic table; values are the
# documented max prompt tokens for common models. If a model isn't
# listed, falls back to DEFAULT_CONTEXT_WINDOW. Operators can
# override via the LLM client config; this is just the guardrail's
# default knowledge.
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-haiku-4-5-20251001": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-7": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8192,
}
DEFAULT_CONTEXT_WINDOW = 100_000

_SOFT_WARNING_THRESHOLD = 0.80
_HARD_LIMIT_THRESHOLD = 0.90


def _estimate_message_tokens(
    messages: list[Message],
    tool_descriptions: list[ToolDescription] | tuple[ToolDescription, ...] | None = None,
) -> int:
    """Cheap chars-per-token heuristic. Anthropic + OpenAI tokenize
    English prose at ~4 chars/token, but JSON-heavy content (tool
    schemas, structured tool results) tokenizes denser — closer to
    3 chars/token because of `{`, `}`, `"`, and many short keys. We
    use 3 to stay safe and pad an additional ~10% for per-message
    framing overhead (role markers, tool_use/tool_result block
    metadata in the Anthropic envelope).

    Before this fix, the estimator counted only message content +
    inline tool_call args. It missed two large contributors that
    actually ship to the model on every iteration:

      1. The `tools` array — every ToolDescription's name,
         description, and JSON schema is serialized to the wire.
         With 50+ upstream tools this can be 30k+ tokens by itself.
      2. Tool result content shipped via `Role.TOOL` messages —
         these *were* counted (they live in `msg.content`) but the
         char/token ratio undercounted JSON-heavy outputs.

    Empirical case: a real email-summarization turn estimated as
    64k tokens actually weighed 202k tokens at the provider
    boundary — a 3.2x undercount. Adding tool schemas + tightening
    the ratio closes most of that gap; the rest is provider
    tokenizer quirks (BPE merges) we don't model.
    """
    total_chars = 0
    for msg in messages:
        if msg.content:
            total_chars += len(msg.content)
        # Per-message framing overhead — role marker, tool_call_id,
        # tool_use envelope. ~20 chars is conservative.
        total_chars += 20
        # tool_calls payloads count too
        for tc in msg.tool_calls or ():
            total_chars += len(tc.name) + len(str(tc.args or {}))
    # Tool schemas ship with every request — these are huge and
    # were previously uncounted.
    if tool_descriptions:
        for td in tool_descriptions:
            total_chars += len(td.name) + len(td.description)
            # JSON schema serialized roughly as str(dict) — close
            # enough to the wire size for a heuristic.
            total_chars += len(str(td.parameters_schema))
    return total_chars // 3


def _context_window_for(model: str | None) -> int:
    """Look up the model's prompt-token window."""
    if model is None:
        return DEFAULT_CONTEXT_WINDOW
    return _MODEL_CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)


@dataclass(frozen=True)
class AgentTurnResult:
    content: str
    iterations: int
    finish_reason: FinishReason
    tool_outcomes: tuple[ToolCallOutcome, ...] = field(default_factory=tuple)


def _turn_to_message(turn: Turn) -> Message | None:
    if turn.role == "user":
        return Message(role=Role.USER, content=turn.content)
    if turn.role == "agent":
        return Message(role=Role.ASSISTANT, content=turn.content)
    return None


def build_tool_descriptions(
    registry: ToolRegistry,
    mode: ExecutionMode = ExecutionMode.TURN_LEVEL,
    session: Session | None = None,
    *,
    selected_tools: list[ToolDefinition] | tuple[ToolDefinition, ...] | None = None,
) -> list[ToolDescription]:
    """Build the tool list shown to the LLM.

    If the session has `tool_aliasing` enabled, every visible tool's
    canonical name is replaced with a session-specific token. The
    reverse map is recomputed at dispatch time (also from the session id),
    so we don't have to thread state — the alias function is pure.

    When `selected_tools` is provided, only that subset is described
    (post capability/mode gating and tool-surface curation).
    """
    if selected_tools is not None:
        tools = list(selected_tools)
    elif session is not None:
        tools = visible_tools(registry, session, mode)
    else:
        tools = filter_tools_for_mode(registry.list(), mode)

    aliasing = session is not None and session.tool_aliasing
    alias_map: dict[str, str] = {}
    if aliasing and session is not None:
        alias_map = build_alias_map(session.id, [t.name for t in tools])

    return [
        ToolDescription(
            name=alias_map.get(t.name, t.name),
            description=t.description,
            parameters_schema=t.parameters_schema,
        )
        for t in tools
    ]


def _format_outcome(outcome: ToolCallOutcome) -> str:
    if outcome.error is not None:
        return f"tool error: {outcome.error}"
    if outcome.decision == Decision.DENY:
        rule_part = outcome.rule or "policy"
        reason_part = f": {outcome.reason}" if outcome.reason else ""
        recovery_hint = (
            "\n\nRecovery: This is a structural block, not a permission request. "
            "Consider /spawn to create a fresh session, or /extract to isolate "
            "the data separately."
        )
        return f"POLICY DENIED by rule '{rule_part}'{reason_part}{recovery_hint}"
    if outcome.decision == Decision.REQUIRE_APPROVAL:
        rule_part = outcome.rule or "policy"
        reason_part = f": {outcome.reason}" if outcome.reason else ""
        recovery_hint = (
            "\n\nApproval Status: The operation is queued for human review. "
            "The operator will approve or deny it separately."
        )
        return f"APPROVAL REQUIRED by rule '{rule_part}'{reason_part}{recovery_hint}"
    output = outcome.output if outcome.output is not None else {}
    return str(output)


def _generated_image_artifacts_from_outcomes(
    outcomes: list[ToolCallOutcome] | tuple[ToolCallOutcome, ...],
    *,
    origin_turn_id: int,
) -> tuple[dict[str, Any], ...]:
    from capabledeputy.agent.chat_turn import image_paths_from_tool_outcome, normalize_image_path
    from capabledeputy.mcp_server.media_results import iter_image_sources_in_value

    artifacts: list[dict[str, Any]] = []
    for outcome in outcomes:
        allowed_paths = image_paths_from_tool_outcome(outcome)
        if not allowed_paths:
            continue
        alt_by_path = {
            normalize_image_path(source): alt
            for source, alt in iter_image_sources_in_value(outcome.output or {})
            if normalize_image_path(source) in allowed_paths
        }
        prompt = None
        if outcome.tool_args:
            raw_prompt = outcome.tool_args.get("prompt")
            if isinstance(raw_prompt, str) and raw_prompt.strip():
                prompt = raw_prompt.strip()
        for path in sorted(allowed_paths):
            artifacts.append(
                make_generated_image_artifact(
                    path=path,
                    alt=alt_by_path.get(path),
                    prompt=prompt,
                    origin_turn_id=origin_turn_id,
                    origin_tool_name=outcome.tool_name,
                ),
            )
    return tuple(artifacts)


def _no_tools_notice_message(session: Session) -> Message:
    if _foreground_gui_session(session):
        return Message(
            role=Role.SYSTEM,
            content=(
                "NOTICE: this CapDepMac/foreground GUI session has no tools "
                "available for this turn. Do not write tool-call code blocks "
                "as text, and do not tell the user to type slash commands. "
                "Tell them that the GUI/daemon needs to surface setup, grant, "
                "or provider-readiness controls for this request."
            ),
        )
    return Message(
        role=Role.SYSTEM,
        content=(
            "NOTICE: this session has no tool capabilities. "
            "Your tool list is empty for this turn. You CANNOT "
            "call any tools. Do not write tool-call code blocks "
            "as text — that does nothing. Respond to the user "
            "by telling them: 'I have no tools available in this "
            "session. Run `/grant <KIND> <pattern>` to grant me "
            "one (e.g. `/grant SEND_EMAIL recipient@example.com "
            "--one-shot`).' Then stop."
        ),
    )


async def run_turn(
    *,
    session_id: UUID,
    user_message: str,
    llm: LLMClient,
    tool_client: LabeledToolClient,
    registry: ToolRegistry,
    graph: SessionGraph,
    audit: AuditWriter,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_iterations: int = 50,
    force_mode: ExecutionMode | None = None,
    cancel_check: Callable[[], bool] | None = None,
    model_pool: ModelPool | None = None,
    tool_families: ToolFamiliesConfig | None = None,
) -> AgentTurnResult:
    """Issue #21 — `run_turn` is now a thin wrapper around the
    streaming generator `run_turn_streaming`. Existing callers
    (daemon RPC, tests, programmatic_loop, etc.) keep working
    unchanged: this consumes all events and returns the final
    `AgentTurnResult`. New streaming consumers (chat REPL Rich
    Live region, rich Textual surface) call `run_turn_streaming`
    directly via `for await` and observe each event.

    Issue #23 — `cancel_check` is an optional zero-arg callable
    polled between iterations. When it returns True, the loop
    yields TurnInterrupted(reason="user_cancelled") and returns the
    partial result. The daemon's session.cancel RPC flips a flag
    that this callable observes; from `run_turn`'s perspective the
    cancellation surfaces as a regular early return with an
    interrupted finish_reason.
    """
    from capabledeputy.agent.events import TurnCompleted, TurnInterrupted

    final_result: AgentTurnResult | None = None
    interrupt_reason: str | None = None
    interrupt_event: TurnInterrupted | None = None
    async for evt in run_turn_streaming(
        session_id=session_id,
        user_message=user_message,
        llm=llm,
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
        system_prompt=system_prompt,
        max_iterations=max_iterations,
        force_mode=force_mode,
        cancel_check=cancel_check,
        model_pool=model_pool,
        tool_families=tool_families,
    ):
        if isinstance(evt, TurnCompleted):
            final_result = evt.result
        elif isinstance(evt, TurnInterrupted):
            interrupt_reason = evt.reason
            interrupt_event = evt
    if interrupt_reason == "max_iterations":
        raise AgentLoopExceededError(
            f"agent loop exceeded {max_iterations} iterations{_LOOP_RECOVERY_HINT}",
        )
    if interrupt_reason == "mode_refused_restricted":
        # Issue #52 — fail-closed: restricted tier with no Pattern ③/⑤
        # mode available. Surface the typed error to RPC callers.
        raise ModeSelectionError(
            interrupt_event.partial_content
            if interrupt_event and interrupt_event.partial_content
            else "restricted-tier session has no Pattern (3)/(5) mode available",
        )
    if interrupt_reason == "agent_loop_thrashing":
        # Issue #2 — the loop was stopped early because the agent kept
        # proposing the same tool call. Surface a clearer cause than a
        # bare cap-fire so the operator knows it's a non-converging loop,
        # not a budget that was simply too small.
        raise AgentLoopExceededError(
            "agent loop stopped: the assistant repeated the same tool "
            f"call {_THRASH_REPEAT_THRESHOLD} times without progress (thrashing)"
            f"{_LOOP_RECOVERY_HINT}",
        )
    if interrupt_reason == "user_cancelled" and interrupt_event is not None:
        # Issue #23 — synthesize an AgentTurnResult so the daemon RPC
        # response shape stays consistent. Caller (chat REPL) sees a
        # normal turn return with finish_reason=interrupted and any
        # partial content/outcomes captured before cancellation.
        return AgentTurnResult(
            content=interrupt_event.partial_content or "[turn cancelled by user]",
            iterations=interrupt_event.iteration,
            finish_reason=FinishReason.LENGTH,
            tool_outcomes=interrupt_event.partial_outcomes,
        )
    if interrupt_event is not None and (
        interrupt_reason == "context_overflow"
        or (interrupt_reason is not None and interrupt_reason.startswith("llm_error:"))
    ):
        # Issue #36 / LLM error handling — the streaming generator has
        # already emitted a terminal interrupt event and audited the
        # failure. Preserve the daemon RPC contract by returning a
        # partial turn result rather than collapsing into the generic
        # "no terminal event" wrapper error.
        return AgentTurnResult(
            content=(interrupt_event.partial_content or f"[turn interrupted: {interrupt_reason}]"),
            iterations=interrupt_event.iteration,
            finish_reason=FinishReason.LENGTH,
            tool_outcomes=interrupt_event.partial_outcomes,
        )
    if final_result is None:
        # Defensive: streaming generator must yield exactly one terminal
        # event before returning.
        raise RuntimeError(
            "run_turn_streaming exited without a terminal event "
            f"(last interrupt reason={interrupt_reason!r})",
        )
    return final_result


async def run_turn_streaming(
    *,
    session_id: UUID,
    user_message: str,
    llm: LLMClient,
    tool_client: LabeledToolClient,
    registry: ToolRegistry,
    graph: SessionGraph,
    audit: AuditWriter,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_iterations: int = 50,
    force_mode: ExecutionMode | None = None,
    cancel_check: Callable[[], bool] | None = None,
    model_pool: ModelPool | None = None,
    tool_families: ToolFamiliesConfig | None = None,
):
    """Streaming variant of `run_turn` (Issue #21).

    Yields `TurnEvent`s as the turn progresses:
      - `IterationStarted` at the top of each loop iter
      - `LLMRequestSent` before each LLM call
      - `LLMResponseReceived` after each LLM call
      - `ToolDispatched` before each tool dispatch
      - `ToolReturned` after each tool returns
      - `TurnCompleted` (terminal) when the LLM produces a final answer
      - `TurnInterrupted` (terminal) when max_iterations exceeded
        (with reason='max_iterations'). Future #23/#31/#32 work
        will surface 'ctrl-c', 'surface-disconnect', and
        'heartbeat-timeout' as additional reasons.

    Token-level streaming (`LLMTokenReceived`) is emitted when the
    active LLM client exposes `respond_streaming` (MLX on macOS).
    """
    from capabledeputy.agent.chat_turn import (
        CHAT_MAX_TOKENS,
        IMAGE_GENERATION_RETRY_NOTICE,
        IMAGE_PATH_HALLUCINATION_RETRY_NOTICE,
        allowed_image_generate_paths,
        collect_prior_work_image_paths,
        has_probable_image_generation_intent,
        image_generate_tool_names_in,
        is_conversational_turn,
        looks_like_hallucinated_image_markdown,
        looks_like_image_generation_refusal,
        repair_hallucinated_image_markdown,
        should_force_image_generate_tool,
    )
    from capabledeputy.agent.events import (
        IterationStarted,
        LLMRequestSent,
        LLMResponseReceived,
        LLMTokenReceived,
        ModelSelected,
        ToolDispatched,
        ToolReturned,
        TurnCompleted,
        TurnInterrupted,
    )
    from capabledeputy.agent.tool_families import load_tool_families
    from capabledeputy.agent.tool_selection import (
        ToolSelectionResult,
        select_tools_for_turn_async,
        widen_tool_surface,
    )
    from capabledeputy.llm.mlx_client import MLXLLMClient, finalize_mlx_text
    from capabledeputy.llm.models_config import ToolSelectionConfig
    from capabledeputy.llm.routing import ModelRoutingContext, ModelRoutingResult

    user_message, model_role_override = _extract_model_role_directive(user_message)
    if not user_message.strip():
        user_message = "Use the selected model mode for this turn."

    session = graph.get(session_id)
    if session.is_terminal:
        raise SessionStateError(
            f"cannot send to terminal session {session_id} (status={session.status})",
        )

    # Mode selection happens BEFORE history is mutated so the
    # programmatic loop can take over cleanly when selected.
    #
    # Issue #52 — a restricted-tier session must run under Pattern ③/⑤,
    # so select_mode needs to know whether a SandboxActuator is wired
    # (the Pattern ⑤ precondition). We read it off the policy context.
    pc = tool_client.policy_context
    has_sandbox_actuator = bool(pc is not None and pc.sandbox_actuator_wired)
    # #305 — the operator-selected security posture (None ⇒ legacy behavior).
    # Drives select_mode's per-tier flow-pattern defaults and the
    # projection-only knob in the tool-surface filters below.
    active_posture = pc.active_posture if pc is not None else None
    try:
        mode, mode_reason = select_mode(
            session.label_state,
            registry,
            prefer_programmatic=session.prefer_programmatic,
            force_mode=force_mode,
            has_sandbox_actuator=has_sandbox_actuator,
            session=session,
            posture=active_posture,
        )
    except ModeSelectionError as e:
        # FR-047 fail-closed (#52): the session reached restricted tier
        # but no Pattern ③/⑤ mode is available (e.g. it escalated
        # mid-session past the spawn-time gate). Refuse the turn rather
        # than running restricted data through a planner-exposing mode.
        await audit.write(
            Event(
                event_type=EventType.MODE_SELECTED,
                session_id=session_id,
                turn_id=len(session.history),
                step_id=0,
                payload={
                    "refused": True,
                    "tier": "restricted",
                    "reason": str(e),
                },
            ),
        )
        yield TurnInterrupted(
            iteration=0,
            reason="mode_refused_restricted",
            partial_content=str(e),
            partial_outcomes=(),
        )
        return

    if mode == ExecutionMode.PROGRAMMATIC:
        # Local import to avoid a circular: programmatic_loop imports from
        # this module for AgentTurnResult.
        from capabledeputy.agent.programmatic_loop import run_programmatic_turn

        await audit.write(
            Event(
                event_type=EventType.MODE_SELECTED,
                session_id=session_id,
                turn_id=len(session.history),
                payload={"mode": mode.value, "reason": mode_reason},
            ),
        )
        prog_llm = llm
        if model_pool is not None:
            visible_for_route = visible_tools(registry, session, mode, active_posture)
            prog_llm, routing = model_pool.resolve_planner(
                ModelRoutingContext(
                    purpose_handle=session.purpose_handle,
                    execution_mode=mode,
                    n_visible_tools=len(visible_for_route),
                    n_selected_tools=len(visible_for_route),
                    user_message_chars=len(user_message),
                    model_role_override=model_role_override,
                ),
            )
            await audit.write(
                Event(
                    event_type=EventType.LLM_MODEL_SELECTED,
                    session_id=session_id,
                    turn_id=len(session.history),
                    payload={
                        "role": routing.role,
                        "reason": routing.reason,
                        "mlx_model": routing.mlx_model,
                        "n_visible_tools": len(visible_for_route),
                    },
                ),
            )
            yield ModelSelected(
                iteration=0,
                role=routing.role,
                reason=routing.reason,
                model=routing.mlx_model,
            )
        prog_result = await run_programmatic_turn(
            session_id=session_id,
            user_message=user_message,
            llm=prog_llm,
            tool_client=tool_client,
            registry=registry,
            graph=graph,
            audit=audit,
        )
        yield TurnCompleted(iteration=0, result=prog_result)
        return

    user_turn = Turn(
        turn_id=len(session.history),
        role="user",
        content=user_message,
    )
    session = await graph.add_turn(session_id, user_turn)

    await audit.write(
        Event(
            event_type=EventType.MODE_SELECTED,
            session_id=session_id,
            turn_id=len(session.history),
            payload={"mode": mode.value, "reason": mode_reason},
        ),
    )

    visible_all = visible_tools(registry, session, mode, active_posture)
    conversational = is_conversational_turn(user_message)

    families_cfg = tool_families or load_tool_families()
    selection_cfg = (
        model_pool.config.tool_selection if model_pool is not None else ToolSelectionConfig()
    )
    router_llm = (
        model_pool.default_planner_client()
        if model_pool is not None and selection_cfg.mode == "retrieve+ai"
        else None
    )
    if conversational:
        tool_surface = ToolSelectionResult(
            selected=(),
            candidates=(),
            n_visible=len(visible_all),
            method="conversational",
        )
    else:
        tool_surface = await select_tools_for_turn_async(
            registry,
            session,
            mode,
            visible_all,
            user_message=user_message,
            router_llm=router_llm,
            families=families_cfg,
            selection_config=selection_cfg,
        )

    effective_llm = llm
    if model_pool is not None:
        if should_force_image_generate_tool(user_message) and model_role_override is None:
            routing = ModelRoutingResult(
                role="planner.tools",
                reason="image_generation_intent",
                mlx_model=model_pool.config.role_spec("planner.tools").mlx,
            )
            effective_llm = model_pool.client("planner.tools")
        else:
            effective_llm, routing = model_pool.resolve_planner(
                ModelRoutingContext(
                    purpose_handle=session.purpose_handle,
                    execution_mode=mode,
                    n_visible_tools=len(visible_all),
                    n_selected_tools=len(tool_surface.selected),
                    user_message_chars=len(user_message),
                    model_role_override=model_role_override,
                ),
            )
        await audit.write(
            Event(
                event_type=EventType.LLM_MODEL_SELECTED,
                session_id=session_id,
                turn_id=len(session.history),
                payload={
                    "role": routing.role,
                    "reason": routing.reason,
                    "mlx_model": routing.mlx_model,
                    "n_visible_tools": len(visible_all),
                    "n_selected_tools": len(tool_surface.selected),
                    "conversational": conversational,
                },
            ),
        )
        yield ModelSelected(
            iteration=0,
            role=routing.role,
            reason=routing.reason,
            model=routing.mlx_model,
        )
    await audit.write(
        Event(
            event_type=EventType.TOOL_SURFACE_SELECTED,
            session_id=session_id,
            turn_id=len(session.history),
            payload={
                "method": tool_surface.method,
                "n_visible": tool_surface.n_visible,
                "n_selected": len(tool_surface.selected),
                "selected": [t.name for t in tool_surface.selected],
                "mandatory_added": list(tool_surface.mandatory_added),
            },
        ),
    )

    # Build deterministic LLM context with session state, tool hints, recent decisions
    tool_descriptions = build_tool_descriptions(
        registry,
        mode,
        session,
        selected_tools=tool_surface.selected,
    )
    recent_events = await audit.tail(limit=40)

    # Create tool registry dict for context builder
    tool_registry_dict = {tool.name: registry.get(tool.name) for tool in registry.list()}

    # Surface available sandbox regions to the agent (Phase A: agent
    # context plumbing for the Podman provider). Empty/None when no
    # actuator is wired so the system prompt skips the section.
    sandbox_summary: str | None = None
    pc = tool_client.policy_context
    if pc is not None and pc.sandbox_actuator is not None:
        actuator = pc.sandbox_actuator
        # Best-effort: actuator may not expose its specs (e.g. demo).
        specs = getattr(actuator, "_specs", None)
        if specs:
            lines = ["Available disposable regions:"]
            for spec_id, spec in specs.items():
                net = getattr(spec, "network", "?")
                img = getattr(spec, "image", "?")
                lines.append(f"  - {spec_id}: image={img}, network={net}")
            sandbox_summary = "\n".join(lines)
        else:
            sandbox_summary = "A SandboxActuator is wired (provider details unavailable)."

    llm_context = build_llm_context(
        session,
        tool_descriptions,
        tool_registry_dict,
        recent_events,
        max_recent_decisions=10 if not conversational else 0,
        sandbox_summary=None if conversational else sandbox_summary,
        tool_call_instructions=tool_call_instructions_for_llm(effective_llm),
        chat_only=conversational,
    )

    # Audit the context for replay purposes
    await audit.write(
        Event(
            event_type=EventType.LLM_CONTEXT_ASSEMBLED,
            session_id=session_id,
            turn_id=len(session.history),
            payload={
                "context_hash": llm_context.context_hash,
                "n_tools": llm_context.n_tools,
                "n_recent_decisions": llm_context.n_recent_decisions,
            },
        ),
    )

    # Use the enriched context as system prompt
    messages: list[Message] = [Message(role=Role.SYSTEM, content=llm_context.system_prompt)]
    for turn in session.history:
        message = _turn_to_message(turn)
        if message is not None:
            messages.append(message)

    # Defense-in-depth: if visible_tools is empty, the LLM has no
    # tool_use options at all. Without explicit notice, models tend
    # to "be helpful" by writing code-block tool calls as text — which
    # bypasses the runtime entirely. Tell the LLM, in plain language,
    # that the list is empty and what the user has to do to fix it.
    if not tool_descriptions and not conversational:
        messages.append(_no_tools_notice_message(session))
    visible_tool_names = {t.name for t in visible_all}
    reverse_map: dict[str, str] = {}
    if session.tool_aliasing:
        reverse_map = build_reverse_map(session.id, [t.name for t in visible_all])
    tool_outcomes: list[ToolCallOutcome] = []

    iteration = 0
    last_response: LLMResponse | None = None
    parse_retry_used = False
    image_gen_retry_used = False
    force_image_generate = should_force_image_generate_tool(user_message)
    probable_image_request = has_probable_image_generation_intent(user_message)
    selected_tool_names = {t.name for t in tool_surface.selected}
    visible_tool_names_all = {t.name for t in visible_all}
    if (force_image_generate or probable_image_request) and not image_generate_tool_names_in(
        visible_tool_names_all
    ):
        final_content = (
            "I cannot generate an image in this session because no generated-image "
            "tool is registered or visible to the daemon. I will not invent a local "
            "image path; enable an admitted image generation tool and retry."
        )
        await audit.write(
            Event(
                event_type=EventType.LLM_ERROR,
                session_id=session_id,
                turn_id=len(session.history),
                step_id=0,
                payload={
                    "error_type": "ImageGenerationToolUnavailable",
                    "message": final_content,
                    "image_generation_intent": True,
                    "n_visible_tools": len(visible_all),
                    "selected_tools": list(selected_tool_names),
                },
            ),
        )
        agent_turn = Turn(
            turn_id=len(session.history),
            role="agent",
            content=final_content,
        )
        await graph.add_turn(session_id, agent_turn)
        yield TurnCompleted(
            iteration=0,
            result=AgentTurnResult(
                content=final_content,
                iterations=0,
                finish_reason=FinishReason.STOP,
                tool_outcomes=(),
            ),
        )
        return
    # Issue #36 — context-window guardrail.
    # We learn the model name on the first response; until then we
    # use a conservative default. The soft warning fires at 80%, the
    # hard limit at 90%. Once a warning fires for a given iteration,
    # we don't re-fire on subsequent iterations of the same turn so
    # the audit log stays scannable.
    context_warning_emitted = False
    last_model: str | None = None
    # Issue #2 — loop-thrash detection + replayable abnormal-termination.
    # `call_counts` tallies identical (tool, args) signatures across the
    # whole turn; `recent_calls` keeps the last-N proposed calls (name +
    # args) for the audit payload when the loop ends abnormally.
    call_counts: dict[str, int] = {}
    recent_calls: list[dict[str, object]] = []

    while iteration < max_iterations:
        iteration += 1
        # Issue #23 — user cancellation. Checked at the top of every
        # iteration (before any LLM/tool work) so a cancel arriving
        # while we were awaiting the previous tool result is honored
        # immediately. The cancel_check polls a daemon-side flag that
        # the session.cancel RPC flips. Partial output produced so far
        # is preserved in `last_response`/`tool_outcomes` and surfaced
        # in the TurnInterrupted event.
        if cancel_check is not None and cancel_check():
            await audit.write(
                Event(
                    event_type=EventType.LLM_ERROR,
                    session_id=session_id,
                    turn_id=len(session.history),
                    step_id=iteration,
                    payload={
                        "error_type": "UserCancelled",
                        "message": "turn cancelled by user (Ctrl-C)",
                        "iteration": iteration,
                    },
                ),
            )
            yield TurnInterrupted(
                iteration=iteration,
                reason="user_cancelled",
                partial_content=(last_response.content if last_response else ""),
                partial_outcomes=tuple(tool_outcomes),
            )
            return

        # Issue #21 — yield IterationStarted before any work. Lets the
        # REPL render "iter N/max" indicators in real time.
        yield IterationStarted(iteration=iteration)

        # Issue #36 — context-size preflight.
        # Estimate the assembled context. If we're over the hard limit
        # for the model we last saw, yield TurnInterrupted and return.
        # If we're over the soft threshold (80%), audit a warning and
        # append a system notice so the LLM knows to wrap up rather
        # than fetching more.
        window = _context_window_for(last_model)
        estimated = _estimate_message_tokens(messages, tool_descriptions)
        ratio = estimated / window if window else 0.0

        if ratio >= _HARD_LIMIT_THRESHOLD:
            await audit.write(
                Event(
                    event_type=EventType.LLM_ERROR,
                    session_id=session_id,
                    turn_id=len(session.history),
                    step_id=iteration,
                    payload={
                        "error_type": "ContextOverflowError",
                        "message": (
                            f"context ~{estimated:,} tokens >= 90% of "
                            f"{window:,}-token window; refusing to send"
                        ),
                        "iteration": iteration,
                        "context_tokens_estimate": estimated,
                        "context_window": window,
                        "ratio": round(ratio, 2),
                        "model": last_model,
                    },
                ),
            )
            yield TurnInterrupted(
                iteration=iteration,
                reason="context_overflow",
                partial_content=(last_response.content if last_response else ""),
                partial_outcomes=tuple(tool_outcomes),
            )
            return

        if ratio >= _SOFT_WARNING_THRESHOLD and not context_warning_emitted:
            # Audit the warning + inject a system message so the LLM
            # knows it's running out of room. The LLM should respond
            # with a final summary rather than more tool calls.
            await audit.write(
                Event(
                    event_type=EventType.LLM_CONTEXT_WARNING,
                    session_id=session_id,
                    turn_id=len(session.history),
                    step_id=iteration,
                    payload={
                        "iteration": iteration,
                        "context_tokens_estimate": estimated,
                        "context_window": window,
                        "ratio": round(ratio, 2),
                        "model": last_model,
                    },
                ),
            )
            messages.append(
                Message(
                    role=Role.SYSTEM,
                    content=(
                        f"NOTICE: your context size (~{estimated:,} tokens) "
                        f"is approaching the {window:,}-token window. "
                        f"STOP making new tool calls. Summarize what you "
                        f"have found into a final answer for the user. "
                        f"If you need more data, ask the user to /spawn "
                        f"a fresh session with a more targeted query."
                    ),
                ),
            )
            context_warning_emitted = True

        await audit.write(
            Event(
                event_type=EventType.LLM_REQUEST_SENT,
                session_id=session_id,
                turn_id=len(session.history),
                step_id=iteration,
                payload={
                    "n_messages": len(messages),
                    "n_tools": len(tool_descriptions),
                    "context_tokens_estimate": estimated,
                    # Surfacing the window lets the chat REPL render a
                    # `N / M (P%)` token counter in the bottom toolbar
                    # without having to know the model→window table on
                    # the client side. The streaming audit consumer in
                    # `cli/chat.py:_send_message_streaming` reads this
                    # alongside the estimate.
                    "context_window": window,
                },
            ),
        )
        yield LLMRequestSent(
            iteration=iteration,
            n_messages=len(messages),
            n_tools=len(tool_descriptions),
        )

        # Issue #36 — wrap llm.respond() so exceptions audit cleanly
        # before propagating. Without this, LLM errors (rate limit,
        # actual context overflow from the provider, network timeout,
        # malformed response, etc.) propagated up to the daemon's RPC
        # handler silently — operators saw a red "rpc error" in chat
        # but the audit log had no trace.
        try:
            chat_max_tokens = CHAT_MAX_TOKENS if conversational else None
            stream_fn = getattr(effective_llm, "respond_streaming", None)
            if stream_fn is not None:
                accumulated = ""
                async for chunk in stream_fn(
                    messages,
                    tool_descriptions,
                    max_tokens=chat_max_tokens,
                ):
                    accumulated += chunk
                    yield LLMTokenReceived(iteration=iteration, text=chunk)
                response = finalize_mlx_text(
                    accumulated,
                    tool_descriptions,
                    model=str(getattr(effective_llm, "_model", last_model or "unknown")),
                )
                try:
                    from capabledeputy.debug.chat_trace import log

                    preview = accumulated if len(accumulated) <= 800 else accumulated[:797] + "…"
                    log(
                        "llm_finalized",
                        iteration=iteration,
                        raw_len=len(accumulated),
                        raw_preview=preview,
                        content_len=len(response.content),
                        content_preview=(
                            response.content
                            if len(response.content) <= 800
                            else response.content[:797] + "…"
                        ),
                        model=response.model,
                        finish_reason=response.finish_reason.value,
                        conversational=conversational,
                    )
                except Exception:
                    pass
            elif isinstance(effective_llm, MLXLLMClient):
                response = await effective_llm.respond(
                    messages,
                    tool_descriptions,
                    max_tokens=chat_max_tokens,
                )
            else:
                response = await effective_llm.respond(messages, tool_descriptions)
        except Exception as e:
            await audit.write(
                Event(
                    event_type=EventType.LLM_ERROR,
                    session_id=session_id,
                    turn_id=len(session.history),
                    step_id=iteration,
                    payload={
                        "error_type": type(e).__name__,
                        "message": str(e)[:500],
                        "iteration": iteration,
                        "context_tokens_estimate": estimated,
                        "context_window": window,
                        "model": last_model,
                    },
                ),
            )
            # Emit a streaming TurnErrored for surfaces consuming the
            # generator (chat REPL Live region, future rich surface).
            yield TurnInterrupted(
                iteration=iteration,
                reason=f"llm_error:{type(e).__name__}",
                partial_content=(last_response.content if last_response else ""),
                partial_outcomes=tuple(tool_outcomes),
            )
            return
        last_model = response.model
        last_response = response

        await audit.write(
            Event(
                event_type=EventType.LLM_RESPONSE_RECEIVED,
                session_id=session_id,
                turn_id=len(session.history),
                step_id=iteration,
                payload={
                    "content_length": len(response.content),
                    "content_preview": (
                        response.content
                        if len(response.content) <= 300
                        else response.content[:297] + "…"
                    ),
                    "n_tool_calls": len(response.tool_calls),
                    "finish_reason": response.finish_reason.value,
                    "model": response.model,
                    # Real provider usage (may be empty for fakes or
                    # if the provider didn't surface it). The toolbar's
                    # usage segment sums these across the session and
                    # the calendar month — toolbar reads via the
                    # streaming consumer in cli/chat.py.
                    "prompt_tokens": int(response.usage.get("prompt_tokens", 0)),
                    "completion_tokens": int(
                        response.usage.get("completion_tokens", 0),
                    ),
                },
            ),
        )
        yield LLMResponseReceived(
            iteration=iteration,
            content_length=len(response.content),
            n_tool_calls=len(response.tool_calls),
            finish_reason=response.finish_reason.value,
            model=response.model or "unknown",
        )

        if (
            not response.tool_calls
            and tool_descriptions
            and planner_uses_json_tools(effective_llm)
            and not parse_retry_used
            and _looks_like_failed_tool_parse(response.content or "")
        ):
            parse_retry_used = True
            await audit.write(
                Event(
                    event_type=EventType.LLM_PARSE_RETRY,
                    session_id=session_id,
                    turn_id=len(session.history),
                    step_id=iteration,
                    payload={"iteration": iteration, "content_length": len(response.content)},
                ),
            )
            messages.append(
                Message(role=Role.ASSISTANT, content=response.content or ""),
            )
            messages.append(Message(role=Role.SYSTEM, content=_PARSE_RETRY_NOTICE))
            continue

        prior_image_paths = collect_prior_work_image_paths(
            *(
                message.content
                for message in messages
                if message.content and message.role != Role.TOOL
            ),
        )
        allowed_image_paths = allowed_image_generate_paths(tool_outcomes)
        image_gen_refusal = looks_like_image_generation_refusal(response.content or "")
        image_path_hallucination = looks_like_hallucinated_image_markdown(
            response.content or "",
            prior_paths=prior_image_paths,
            allowed_paths=allowed_image_paths,
        )
        image_gen_mandatory = force_image_generate or (probable_image_request and image_gen_refusal)
        if (
            not response.tool_calls
            and tool_descriptions
            and not image_gen_retry_used
            and image_generate_tool_names_in(selected_tool_names)
            and planner_uses_json_tools(effective_llm)
            and (
                (not tool_outcomes and image_gen_mandatory)
                or (force_image_generate and image_path_hallucination)
            )
        ):
            image_gen_retry_used = True
            retry_reason = (
                "image_path_hallucination"
                if image_path_hallucination
                else "image_generation_mandatory"
            )
            retry_notice = (
                IMAGE_PATH_HALLUCINATION_RETRY_NOTICE
                if image_path_hallucination
                else IMAGE_GENERATION_RETRY_NOTICE
            )
            await audit.write(
                Event(
                    event_type=EventType.LLM_PARSE_RETRY,
                    session_id=session_id,
                    turn_id=len(session.history),
                    step_id=iteration,
                    payload={
                        "iteration": iteration,
                        "reason": retry_reason,
                        "content_length": len(response.content),
                    },
                ),
            )
            messages.append(
                Message(role=Role.ASSISTANT, content=response.content or ""),
            )
            messages.append(Message(role=Role.SYSTEM, content=retry_notice))
            continue

        if not response.tool_calls:
            final_content = response.content or ""
            if force_image_generate or probable_image_request:
                final_content = repair_hallucinated_image_markdown(
                    final_content,
                    prior_paths=prior_image_paths,
                    allowed_paths=allowed_image_paths,
                    outcomes=tool_outcomes,
                )
            final_content = _repair_foreground_recovery_leak(
                final_content,
                session=session,
                outcomes=tool_outcomes,
            )
            agent_turn = Turn(
                turn_id=len(session.history),
                role="agent",
                content=final_content,
            )
            await graph.add_turn(session_id, agent_turn)
            await graph.add_session_artifacts(
                session_id,
                _generated_image_artifacts_from_outcomes(
                    tool_outcomes,
                    origin_turn_id=agent_turn.turn_id,
                ),
            )
            yield TurnCompleted(
                iteration=iteration,
                result=AgentTurnResult(
                    content=final_content,
                    iterations=iteration,
                    finish_reason=response.finish_reason,
                    tool_outcomes=tuple(tool_outcomes),
                ),
            )
            return

        messages.append(
            Message(
                role=Role.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            ),
        )

        response_tool_names = {
            reverse_map.get(tool_call.name, tool_call.name) for tool_call in response.tool_calls
        }
        for tool_call in response.tool_calls:
            # Reverse-map alias → canonical name. If the LLM produces a
            # token that doesn't match any visible tool's alias, the
            # name passes through untouched and ToolNotFoundError fires
            # below with the unmatched string in the message.
            real_name = reverse_map.get(tool_call.name, tool_call.name)

            # Issue #2 — record the proposed call and detect thrash
            # (same tool + same args repeated) BEFORE dispatching, so a
            # confirmed non-converging loop stops without burning the
            # rest of the iteration budget on a call we already know the
            # outcome of.
            signature = _call_signature(real_name, tool_call.args)
            call_counts[signature] = call_counts.get(signature, 0) + 1
            recent_calls.append(
                {
                    "iteration": iteration,
                    "tool_name": real_name,
                    "args": tool_call.args,
                },
            )
            del recent_calls[:-_LAST_CALLS_RETAINED]
            if call_counts[signature] >= _THRASH_REPEAT_THRESHOLD:
                await audit.write(
                    Event(
                        event_type=EventType.AGENT_LOOP_THRASHING,
                        session_id=session_id,
                        turn_id=len(session.history),
                        step_id=iteration,
                        payload={
                            "iteration": iteration,
                            "repeated_tool": real_name,
                            "repeat_count": call_counts[signature],
                            "threshold": _THRASH_REPEAT_THRESHOLD,
                            "last_calls": list(recent_calls),
                        },
                    ),
                )
                yield TurnInterrupted(
                    iteration=iteration,
                    reason="agent_loop_thrashing",
                    partial_content=(last_response.content if last_response else ""),
                    partial_outcomes=tuple(tool_outcomes),
                )
                return

            yield ToolDispatched(
                iteration=iteration,
                tool_name=real_name,
                tool_args=tool_call.args,
            )
            try:
                registry.get(real_name)
            except ToolNotFoundError:
                tool_surface = widen_tool_surface(
                    tool_surface,
                    visible_all,
                    missing_tool_name=real_name,
                )
                tool_descriptions = build_tool_descriptions(
                    registry,
                    mode,
                    session,
                    selected_tools=tool_surface.selected,
                )
                visible_tool_names = {t.name for t in tool_surface.selected}
                outcome = ToolCallOutcome(
                    decision=Decision.DENY,
                    reason=f"tool not found: {tool_call.name}",
                    tool_name=tool_call.name,
                    tool_args=tool_call.args,
                )
            else:
                if real_name not in visible_tool_names:
                    visible_by_name = {tool.name: tool for tool in visible_all}
                    if real_name in visible_by_name:
                        tool_surface = widen_tool_surface(
                            tool_surface,
                            visible_all,
                            missing_tool_name=real_name,
                        )
                        tool_descriptions = build_tool_descriptions(
                            registry,
                            mode,
                            session,
                            selected_tools=tool_surface.selected,
                        )
                        visible_tool_names = {tool.name for tool in tool_surface.selected}
                        outcome = await tool_client.call_tool(
                            session_id,
                            real_name,
                            tool_call.args,
                        )
                    else:
                        outcome = ToolCallOutcome(
                            decision=Decision.DENY,
                            rule="tool-not-visible-in-current-mode",
                            reason=(
                                f"tool {real_name!r} is not visible in "
                                f"{mode.value} mode for this session"
                            ),
                            tool_name=real_name,
                            tool_args=tool_call.args,
                        )
                else:
                    outcome = await tool_client.call_tool(
                        session_id,
                        real_name,
                        tool_call.args,
                    )

            tool_outcomes.append(outcome)
            yield ToolReturned(iteration=iteration, outcome=outcome)
            messages.append(
                Message(
                    role=Role.TOOL,
                    content=_format_outcome(outcome),
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                ),
            )

            updated_session = graph.get(session_id)
            if updated_session.label_state != session.label_state:
                session = updated_session
                try:
                    mode, mode_reason = select_mode(
                        session.label_state,
                        registry,
                        prefer_programmatic=session.prefer_programmatic,
                        force_mode=force_mode,
                        has_sandbox_actuator=has_sandbox_actuator,
                        session=session,
                        posture=active_posture,
                    )
                except ModeSelectionError as e:
                    await audit.write(
                        Event(
                            event_type=EventType.MODE_SELECTED,
                            session_id=session_id,
                            turn_id=len(session.history),
                            step_id=iteration,
                            payload={
                                "refused": True,
                                "tier": "restricted",
                                "reason": str(e),
                            },
                        ),
                    )
                    yield TurnInterrupted(
                        iteration=iteration,
                        reason="mode_refused_restricted",
                        partial_content=(last_response.content if last_response else ""),
                        partial_outcomes=tuple(tool_outcomes),
                    )
                    return

                await audit.write(
                    Event(
                        event_type=EventType.MODE_SELECTED,
                        session_id=session_id,
                        turn_id=len(session.history),
                        step_id=iteration,
                        payload={"mode": mode.value, "reason": mode_reason},
                    ),
                )

                prior_selected_names = {tool.name for tool in tool_surface.selected}
                prior_selected_names.update(response_tool_names)
                visible_all = visible_tools(registry, session, mode, active_posture)
                tool_surface = await select_tools_for_turn_async(
                    registry,
                    session,
                    mode,
                    visible_all,
                    user_message=user_message,
                    router_llm=router_llm,
                    families=families_cfg,
                    selection_config=selection_cfg,
                )
                if prior_selected_names:
                    visible_by_name = {tool.name: tool for tool in visible_all}
                    selected_by_name = {tool.name: tool for tool in tool_surface.selected}
                    for name in prior_selected_names:
                        tool = visible_by_name.get(name)
                        if tool is not None:
                            selected_by_name[name] = tool
                    if set(selected_by_name) != {tool.name for tool in tool_surface.selected}:
                        tool_surface = ToolSelectionResult(
                            selected=tuple(
                                sorted(selected_by_name.values(), key=lambda tool: tool.name),
                            ),
                            candidates=tool_surface.candidates,
                            n_visible=tool_surface.n_visible,
                            method=f"{tool_surface.method}+retained",
                            mandatory_added=tool_surface.mandatory_added,
                            scores=tool_surface.scores,
                        )
                tool_descriptions = build_tool_descriptions(
                    registry,
                    mode,
                    session,
                    selected_tools=tool_surface.selected,
                )
                visible_tool_names = {t.name for t in tool_surface.selected}
                reverse_map = (
                    build_reverse_map(session.id, [t.name for t in visible_all])
                    if session.tool_aliasing
                    else {}
                )
                recent_events = await audit.tail(limit=40)
                llm_context = build_llm_context(
                    session,
                    tool_descriptions,
                    tool_registry_dict,
                    recent_events,
                    max_recent_decisions=10,
                    sandbox_summary=sandbox_summary,
                    tool_call_instructions=tool_call_instructions_for_llm(effective_llm),
                )
                messages[0] = Message(role=Role.SYSTEM, content=llm_context.system_prompt)
                await audit.write(
                    Event(
                        event_type=EventType.LLM_CONTEXT_ASSEMBLED,
                        session_id=session_id,
                        turn_id=len(session.history),
                        step_id=iteration,
                        payload={
                            "context_hash": llm_context.context_hash,
                            "n_tools": llm_context.n_tools,
                            "n_recent_decisions": llm_context.n_recent_decisions,
                            "refreshed_after_tool": real_name,
                        },
                    ),
                )
                messages.append(
                    Message(
                        role=Role.SYSTEM,
                        content=(
                            "NOTICE: session labels changed after tool "
                            f"{real_name!r}. Execution mode is now "
                            f"{mode.value!r}; the tool list for subsequent "
                            "calls has been refreshed. Do not call tools "
                            "that are no longer listed."
                        ),
                    ),
                )
                if not tool_descriptions:
                    messages.append(_no_tools_notice_message(session))

    # Loop exceeded max_iterations. Issue #2 — audit the cap-fire with
    # the last-N tool calls so the pathological turn is inspectable /
    # replayable instead of vanishing into an opaque RPC error. Then
    # yield the terminal TurnInterrupted so streaming consumers can
    # render the partial state; the run_turn wrapper detects this and
    # raises AgentLoopExceededError for backwards compatibility.
    await audit.write(
        Event(
            event_type=EventType.AGENT_LOOP_EXCEEDED,
            session_id=session_id,
            turn_id=len(session.history),
            step_id=iteration,
            payload={
                "max_iterations": max_iterations,
                "iterations": iteration,
                "last_finish_reason": (
                    last_response.finish_reason.value if last_response else None
                ),
                "last_calls": list(recent_calls),
            },
        ),
    )
    partial_content = last_response.content if last_response else ""
    yield TurnInterrupted(
        iteration=iteration,
        reason="max_iterations",
        partial_content=partial_content,
        partial_outcomes=tuple(tool_outcomes),
    )
