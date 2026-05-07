"""RPC handlers for the labeled memory store."""

from __future__ import annotations

from typing import Any

from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler


def make_memory_handlers(app: App) -> dict[str, Handler]:
    async def memory_entries(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "entries": [
                {
                    "key": key,
                    "labels": sorted(label.value for label in app.memory.labels_of(key)),
                }
                for key in app.memory.keys()  # noqa: SIM118
            ],
        }

    return {"memory.entries": memory_entries}
