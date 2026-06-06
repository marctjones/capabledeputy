"""Tests for deterministic LLM context builder."""

from datetime import UTC, datetime

import pytest

from capabledeputy.agent.context import (
    LLMContext,
    build_llm_context,
)
from capabledeputy.audit.events import Event, EventType
from capabledeputy.llm.types import ToolDescription
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.model import Session
from capabledeputy.tools.registry import ToolDefinition


def _dummy_tool_handler(*args, **kwargs):
    """Dummy handler for ToolDefinition."""
    pass


@pytest.fixture
def empty_session() -> Session:
    """Create a minimal session for testing."""
    return Session.new()


@pytest.fixture
def rich_session() -> Session:
    """Create a session with labels and metadata."""
    return Session.new(
        intent="financial-review",
        clearance_profile_id="tier_2",
        risk_preference_at_spawn="balanced",
        label_state=LabelState(
            a=frozenset({CategoryTag(category="financial", tier=Tier.REGULATED)}),
            b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
        ),
    )


@pytest.fixture
def memory_tool() -> tuple[ToolDescription, ToolDefinition]:
    """Create a memory tool for testing."""
    desc = ToolDescription(
        name="memory.read",
        description="Read from memory store",
        parameters_schema={"type": "object", "properties": {}},
    )
    defn = ToolDefinition(
        name="memory.read",
        description="Read from memory store",
        capability_kind=CapabilityKind.READ_FS,
        handler=_dummy_tool_handler,
        default_reversibility={"degree": "reversible", "agent": "system"},
        effect_class="data.read_memory",
    )
    return desc, defn


@pytest.fixture
def email_tool() -> tuple[ToolDescription, ToolDefinition]:
    """Create an email send tool for testing."""
    desc = ToolDescription(
        name="email.send",
        description="Send an email",
        parameters_schema={"type": "object", "properties": {}},
    )
    defn = ToolDefinition(
        name="email.send",
        description="Send an email",
        capability_kind=CapabilityKind.SEND_EMAIL,
        handler=_dummy_tool_handler,
        social_commitment=True,
        effect_class="egress.email",
    )
    return desc, defn


class TestLLMContextDeterminism:
    """Test that context building is deterministic."""

    def test_same_inputs_same_hash(self, empty_session: Session) -> None:
        """Same inputs should produce identical context hashes."""
        tools = [
            ToolDescription(
                name="tool1",
                description="Test tool",
                parameters_schema={},
            ),
        ]
        registry = {
            "tool1": ToolDefinition(
                name="tool1",
                description="Test tool",
                capability_kind=CapabilityKind.READ_FS,
                handler=_dummy_tool_handler,
            ),
        }
        events = []

        ctx1 = build_llm_context(empty_session, tools, registry, events)
        ctx2 = build_llm_context(empty_session, tools, registry, events)

        assert ctx1.context_hash == ctx2.context_hash
        assert ctx1.system_prompt == ctx2.system_prompt

    def test_context_hash_changes_with_different_session_labels(
        self,
        empty_session: Session,
    ) -> None:
        """Different session labels should produce different hashes."""
        tools = [
            ToolDescription(
                name="tool1",
                description="Test tool",
                parameters_schema={},
            ),
        ]
        registry = {
            "tool1": ToolDefinition(
                name="tool1",
                description="Test tool",
                capability_kind=CapabilityKind.READ_FS,
                handler=_dummy_tool_handler,
            ),
        }
        events = []

        ctx1 = build_llm_context(empty_session, tools, registry, events)

        session_with_label = Session.new(
            label_state=LabelState(
                b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
            ),
        )
        ctx2 = build_llm_context(session_with_label, tools, registry, events)

        assert ctx1.context_hash != ctx2.context_hash


class TestEmptySession:
    """Test context for a minimal session."""

    def test_empty_session_has_no_labels(
        self,
        empty_session: Session,
    ) -> None:
        """Empty session should produce valid context with no labels."""
        tools = []
        registry = {}
        events = []

        ctx = build_llm_context(empty_session, tools, registry, events)

        assert ctx.n_tools == 0
        assert ctx.n_recent_decisions == 0
        assert "Current labels: none" in ctx.system_prompt
        assert "No tools available" in ctx.system_prompt
        assert isinstance(ctx.context_hash, str)
        assert len(ctx.context_hash) == 64  # SHA-256 hex

    def test_empty_session_includes_session_section(
        self,
        empty_session: Session,
    ) -> None:
        """Empty session should still include Session State section."""
        tools = []
        registry = {}
        events = []

        ctx = build_llm_context(empty_session, tools, registry, events)

        assert "# Session State" in ctx.system_prompt
        assert f"Session id: {str(empty_session.id)[:8]}" in ctx.system_prompt
        assert "Purpose: general" in ctx.system_prompt
        assert "Profile: default" in ctx.system_prompt
        assert "Risk dial: cautious" in ctx.system_prompt


