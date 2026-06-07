"""RPC handlers for the labeled memory store."""

from __future__ import annotations

from typing import Any

from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler


def make_memory_handlers(app: App) -> dict[str, Handler]:
    async def memory_entries(params: dict[str, Any]) -> dict[str, Any]:
        from capabledeputy.policy.labels import legacy_labels_present

        entries = []
        for key in app.memory.keys():  # noqa: SIM118 (custom list API)
            # Convert LabelState back to legacy label strings for metadata
            # display (matched by category / provenance level).
            label_state = app.memory.label_state_of(key)
            entries.append(
                {
                    "key": key,
                    "labels": legacy_labels_present(label_state),
                }
            )
        return {"entries": entries}

    return {"memory.entries": memory_entries}
