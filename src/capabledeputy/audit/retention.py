"""Read-only inventory for legacy audit-output retention (#454).

The audit log is a broadly inspectable control plane.  This scanner answers
whether older ``tool.returned`` events may have copied a tool result into that
plane, without returning the copied value.  It deliberately never rewrites
history: remediation needs a separate, operator-approved retention decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LegacyToolOutputInventory:
    """Content-free classification of ``tool.returned`` audit records."""

    path: Path
    total_events: int
    tool_returned_events: int
    redacted_tool_returned_events: int
    legacy_output_candidates: tuple[str, ...]
    malformed_lines: int

    @property
    def remediation_required(self) -> bool:
        return bool(self.legacy_output_candidates or self.malformed_lines)

    def to_dict(self) -> dict[str, object]:
        # Audit IDs are operational metadata too.  A count establishes whether
        # action is needed; a small sample lets an operator locate the period
        # without turning a routine inventory into a bulk export.
        sample = self.legacy_output_candidates[:20]
        return {
            "path": str(self.path),
            "total_events": self.total_events,
            "tool_returned_events": self.tool_returned_events,
            "redacted_tool_returned_events": self.redacted_tool_returned_events,
            "legacy_output_candidate_count": len(self.legacy_output_candidates),
            "legacy_output_candidate_sample": list(sample),
            "legacy_output_candidates_truncated": len(sample) < len(self.legacy_output_candidates),
            "malformed_lines": self.malformed_lines,
            "remediation_required": self.remediation_required,
            "content_rendered": False,
        }


def inventory_legacy_tool_outputs(path: Path) -> LegacyToolOutputInventory:
    """Classify possible pre-redaction records without exposing their content.

    A current event is recognizable solely by its ``output_redacted: true``
    commitment.  Any older ``tool.returned`` event without that exact marker
    is a conservative remediation candidate.  Audit IDs identify records to
    an operator while avoiding output, tool arguments, and payload rendering.
    """
    if not path.exists():
        return LegacyToolOutputInventory(path, 0, 0, 0, (), 0)

    total = tool_returned = redacted = malformed = 0
    candidates: list[str] = []
    with path.open(encoding="utf-8") as source:
        for raw_line in source:
            if not raw_line.strip():
                continue
            total += 1
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(event, dict) or event.get("event_type") != "tool.returned":
                continue
            tool_returned += 1
            payload = event.get("payload")
            if isinstance(payload, dict) and payload.get("output_redacted") is True:
                redacted += 1
                continue
            audit_id = event.get("audit_id")
            candidates.append(str(audit_id) if audit_id is not None else "<missing-audit-id>")

    return LegacyToolOutputInventory(
        path=path,
        total_events=total,
        tool_returned_events=tool_returned,
        redacted_tool_returned_events=redacted,
        legacy_output_candidates=tuple(candidates),
        malformed_lines=malformed,
    )
