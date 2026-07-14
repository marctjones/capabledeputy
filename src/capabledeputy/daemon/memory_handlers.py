"""RPC handlers for the labeled memory store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler
from capabledeputy.policy.fs_labeling import label_string_to_state
from capabledeputy.policy.labels import LabelState

# Bounded read for ingest — the runtime reads the source, never the planner.
_INGEST_MAX_BYTES = 256 * 1024


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

    async def memory_ingest_file(params: dict[str, Any]) -> dict[str, Any]:
        """Model-A ingest (#300/#301/#303): the RUNTIME reads a source file,
        labels it, and lands it in labeled memory — the planner is never
        involved, so the raw value never enters the model's context. When a
        session is given, the resolved label is raised onto the session BEFORE
        its next turn, so mode selection picks REFERENCE (raw readers hidden) and
        the planner can only route the value via `memory.handle`, never read it.

        Params: path (required), key (optional), session_id (optional),
        category (optional explicit `confidential.<category>` override),
        require_label (optional; fail closed if no confidential label resolves).
        """
        raw_path = str(params["path"])
        path = Path(raw_path).expanduser()
        if not path.is_file():
            return {"ok": False, "error": f"not a file: {raw_path}"}
        data = path.read_bytes()
        if len(data) > _INGEST_MAX_BYTES:
            return {"ok": False, "error": f"file exceeds {_INGEST_MAX_BYTES}-byte ingest cap"}
        # UTF-8 text only for now. A binary/PDF file would decode to lossy garbage
        # (useless value AND content-regex labeling can't match mojibake), so
        # refuse explicitly rather than silently store junk. PDF ingest via the
        # existing pdf-extract path is a tracked follow-up.
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "ok": False,
                "error": "non-text file: ingest currently supports UTF-8 text only "
                "(PDF/binary ingest is not yet implemented)",
            }

        # Labeling is done by the RUNTIME: an explicit operator category override,
        # else the path/content fs-label rules. NB (labeling-oracle boundary): a
        # file that matches no rule and has no explicit category resolves to NO
        # label — it is stored public and does NOT taint the session. Sensitive
        # files outside the shipped high-precision rules must be ingested with an
        # explicit `category`; use `require_label` to fail closed instead.
        explicit = params.get("category")
        if explicit:
            label = label_string_to_state(f"confidential.{explicit}")
        elif app._fs_labeler is not None:
            label = app._fs_labeler.labels_for(str(path), content=content)
        else:
            label = LabelState()

        if bool(params.get("require_label")) and not label.a:
            return {
                "ok": False,
                "error": "no confidential label resolved for this file; pass an explicit "
                "`category` (require_label was set, so ingest fails closed)",
            }

        key = str(params.get("key") or f"ingest/{path.name}")
        app.memory.write(key, content, label, trust_class="operator_note")

        session_tainted = False
        if params.get("session_id") and (label.a or label.b):
            await app.graph.add_tags(UUID(str(params["session_id"])), label)
            session_tainted = True

        return {
            "ok": True,
            "key": key,
            "categories": sorted(tag.category for tag in label.a),
            "tiers": sorted({tag.tier.value for tag in label.a}),
            "session_tainted": session_tainted,
            "bytes": len(content),
        }

    return {
        "memory.entries": memory_entries,
        "memory.policy": memory_policy,
        "memory.prune": memory_prune,
        "memory.compact_session": memory_compact_session,
        "memory.ingest_file": memory_ingest_file,
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
                "content_sha256": __import__("hashlib")
                .sha256(
                    content.encode("utf-8"),
                )
                .hexdigest(),
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
            f"{item['role']}[{item['turn_id']}]: {item['content_preview']}" for item in items
        ),
        "source_turns": items,
    }
