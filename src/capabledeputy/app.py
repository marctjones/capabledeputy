"""Top-level App: composes audit, store, graph, tool registry, dispatcher, LLM."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from capabledeputy.approval.queue import ApprovalQueue
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.llm.client import LLMClient

if TYPE_CHECKING:
    from capabledeputy.llm.pool import ModelPool
from capabledeputy.onguard import OnguardStore
from capabledeputy.paths import default_audit_log_path, default_state_db_path
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.purposes import Purposes
from capabledeputy.resources.static import StaticResourcePublisher
from capabledeputy.session.coordination import SessionCoordinator, WorkstreamCoordinator
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.store import SessionStore
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.native.calendar import CalendarStore, make_calendar_tools
from capabledeputy.tools.native.chart import make_chart_tools
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
from capabledeputy.upstream.admission_store import McpAdmissionStore


class App:
    def __init__(
        self,
        state_db_path: Path | None = None,
        audit_log_path: Path | None = None,
        llm_client: LLMClient | None = None,
        quarantined_llm: LLMClient | None = None,
        model_pool: ModelPool | None = None,
        skills_dir: Path | None = None,
        enable_policy_preview: bool = True,
        policy_context: PolicyContext | None = None,
        purposes: Purposes | None = None,
        resources: StaticResourcePublisher | None = None,
        fs_labeler: Any = None,
    ) -> None:
        # Issue #5 — dynamic filesystem labeling. When provided (loaded
        # from configs/fs_label_rules.yaml), fs reads attach Axis-A
        # category labels so local-file data participates in IFC.
        self._fs_labeler = fs_labeler
        resolved_state_db_path = state_db_path or default_state_db_path()
        self.audit = AuditWriter(audit_log_path or default_audit_log_path())
        self.store = SessionStore(resolved_state_db_path)
        self.onguard = OnguardStore(resolved_state_db_path)
        self.mcp_admissions = McpAdmissionStore(resolved_state_db_path)
        from capabledeputy.llm.pool import ModelPool as _ModelPool

        self.model_pool: _ModelPool | None = model_pool
        if model_pool is not None:
            _planner = model_pool.default_planner_client()
            _quarantined_resolved: LLMClient | None = model_pool.extractor_client()
        else:
            _planner = llm_client
            # Resolve quarantined LLM first so we can signal availability
            # to SessionGraph. Falls back to the main llm_client when no
            # separate quarantined client was provided — the same model
            # plays both roles (a single-LLM deployment).
            _quarantined_resolved = quarantined_llm or llm_client
        # 003 runtime activation — SessionGraph receives the Purposes
        # registry so spawn/grant/delegate enforce FR-009 admissibility.
        # The `quarantined_available` flag enables the FR-047-style
        # fail-closed check at spawn for pattern_2_dual_llm purposes.
        self.graph = SessionGraph(
            audit=self.audit,
            store=self.store,
            purposes=purposes,
            quarantined_available=_quarantined_resolved is not None,
        )
        self.memory = LabeledMemoryStore(resolved_state_db_path)
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
        self.llm_client: LLMClient | None = _planner
        self.quarantined_llm: LLMClient | None = _quarantined_resolved
        # Wired post-construction by the daemon lifecycle (and by tests): the
        # running Daemon and the upstream MCP manager. Declared here (untyped to
        # avoid import cycles) so assignments type-check instead of needing
        # per-site `# type: ignore` / `cast(Any, app)`.
        self.daemon_server: Any = None
        self.upstream_manager: Any = None
        # Issue #23 — per-session cancellation flags. session.send sets
        # the entry to False at turn start; session.cancel flips it
        # True; the agent loop polls between iterations and yields
        # TurnInterrupted(reason="user_cancelled") when it sees True.
        # Dict-of-bools is sufficient — we don't need anyio.Event since
        # we only care about a tripwire that's checked, not awaited.
        self.cancellation_flags: dict[UUID, bool] = {}
        self.session_coordinator = SessionCoordinator()
        self.workstreams = WorkstreamCoordinator()
        from capabledeputy.daemon.turn_lifecycle import TurnLifecycleManager

        self.turns = TurnLifecycleManager(self)
        self._skills_dir = skills_dir
        self.skill_load_report: Any = None
        self._enable_policy_preview = enable_policy_preview
        # Background devbox idle-reaper task, started by `startup()`
        # when a PodmanDevbox is wired. Held here so `shutdown()` (and
        # tests) can cancel it cleanly. None when no devbox manager is
        # wired or before startup.
        self._devbox_reaper_task: Any = None
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
        for tool in make_fs_tools(self._fs_labeler):
            self.registry.register(tool)
        for tool in make_chart_tools():
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
        # Persistent dev containers — same Podman provider, long-lived
        # per-(session, spec) lifetime. Tool list is empty when no
        # PodmanDevbox is wired on the policy context (no provider →
        # no tools, mirroring the sandbox.run pattern).
        from capabledeputy.tools.native.devbox import make_devbox_tools

        for tool in make_devbox_tools(self.policy_context):
            self.registry.register(tool)
        if self.quarantined_llm is not None:
            for tool in make_extract_tools(self.memory, self.quarantined_llm, self.inbox):
                self.registry.register(tool)

    def _maybe_load_skills(self) -> None:
        if self._skills_dir is None:
            return
        if not self._skills_dir.is_dir():
            return
        # Local import keeps PyYAML optional for users who don't use skills.
        from capabledeputy.skills.loader import load_skill_directory_report

        sandbox_actuator = (
            self.policy_context.sandbox_actuator if self.policy_context is not None else None
        )
        self.skill_load_report = load_skill_directory_report(
            self._skills_dir,
            self.registry,
            self.quarantined_llm,
            skip_on_duplicate=True,
            sandbox_actuator=sandbox_actuator,
            audit=self.audit,
        )

    async def startup(self) -> None:
        await self.store.initialize()
        await self.onguard.initialize()
        await self.mcp_admissions.initialize()
        await self.graph.load()
        self._maybe_start_devbox_reaper()

    def _maybe_start_devbox_reaper(self) -> None:
        """Spawn the periodic devbox idle-reaper if a PodmanDevbox is
        wired. Off by default for installs without Podman.

        Cadence + threshold are env-tunable:
          CAPDEP_DEVBOX_REAP_INTERVAL_SECONDS — how often to wake
            up and scan (default 300 = 5 minutes).
          CAPDEP_DEVBOX_IDLE_SECONDS — a devbox is "idle" when its
            last_exec_at is older than this (default 3600 = 1 hour).
          CAPDEP_DEVBOX_IDLE_REAPER — set to "off" to disable the
            background reaper entirely (operator still has
            `capdep maintenance containers --apply`).
        """
        import os
        import sys

        if self.policy_context is None or self.policy_context.devbox_manager is None:
            return
        if os.environ.get("CAPDEP_DEVBOX_IDLE_REAPER", "").lower() in {
            "off",
            "false",
            "0",
            "no",
        }:
            print(
                "[devbox] idle reaper disabled via CAPDEP_DEVBOX_IDLE_REAPER",
                file=sys.stderr,
            )
            return
        interval = int(
            os.environ.get("CAPDEP_DEVBOX_REAP_INTERVAL_SECONDS", "300"),
        )
        idle = int(os.environ.get("CAPDEP_DEVBOX_IDLE_SECONDS", "3600"))
        manager = self.policy_context.devbox_manager
        print(
            f"[devbox] idle reaper started: scan every {interval}s, reap containers idle > {idle}s",
            file=sys.stderr,
        )

        async def _reaper_loop() -> None:
            import asyncio

            try:
                while True:
                    await asyncio.sleep(interval)
                    try:
                        reaped = manager.reap_idle(idle_seconds=idle)
                    except Exception as e:
                        print(
                            f"[devbox] reaper error (will retry): {e}",
                            file=sys.stderr,
                        )
                        continue
                    if reaped:
                        names = [f"{s}/{spec}" for s, spec in reaped]
                        print(
                            f"[devbox] reaped {len(reaped)} idle container(s): {', '.join(names)}",
                            file=sys.stderr,
                        )
            except asyncio.CancelledError:
                return

        import asyncio

        self._devbox_reaper_task = asyncio.create_task(
            _reaper_loop(),
            name="devbox-idle-reaper",
        )

    async def shutdown(self) -> None:
        """Cancel background tasks + tear down live devboxes on
        daemon shutdown. Idempotent.

        Roadmap v2 #1 — devbox teardown. Without this, live
        containers leak past daemon death and the idle reaper only
        catches them after the next process picks up. We walk every
        live (session, spec) and call stop_session so the operator
        running `capdep daemon stop` sees a clean state.
        Workspaces are preserved (the operator's work survives);
        purging is operator-explicit via the maintenance CLI."""
        if self._devbox_reaper_task is not None:
            import asyncio
            import contextlib

            self._devbox_reaper_task.cancel()
            # CancelledError is BaseException, not Exception — needs
            # explicit suppression.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._devbox_reaper_task
            self._devbox_reaper_task = None

        # Devbox teardown — best-effort, never raises into the
        # daemon's shutdown path. A failed teardown leaves the
        # workspace intact for the operator to clean up via
        # `capdep maintenance containers --apply`.
        if self.policy_context is not None and self.policy_context.devbox_manager is not None:
            import contextlib
            import sys

            manager = self.policy_context.devbox_manager
            # Collect distinct session ids without acquiring the
            # manager's lock for an extended time: snapshot the
            # keys, release, then iterate.
            session_ids = {sid for sid, _ in list(manager._live.keys())}
            total = 0
            for sid in session_ids:
                with contextlib.suppress(Exception):
                    total += manager.stop_session(sid)
            if total:
                print(
                    f"[devbox] shutdown reaped {total} live container(s)",
                    file=sys.stderr,
                )
