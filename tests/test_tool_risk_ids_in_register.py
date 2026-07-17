"""Invariant: every tool's declared `risk_ids` cites a real risk-register
entry (FR-015). Guards the rule-5 gap — `ToolRegistry.register()` enforces
rules 1-4 but not the register-membership of risk_ids — so this CI check
catches a typo'd / orphan risk-id that would otherwise register silently.

Source-scan (like the SC-001 lint) so it needs no tool construction.
"""

from __future__ import annotations

import re
from pathlib import Path

from capabledeputy.policy.risk_register import load as load_risk_register

_RISK_IDS = re.compile(r"risk_ids=\(([^)]*)\)")
_ID = re.compile(r'"([^"]+)"')


def test_all_tool_risk_ids_exist_in_register() -> None:
    root = Path(__file__).resolve().parent.parent
    # #384 — format-agnostic loader (reads risk_register.yaml, or a legacy .json).
    register = set(load_risk_register(root / "configs/risk_register.yaml").entries)
    orphans: list[tuple[str, str]] = []
    for path in (root / "src/capabledeputy").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for block in _RISK_IDS.finditer(text):
            for rid in _ID.findall(block.group(1)):
                if rid not in register:
                    orphans.append((str(path.relative_to(root)), rid))
    assert not orphans, f"tools cite risk_ids absent from configs/risk_register.yaml: {orphans}"