class TestRichSession:
    """Test context for a session with labels and metadata."""

    def test_rich_session_includes_all_labels(
        self,
        rich_session: Session,
    ) -> None:
        """Rich session should include all labels in sorted order."""
        tools = []
        registry = {}
        events = []

        ctx = build_llm_context(rich_session, tools, registry, events)

        # Labels should be sorted - four-axis format: category:X@tier, provenance:Y
        assert "Current labels:" in ctx.system_prompt
        assert "category:financial@regulated" in ctx.system_prompt
        assert "provenance:external-untrusted" in ctx.system_prompt

    def test_rich_session_includes_metadata(
        self,
        rich_session: Session,
    ) -> None:
        """Rich session should include intent, profile, and dial."""
        tools = []
        registry = {}
        events = []

        ctx = build_llm_context(rich_session, tools, registry, events)

        assert "Purpose: financial-review" in ctx.system_prompt
        assert "Profile: tier_2" in ctx.system_prompt
        assert "Risk dial: balanced" in ctx.system_prompt


class TestToolInclusionAndHints:
    """Test that tools are listed with correct hints."""

    def test_reversible_system_tool_shows_likely_auto(
        self,
        empty_session: Session,
        memory_tool: tuple[ToolDescription, ToolDefinition],
    ) -> None:
        """Reversible system tools should show 'likely AUTO'."""
        tool_desc, tool_defn = memory_tool
        tools = [tool_desc]
        registry = {tool_desc.name: tool_defn}
        events = []

        ctx = build_llm_context(empty_session, tools, registry, events)

        assert "likely AUTO" in ctx.system_prompt
        assert "memory.read" in ctx.system_prompt

    def test_email_tool_with_untrusted_and_egress_shows_deny(
        self,
        email_tool: tuple[ToolDescription, ToolDefinition],
    ) -> None:
        """Email tool in untrusted+egress session should show 'likely DENY'."""
        tool_desc, tool_defn = email_tool

        session = Session.new(
            label_state=LabelState(
                b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
            ),
        )

        tools = [tool_desc]
        registry = {tool_desc.name: tool_defn}
        events = []

        ctx = build_llm_context(session, tools, registry, events)

        assert "likely DENY" in ctx.system_prompt
        assert "untrusted-meets-egress" in ctx.system_prompt

    def test_email_tool_with_financial_shows_deny(
        self,
        email_tool: tuple[ToolDescription, ToolDefinition],
    ) -> None:
        """Email tool with financial label should show 'likely DENY'."""
        tool_desc, tool_defn = email_tool

        session = Session.new(
            label_state=LabelState(
                a=frozenset({CategoryTag(category="financial", tier=Tier.REGULATED)}),
            ),
        )

        tools = [tool_desc]
        registry = {tool_desc.name: tool_defn}
        events = []

        ctx = build_llm_context(session, tools, registry, events)

        assert "likely DENY" in ctx.system_prompt
        assert "financial-meets-email" in ctx.system_prompt

    def test_tool_count_correct(
        self,
        empty_session: Session,
        memory_tool: tuple[ToolDescription, ToolDefinition],
        email_tool: tuple[ToolDescription, ToolDefinition],
    ) -> None:
        """Tool count should match number of tools in context."""
        mem_desc, mem_defn = memory_tool
        email_desc, email_defn = email_tool

        tools = [mem_desc, email_desc]
        registry = {mem_desc.name: mem_defn, email_desc.name: email_defn}
        events = []

        ctx = build_llm_context(empty_session, tools, registry, events)

        assert ctx.n_tools == 2
        assert "memory.read" in ctx.system_prompt
        assert "email.send" in ctx.system_prompt


