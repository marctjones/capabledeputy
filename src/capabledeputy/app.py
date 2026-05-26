"""Top-level App: composes audit, store, graph, tool registry, dispatcher, LLM."""

from __future__ import annotations

from pathlib import Path

from capabledeputy.approval.queue import ApprovalQueue
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.llm.client import LLMClient
from capabledeputy.paths import default_audit_log_path, default_state_db_path
from capabledeputy.policy.purposes import Purposes
from capabledeputy.resources.static import StaticResourcePublisher
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.store import SessionStore
from capabledeputy.tools.client import LabeledToolClient, PolicyContext
from capabledeputy.tools.native.calendar import CalendarStore, make_calendar_tools
from capabledeputy.tools.native.email import DraftBox, EmailOutbox, make_email_tools
from capabledeputy.tools.native.extract import make_extract_tools
from capabledeputy.tools.native.fs import make_fs_tools
from capabledeputy.tools.native.inbox import Inbox, make_inbox_tools
from capabledeputy.tools.native.memory import LabeledMemoryStore, make_memory_tools
from capabledeputy.tools.native.policy_preview import make_policy_preview_tools
from capabledeputy.tools.native.purchase import PurchaseQueue, make_purchase_tools
from capabledeputy.tools.native.resources import make_resources_tools
from capabledeputy.tools.native.tasks import TaskStore, make_tasks_tools
from capabledeputy.tools.native.web import WebMock, make_web_tools
from capabledeputy.tools.registry import ToolRegistry


class App:
    def __init__(
        self,
        state_db_path: Path | None = None,
        audit_log_path: Path | None = None,
        llm_client: LLMClient | None = None,
        quarantined_llm: LLMClient | None = None,
        skills_dir: Path | None = None,
        enable_policy_preview: bool = True,
        policy_context: PolicyContext | None = None,
        purposes: Purposes | None = None,
        resources: StaticResourcePublisher | None = None,
    ) -> None:
        self.audit = AuditWriter(audit_log_path or default_audit_log_path())
        self.store = SessionStore(state_db_path or default_state_db_path())
        # 003 runtime activation — SessionGraph receives the Purposes
        # registry so spawn/grant/delegate enforce FR-009 admissibility.
        self.graph = SessionGraph(
            audit=self.audit,
            store=self.store,
            purposes=purposes,
        )
        self.memory = LabeledMemoryStore()
        self.purchase_queue = PurchaseQueue()
        self.email_outbox = EmailOutbox()
        self.email_drafts = DraftBox()
        self.calendar = CalendarStore()
        self.inbox = Inbox()
        self.web = WebMock()
        self.tasks = TaskStore()
        # Operator-published resources (configs/resources.yaml). Empty
        # publisher when the operator hasn't declared any; the
        # resources.list / resources.read tools register either way
        # but return empty catalogs.
        self.resources = resources or StaticResourcePublisher(resources=())
        # 002 US2 — approval queue receives the graph so it can
        # validate at approve-time that the capability_requested is
        # not cascaded-inert. Without the graph, the queue still
        # works but skips cascade invalidation (back-compat).
        self.approval_queue = ApprovalQueue(audit=self.audit, graph=self.graph)
        self.registry = ToolRegistry()
        self.policy_context = policy_context
        self.purposes = purposes
        # 003 runtime activation — LabeledToolClient receives the
        # PolicyContext bus so engine.decide() composes the v2 axes,
        # envelope dial, override grants, bindings, etc. With None
        # the dispatcher behaves exactly as v0.7.
        self.tool_client = LabeledToolClient(
            self.registry,
            self.graph,
            self.audit,
            approval_queue=self.approval_queue,
            policy_context=policy_context,
        )
        self.llm_client: LLMClient | None = llm_client
        self.quarantined_llm: LLMClient | None = quarantined_llm or llm_client
        self._skills_dir = skills_dir
        self._enable_policy_preview = enable_policy_preview
        self._register_native_tools()
        self._maybe_load_skills()

    def _register_native_tools(self) -> None:
        for tool in make_memory_tools(self.memory):
            self.registry.register(tool)
        for tool in make_purchase_tools(self.purchase_queue):
            self.registry.register(tool)
        for tool in make_email_tools(self.email_outbox, self.email_drafts):
            self.registry.register(tool)
        for tool in make_calendar_tools(self.calendar):
            self.registry.register(tool)
        for tool in make_inbox_tools(self.inbox):
            self.registry.register(tool)
        for tool in make_web_tools(self.web):
            self.registry.register(tool)
        for tool in make_tasks_tools(self.tasks):
            self.registry.register(tool)
        for tool in make_fs_tools():
            self.registry.register(tool)
        for tool in make_resources_tools(self.resources):
            self.registry.register(tool)
        # policy.preview lets the agent dry-run a policy decision so it
        # can plan around gates. It is read-only and OFF the enforcement
        # path (decide() runs unconditionally at dispatch regardless).
        # Disabling it does not strengthen enforcement; it (a) makes
        # agent policy-probing show up as loud audited denied calls
        # instead of silent queries, and (b) keeps the agent's
        # capability surface strictly minimal. Default on.
        if self._enable_policy_preview:
            for tool in make_policy_preview_tools(self.graph):
                self.registry.register(tool)
        # 004 U036: agent-callable sandbox.run tool when an actuator
        # is wired. Returns an empty list when the policy context has
        # no sandbox_actuator, so the tool list stays clean on installs
        # without Podman.
        from capabledeputy.tools.native.sandbox import make_sandbox_tools

        # Pass the audit writer so the sandbox tool emits
        # ISOLATION_REGION_CREATED / ISOLATION_REGION_DISCARDED events
        # for region lifecycle (FR-040, Pattern ⑤ audit trail).
        for tool in make_sandbox_tools(self.policy_context, audit=self.audit):
            self.registry.register(tool)
        if self.quarantined_llm is not None:
            for tool in make_extract_tools(self.memory, self.quarantined_llm):
                self.registry.register(tool)

    def _maybe_load_skills(self) -> None:
        if self._skills_dir is None or self.quarantined_llm is None:
            return
        if not self._skills_dir.is_dir():
            return
        # Local import keeps PyYAML optional for users who don't use skills.
        from capabledeputy.skills.loader import load_skill_directory

        load_skill_directory(
            self._skills_dir,
            self.registry,
            self.quarantined_llm,
            skip_on_duplicate=True,
        )

    async def startup(self) -> None:
        await self.store.initialize()
        await self.graph.load()
