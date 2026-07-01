"""RPC handlers for the labeled memory store."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler


def make_memory_handlers(app: App) -> dict[str, Handler]:
    async def memory_entries(params: dict[str, Any]) -> dict[str, Any]:
        include_values = bool(params.get("include_values", False))
        return {"entries": app.memory.entries(include_values=include_values)}

    async def memory_policy(params: dict[str, Any]) -> dict[str, Any]:
        entries = app.memory.entries(include_values=False)
        return {
            "durable": app.memory.durable,
            "path": app.memory.snapshot()["path"],
            "entry_count": len(entries),
            "trust_classes": app.memory.snapshot().get("trust_classes", {}),
            "retention_policy": {
                "default_trust_class": "session",
                "prunable_trust_classes": [
                    "session",
                    "derived_summary",
                    "operator_note",
                ],
                "destructive_prune_requires_apply": True,
            },
        }

    async def memory_prune(params: dict[str, Any]) -> dict[str, Any]:
        return app.memory.prune(
            older_than_days=(
                int(params["older_than_days"])
                if params.get("older_than_days") is not None
                else None
            ),
            trust_class=(str(params["trust_class"]) if params.get("trust_class") else None),
            dry_run=not bool(params.get("apply", False)),
        )

    async def memory_compact_session(params: dict[str, Any]) -> dict[str, Any]:
        session_id = UUID(str(params["session_id"]))
        session = app.graph.get(session_id)
        keep_last = max(0, int(params.get("keep_last") or 8))
        history = list(session.history)
        compacted = history[:-keep_last] if keep_last else history
        if not compacted:
            return {
                "compacted": False,
                "reason": "nothing older than keep_last",
                "session_id": str(session_id),
            }
        summary = _summary_artifact(session, compacted, keep_last=keep_last)
        key = str(
            params.get("key")
            or f"compaction/session/{session_id}/{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        )
        app.memory.write(
            key,
            summary,
            session.label_state,
            trust_class="derived_summary",
        )
        return {
            "compacted": True,
            "session_id": str(session_id),
            "key": key,
            "artifact": summary,
            "labels": app.memory.entries(include_values=False)[-1].get("labels", []),
        }

    return {
        "memory.entries": memory_entries,
        "memory.policy": memory_policy,
        "memory.prune": memory_prune,
        "memory.compact_session": memory_compact_session,
    }


def _summary_artifact(session: Any, turns: list[Any], *, keep_last: int) -> dict[str, Any]:
    items = []
    for turn in turns:
        content = str(getattr(turn, "content", ""))
        items.append(
            {
                "turn_id": getattr(turn, "turn_id", None),
                "role": getattr(turn, "role", ""),
                "content_preview": content[:500],
                "content_sha256": __import__("hashlib").sha256(
                    content.encode("utf-8"),
                ).hexdigest(),
            },
        )
    return {
        "artifact_type": "capdep.compaction_summary.v1",
        "session_id": str(session.id),
        "created_at": datetime.now(UTC).isoformat(),
        "source": {
            "history_turns_compacted": len(items),
            "history_turns_retained": keep_last,
            "session_updated_at": session.updated_at.isoformat(),
        },
        "provenance": {
            "labels": session.label_state.to_dict(),
            "purpose_handle": session.purpose_handle,
            "origin": session.origin.to_dict(),
        },
        "summary": "\n".join(
            f"{item['role']}[{item['turn_id']}]: {item['content_preview']}"
            for item in items
        ),
        "source_turns": items,
    }
