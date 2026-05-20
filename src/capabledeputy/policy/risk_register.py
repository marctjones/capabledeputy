"""Risk Register (003 FR-015, FR-028).

A single in-repo JSON file at configs/risk_register.json (operator-
editable, human-declared, AI-read-only) holding entries
`{id, summary, framework_refs[]}`. Labels and decisions cite `id`;
the register itself cites external framework references (NIST CSF,
ISO 27001, OWASP, CIS, etc.) — every register id MUST cite >=1
external ref (SC-001 lint, scripts/lint_risk_register.py).

Loaded once at daemon startup; held in memory. Lookup/exists are O(1)
dict access. The orphan audit is a static check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


class RiskRegisterError(RuntimeError):
    """The register file is missing, unparseable, or malformed.
    Fail-closed per Constitution VI — daemon refuses to start."""


@dataclass(frozen=True)
class RiskRegisterEntry:
    id: str
    summary: str
    framework_refs: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RiskRegister:
    """In-memory view of configs/risk_register.json. Construct via
    `load()` — the constructor is unguarded for testability."""

    entries: dict[str, RiskRegisterEntry]

    def get(self, register_id: str) -> RiskRegisterEntry:
        try:
            return self.entries[register_id]
        except KeyError as e:
            raise RiskRegisterError(f"unknown risk-register id: {register_id!r}") from e

    def exists(self, register_id: str) -> bool:
        return register_id in self.entries

    def audit_orphans(self) -> list[str]:
        """Return ids with empty framework_refs (SC-001 violation).
        Surfaced by scripts/lint_risk_register.py at CI time; usable
        from runtime audits too (FR-028)."""
        return sorted(rid for rid, entry in self.entries.items() if not entry.framework_refs)

    def __len__(self) -> int:
        return len(self.entries)


def load(path: Path) -> RiskRegister:
    """Load configs/risk_register.json. Fail-closed on missing file,
    malformed JSON, or malformed entry shape."""
    if not path.is_file():
        raise RiskRegisterError(f"risk register missing: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RiskRegisterError(f"risk register unparseable: {path} — {e}") from e

    if not isinstance(raw, dict):
        raise RiskRegisterError(f"risk register root must be an object: {path}")
    raw_entries = raw.get("entries", [])
    if not isinstance(raw_entries, list):
        raise RiskRegisterError(f"risk register 'entries' must be a list: {path}")

    parsed: dict[str, RiskRegisterEntry] = {}
    for i, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            raise RiskRegisterError(f"risk register entry {i} is not an object: {path}")
        try:
            entry_id = item["id"]
            summary = item["summary"]
        except KeyError as e:
            raise RiskRegisterError(
                f"risk register entry {i} missing required field: {e.args[0]!r}",
            ) from e
        refs = item.get("framework_refs", [])
        if not isinstance(refs, list):
            raise RiskRegisterError(
                f"risk register entry {entry_id!r}: framework_refs must be a list",
            )
        if entry_id in parsed:
            raise RiskRegisterError(f"risk register entry {entry_id!r} duplicated")
        parsed[entry_id] = RiskRegisterEntry(
            id=str(entry_id),
            summary=str(summary),
            framework_refs=tuple(str(r) for r in refs),
        )
    return RiskRegister(entries=parsed)
