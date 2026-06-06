"""RPC handlers for the labeled memory store."""

from __future__ import annotations

from typing import Any

from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler


def make_memory_handlers(app: App) -> dict[str, Handler]:
    async def memory_entries(params: dict[str, Any]) -> dict[str, Any]:
        from capabledeputy.policy.labels import _LEGACY_LABEL_STRINGS_TO_TAGS

        entries = []
        for key in app.memory.keys():  # noqa: SIM118 (custom list API)
            label_state = app.memory.label_state_of(key)
            # Convert LabelState back to label strings for metadata display.
            # Check which legacy labels match this state (a label matches if
            # its tags are a subset of the stored state).
            label_strings = []
            for label_str, tags in _LEGACY_LABEL_STRINGS_TO_TAGS.items():
                # Egress labels map to empty LabelState, skip them for display
                if not tags.a and not tags.b:
                    continue
                # Check if this label's tags are all present in the state
                if all(cat in label_state.a for cat in tags.a) and all(
                    prov in label_state.b for prov in tags.b
                ):
                    label_strings.append(label_str)
            entries.append(
                {
                    "key": key,
                    "labels": sorted(label_strings),
                }
            )
        return {"entries": entries}

    return {"memory.entries": memory_entries}
