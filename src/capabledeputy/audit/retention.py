"""Read-only inventory for legacy audit-output retention (#454).

The audit log is a broadly inspectable control plane.  This scanner answers
whether older ``tool.returned`` events may have copied a tool result into that
plane, without returning the copied value.  It deliberately never rewrites
history: remediation needs a separate, operator-approved retention decision.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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


@dataclass(frozen=True)
class RetentionRemediation:
    """Evidence for a non-destructive legacy-output sanitization.

    The source audit is never changed.  The operator chooses whether to cut
    over to the separately created sanitized copy after reviewing this
    manifest and the source's own audit-chain verification result.
    """

    source: Path
    sanitized: Path
    manifest: Path
    operator: str
    source_sha256: str
    sanitized_sha256: str
    total_events: int
    sanitized_tool_returned_events: int
    candidate_audit_ids_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "capdep.audit-retention-remediation.v1",
            "source": str(self.source),
            "sanitized": str(self.sanitized),
            "manifest": str(self.manifest),
            "operator": self.operator,
            "source_sha256": self.source_sha256,
            "sanitized_sha256": self.sanitized_sha256,
            "total_events": self.total_events,
            "sanitized_tool_returned_events": self.sanitized_tool_returned_events,
            "candidate_audit_ids_digest": self.candidate_audit_ids_digest,
            "content_rendered": False,
        }


def sanitize_legacy_tool_outputs(
    source: Path,
    sanitized: Path,
    *,
    operator: str,
) -> RetentionRemediation:
    """Create a separately auditable, redacted replacement for a legacy log.

    This is deliberately a *copy* operation: no source bytes are modified or
    removed, and an existing destination is refused.  A raw legacy payload is
    read only long enough to compute a stable digest and size, then replaced
    by metadata matching current ``tool.returned`` audit practice.  Every
    output line receives a fresh hash chain, while the sidecar manifest binds
    source and sanitized file hashes plus the (content-free) set of affected
    audit IDs.

    A malformed record is refused rather than silently omitted, because an
    operator must be able to account for every event before a cutover.
    """
    if not operator.strip():
        raise ValueError("operator identity is required for retention remediation")
    if not source.is_file():
        raise ValueError(f"source audit log does not exist: {source}")
    if source.resolve() == sanitized.resolve():
        raise ValueError("sanitized destination must differ from the source audit log")
    if sanitized.exists():
        raise ValueError(f"sanitized destination already exists: {sanitized}")
    manifest = sanitized.with_name(f"{sanitized.name}.retention-manifest.json")
    if manifest.exists():
        raise ValueError(f"retention manifest already exists: {manifest}")

    source_bytes = source.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    candidate_ids: list[str] = []
    transformed: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(source_bytes.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"source audit has malformed record at line {line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"source audit has non-object record at line {line_number}")
        payload = record.get("payload")
        if record.get("event_type") == "tool.returned" and not (
            isinstance(payload, dict) and payload.get("output_redacted") is True
        ):
            audit_id = str(record.get("audit_id") or "<missing-audit-id>")
            candidate_ids.append(audit_id)
            raw_payload = payload if isinstance(payload, dict) else {"legacy_payload": payload}
            encoded_payload = json.dumps(
                raw_payload,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
            record = dict(record)
            record["payload"] = {
                "output_digest": hashlib.sha256(encoded_payload).hexdigest(),
                "output_type": type(raw_payload).__name__,
                "output_size_bytes": len(encoded_payload),
                "output_redacted": True,
                "legacy_output_removed": True,
                "legacy_payload_digest": hashlib.sha256(encoded_payload).hexdigest(),
            }
        # A newly generated chain is necessary because payload redaction
        # changes line bytes.  The manifest commits to the old and new files.
        record.pop("prev_hash", None)
        transformed.append(record)

    sanitized.parent.mkdir(parents=True, exist_ok=True)
    temp_sanitized = sanitized.with_name(f".{sanitized.name}.retention-partial")
    temp_manifest = manifest.with_name(f".{manifest.name}.retention-partial")
    if temp_sanitized.exists() or temp_manifest.exists():
        raise ValueError(
            "retention remediation temporary output already exists; inspect it before retrying"
        )
    previous_hash: str | None = None
    try:
        with temp_sanitized.open("x", encoding="utf-8") as output:
            for record in transformed:
                record["prev_hash"] = previous_hash
                line = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str) + "\n"
                line_bytes = line.encode("utf-8")
                output.write(line)
                previous_hash = hashlib.sha256(line_bytes).hexdigest()
            output.flush()
            os.fsync(output.fileno())
        sanitized_sha256 = hashlib.sha256(temp_sanitized.read_bytes()).hexdigest()
        candidate_digest = hashlib.sha256("\n".join(candidate_ids).encode("utf-8")).hexdigest()
        manifest_payload = {
            "schema": "capdep.audit-retention-remediation.v1",
            "created_at": datetime.now(UTC).isoformat(),
            "operator": operator,
            "source": str(source),
            "source_sha256": source_sha256,
            "sanitized": str(sanitized),
            "sanitized_sha256": sanitized_sha256,
            "total_events": len(transformed),
            "sanitized_tool_returned_events": len(candidate_ids),
            "candidate_audit_ids_digest": candidate_digest,
            "content_rendered": False,
            "source_modified": False,
            "cutover_performed": False,
        }
        with temp_manifest.open("x", encoding="utf-8") as output:
            json.dump(manifest_payload, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_sanitized, sanitized)
        os.replace(temp_manifest, manifest)
    except Exception:
        temp_sanitized.unlink(missing_ok=True)
        temp_manifest.unlink(missing_ok=True)
        raise

    return RetentionRemediation(
        source=source,
        sanitized=sanitized,
        manifest=manifest,
        operator=operator,
        source_sha256=source_sha256,
        sanitized_sha256=sanitized_sha256,
        total_events=len(transformed),
        sanitized_tool_returned_events=len(candidate_ids),
        candidate_audit_ids_digest=candidate_digest,
    )