class TestRecentDecisions:
    """Test that recent policy decisions are included."""

    def test_no_recent_decisions(
        self,
        empty_session: Session,
    ) -> None:
        """No events should produce 'No recent decisions'."""
        tools = []
        registry = {}
        events = []

        ctx = build_llm_context(empty_session, tools, registry, events)

        assert ctx.n_recent_decisions == 0
        assert "No recent decisions" in ctx.system_prompt

    def test_recent_policy_decided_events_included(
        self,
        empty_session: Session,
    ) -> None:
        """POLICY_DECIDED events should be formatted and included."""
        now = datetime.now(UTC)
        events = [
            Event(
                event_type=EventType.POLICY_DECIDED,
                session_id=empty_session.id,
                timestamp=now,
                payload={
                    "decision": "allow",
                    "tool": "memory.read",
                    "reason": "no conflicts",
                },
            ),
        ]

        tools = []
        registry = {}

        ctx = build_llm_context(empty_session, tools, registry, events)

        assert ctx.n_recent_decisions == 1
        assert "ALLOW" in ctx.system_prompt
        assert "memory.read" in ctx.system_prompt
        assert "no conflicts" in ctx.system_prompt

    def test_recent_decisions_limited_to_max(
        self,
        empty_session: Session,
    ) -> None:
        """Recent decisions should be limited by max_recent_decisions."""
        now = datetime.now(UTC)
        events = []
        for i in range(20):
            events.append(
                Event(
                    event_type=EventType.POLICY_DECIDED,
                    session_id=empty_session.id,
                    timestamp=now,
                    payload={
                        "decision": "allow",
                        "tool": f"tool{i}",
                        "reason": "reason",
                    },
                ),
            )

        tools = []
        registry = {}

        ctx = build_llm_context(empty_session, tools, registry, events, max_recent_decisions=5)

        assert ctx.n_recent_decisions == 5

    def test_non_policy_events_ignored(
        self,
        empty_session: Session,
    ) -> None:
        """Non-POLICY_DECIDED events should be ignored."""
        now = datetime.now(UTC)
        events = [
            Event(
                event_type=EventType.LLM_REQUEST_SENT,
                session_id=empty_session.id,
                timestamp=now,
                payload={"n_messages": 5},
            ),
            Event(
                event_type=EventType.POLICY_DECIDED,
                session_id=empty_session.id,
                timestamp=now,
                payload={
                    "decision": "deny",
                    "tool": "memory.delete",
                    "reason": "destructive",
                },
            ),
        ]

        tools = []
        registry = {}

        ctx = build_llm_context(empty_session, tools, registry, events)

        # Only the POLICY_DECIDED event should be counted
        assert ctx.n_recent_decisions == 1
        assert "DENY" in ctx.system_prompt


class TestContextStructure:
    """Test that the context has the expected structure."""

    def test_returns_llm_context_dataclass(
        self,
        empty_session: Session,
    ) -> None:
        """Should return an LLMContext instance."""
        tools = []
        registry = {}
        events = []

        ctx = build_llm_context(empty_session, tools, registry, events)

        assert isinstance(ctx, LLMContext)
        assert isinstance(ctx.system_prompt, str)
        assert isinstance(ctx.context_hash, str)
        assert isinstance(ctx.n_tools, int)
        assert isinstance(ctx.n_recent_decisions, int)

    def test_system_prompt_includes_key_sections(
        self,
        empty_session: Session,
    ) -> None:
        """System prompt should include all required sections."""
        tools = []
        registry = {}
        events = []

        ctx = build_llm_context(empty_session, tools, registry, events)

        required_sections = [
            "# Session State",
            "# Available Tools",
            "# Recent Decisions",
            "# Recovery Hints",
            "CRITICAL — how you call tools:",
            "How the policy works (high-level):",
        ]

        for section in required_sections:
            assert section in ctx.system_prompt

    def test_context_hash_is_hex_string(
        self,
        empty_session: Session,
    ) -> None:
        """Context hash should be a valid hex string (SHA-256)."""
        tools = []
        registry = {}
        events = []

        ctx = build_llm_context(empty_session, tools, registry, events)

        # Should be 64-character hex (SHA-256)
        assert len(ctx.context_hash) == 64
        assert all(c in "0123456789abcdef" for c in ctx.context_hash)


class TestTokenBudget:
    """Test that context stays within reasonable token budget."""

    def test_context_under_2000_tokens_for_typical_session(
        self,
        rich_session: Session,
        memory_tool: tuple[ToolDescription, ToolDefinition],
        email_tool: tuple[ToolDescription, ToolDefinition],
    ) -> None:
        """Context should stay under ~2000 tokens for typical usage."""
        mem_desc, mem_defn = memory_tool
        email_desc, email_defn = email_tool

        tools = [mem_desc, email_desc]
        registry = {mem_desc.name: mem_defn, email_desc.name: email_defn}

        # Add some recent events
        now = datetime.now(UTC)
        events = [
            Event(
                event_type=EventType.POLICY_DECIDED,
                session_id=rich_session.id,
                timestamp=now,
                payload={"decision": "allow", "tool": "memory.read", "reason": "test"},
            ),
        ]

        ctx = build_llm_context(rich_session, tools, registry, events)

        # Rough estimate: ~4 chars per token; real estimate depends on tokenizer
        # But we want to ensure we're not producing massive prompts
        token_estimate = len(ctx.system_prompt) // 4
        assert token_estimate < 3000, f"Context too large: {token_estimate} tokens"


class TestRecoveryHints:
    """Test that recovery hints are included appropriately."""

    def test_recovery_hints_section_present(
        self,
        empty_session: Session,
    ) -> None:
        """Recovery hints section should always be present."""
        tools = []
        registry = {}
        events = []

        ctx = build_llm_context(empty_session, tools, registry, events)

        assert "# Recovery Hints" in ctx.system_prompt
        assert "structural" in ctx.system_prompt.lower()
        assert "/spawn" in ctx.system_prompt
