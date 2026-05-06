"""Top-level App: composes audit log, session store, and session graph.

The App is what the daemon actually serves. Tests can construct it
with custom paths; production paths come from `paths.default_*`.
"""

from __future__ import annotations

from pathlib import Path

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.paths import default_audit_log_path, default_state_db_path
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.store import SessionStore


class App:
    def __init__(
        self,
        state_db_path: Path | None = None,
        audit_log_path: Path | None = None,
    ) -> None:
        self.audit = AuditWriter(audit_log_path or default_audit_log_path())
        self.store = SessionStore(state_db_path or default_state_db_path())
        self.graph = SessionGraph(audit=self.audit, store=self.store)

    async def startup(self) -> None:
        await self.store.initialize()
        await self.graph.load()
