"""Top-level App: composes audit, store, graph, tool registry, dispatcher, LLM."""

from __future__ import annotations

from pathlib import Path

from capabledeputy.approval.queue import ApprovalQueue
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.llm.client import LLMClient
from capabledeputy.paths import default_audit_log_path, default_state_db_path
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.store import SessionStore
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.native.email import EmailOutbox, make_email_tools
from capabledeputy.tools.native.extract import make_extract_tools
from capabledeputy.tools.native.memory import LabeledMemoryStore, make_memory_tools
from capabledeputy.tools.native.purchase import PurchaseQueue, make_purchase_tools
from capabledeputy.tools.registry import ToolRegistry


class App:
    def __init__(
        self,
        state_db_path: Path | None = None,
        audit_log_path: Path | None = None,
        llm_client: LLMClient | None = None,
        quarantined_llm: LLMClient | None = None,
    ) -> None:
        self.audit = AuditWriter(audit_log_path or default_audit_log_path())
        self.store = SessionStore(state_db_path or default_state_db_path())
        self.graph = SessionGraph(audit=self.audit, store=self.store)
        self.memory = LabeledMemoryStore()
        self.purchase_queue = PurchaseQueue()
        self.email_outbox = EmailOutbox()
        self.approval_queue = ApprovalQueue(audit=self.audit)
        self.registry = ToolRegistry()
        self.tool_client = LabeledToolClient(self.registry, self.graph, self.audit)
        self.llm_client: LLMClient | None = llm_client
        self.quarantined_llm: LLMClient | None = quarantined_llm or llm_client
        self._register_native_tools()

    def _register_native_tools(self) -> None:
        for tool in make_memory_tools(self.memory):
            self.registry.register(tool)
        for tool in make_purchase_tools(self.purchase_queue):
            self.registry.register(tool)
        for tool in make_email_tools(self.email_outbox):
            self.registry.register(tool)
        if self.quarantined_llm is not None:
            for tool in make_extract_tools(self.memory, self.quarantined_llm):
                self.registry.register(tool)

    async def startup(self) -> None:
        await self.store.initialize()
        await self.graph.load()
