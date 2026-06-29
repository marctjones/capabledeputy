"""Emit structured image attachment events from tool outcomes."""

from __future__ import annotations

from typing import Any

from capabledeputy.mcp_server.media_results import iter_image_sources_in_value


def image_attachment_payloads_from_outcome(outcome: dict[str, Any]) -> list[dict[str, str]]:
    """Return wire payloads for ``image_attachment`` turn events."""
    attachments: list[dict[str, str]] = []
    seen: set[str] = set()

    for source, alt in iter_image_sources_in_value(outcome):
        normalized = source.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        attachments.append(
            {
                "path": normalized,
                "alt": alt or "image",
                "source": "tool_return",
            },
        )
    return attachments