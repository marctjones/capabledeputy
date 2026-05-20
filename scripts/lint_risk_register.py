"""SC-001 lint: every label cites >=1 risk register id; every register
entry cites >=1 external framework_refs. Run from repo root:

    uv run python scripts/lint_risk_register.py

Exits 0 on clean, non-zero with a per-orphan diagnostic on violations.
Also runs a coverage-audit citation expander pass to normalize
slash-shortened FR citations like `(FR-032/036/038)` into the equivalent
comma-list `(FR-032, FR-036, FR-038)` so substring-matching coverage
tools (e.g. /speckit-analyze) do not underreport. The expander only
warns; it does not rewrite files in this pass.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent


def _load_risk_register(root: Path) -> dict[str, dict]:
    path = root / "configs" / "risk_register.json"
    if not path.exists():
        print(f"FATAL: missing {path}", file=sys.stderr)
        sys.exit(2)
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    return {e["id"]: e for e in entries if isinstance(e, dict) and "id" in e}


def _load_labels(root: Path) -> list[dict]:
    path = root / "configs" / "labels.yaml"
    if not path.exists():
        print(f"FATAL: missing {path}", file=sys.stderr)
        sys.exit(2)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[dict] = []
    for key in ("categories", "reversibility_labels", "mutability_labels"):
        items = data.get(key) or []
        for item in items:
            if isinstance(item, dict):
                out.append({**item, "_section": key})
    return out


def _check_register_external_refs(register: dict[str, dict]) -> list[str]:
    orphans = []
    for rid, entry in register.items():
        refs = entry.get("framework_refs") or []
        if not isinstance(refs, list) or len(refs) == 0:
            orphans.append(rid)
    return orphans


def _check_label_register_refs(
    labels: list[dict],
    register: dict[str, dict],
) -> list[tuple[str, str, str]]:
    """Return list of (section, label_id, reason) for any orphan label."""
    problems: list[tuple[str, str, str]] = []
    register_ids = set(register.keys())
    for label in labels:
        lid = label.get("id", "<no-id>")
        section = label.get("_section", "<unknown>")
        risk_ids = label.get("risk_ids") or []
        if not isinstance(risk_ids, list) or len(risk_ids) == 0:
            problems.append((section, lid, "cites zero risk_ids"))
            continue
        for rid in risk_ids:
            if rid not in register_ids:
                problems.append((section, lid, f"cites unknown risk_id={rid!r}"))
    return problems


_SLASH_CITATION = re.compile(r"\bFR-\d+(?:/\d+){1,}\b")


def _expand_slash_citations(root: Path) -> list[tuple[Path, int, str]]:
    """Find slash-shortened FR citations in specs/ markdown. Warning only."""
    findings: list[tuple[Path, int, str]] = []
    specs_dir = root / "specs"
    if not specs_dir.exists():
        return findings
    for md in specs_dir.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _SLASH_CITATION.finditer(line):
                findings.append((md, lineno, match.group(0)))
    return findings


def main() -> int:
    root = _repo_root()
    register = _load_risk_register(root)
    labels = _load_labels(root)

    register_orphans = _check_register_external_refs(register)
    label_problems = _check_label_register_refs(labels, register)
    slash_citations = _expand_slash_citations(root)

    n_errors = 0
    if register_orphans:
        n_errors += len(register_orphans)
        print(
            f"SC-001: {len(register_orphans)} register entries with zero framework_refs:",
            file=sys.stderr,
        )
        for rid in register_orphans:
            print(f"  - {rid}", file=sys.stderr)

    if label_problems:
        n_errors += len(label_problems)
        print(f"SC-001: {len(label_problems)} label citation problem(s):", file=sys.stderr)
        for section, lid, reason in label_problems:
            print(f"  - {section}/{lid}: {reason}", file=sys.stderr)

    if slash_citations:
        # Warning-only — coverage-audit hint per tasks.md notes.
        print(
            f"info: {len(slash_citations)} slash-shortened FR citation(s) in "
            "specs/ — coverage audits should expand these:",
            file=sys.stderr,
        )
        for path, lineno, text in slash_citations[:10]:
            print(f"  {path.relative_to(root)}:{lineno}  {text}", file=sys.stderr)
        if len(slash_citations) > 10:
            print(f"  ... and {len(slash_citations) - 10} more", file=sys.stderr)

    if n_errors == 0:
        n_register = len(register)
        n_labels = len(labels)
        print(
            f"SC-001 clean: {n_register} register entries, {n_labels} labels "
            "all cite each other correctly.",
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
