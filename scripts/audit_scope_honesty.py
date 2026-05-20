#!/usr/bin/env python3
"""T111 — Scope-honesty audit (SC-009).

Verify that no FR in the 003 spec addresses a *named deliberate
non-goal*. The non-goals (per spec.md):

  - Model bias / accuracy / evaluation (the policy oracle does not
    evaluate model quality)
  - Content safety (hate/violence/etc. — operator-curated, outside
    the labeling framework)
  - Lawful basis / consent / DSAR (jurisdictional questions live in
    operator policy, not in the engine)
  - Substrate security (provider impls live in spec 004; the
    labeling framework only specifies in-TCB labels + policy)

If any FR body mentions one of these terms, the audit raises so
the spec author can rephrase. Run in CI to keep the boundary clean
as the spec evolves.

Exit codes:
  0 — no scope-honesty violations
  1 — one or more violations found (prints the offending FRs)
  2 — usage / file-access error
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Named non-goal phrases. Each tuple is (phrase, category).
# Case-insensitive substring match. Phrases are deliberately specific
# to avoid false positives — e.g., "model" alone is too broad.
_FORBIDDEN_TERMS: tuple[tuple[str, str], ...] = (
    ("model bias", "non-goal: model bias"),
    ("model accuracy", "non-goal: model accuracy"),
    ("model evaluation", "non-goal: model evaluation"),
    ("content safety", "non-goal: content safety"),
    ("hate speech", "non-goal: content safety"),
    ("lawful basis", "non-goal: lawful basis"),
    ("consent management", "non-goal: consent"),
    ("dsar", "non-goal: data-subject access requests"),
    ("data subject access request", "non-goal: DSAR"),
    ("provider security", "non-goal: substrate security"),
    ("substrate security", "non-goal: substrate security"),
)

# A list of "allowed-context" phrases. If one of these appears in
# the same paragraph as a forbidden term, we treat it as a
# deliberate disavowal (the spec says "we are NOT addressing X"),
# not a violation.
_ALLOWED_DISAVOWAL_MARKERS: tuple[str, ...] = (
    "deliberate non-goal",
    "out of scope",
    "not in scope",
    "deferred",
    "outside the labeling framework",
)


_FR_PATTERN = re.compile(r"^\s*(FR-\d+):", re.MULTILINE)


def audit_spec(spec_path: Path) -> list[tuple[str, str, str]]:
    """Return a list of (fr_id, term_violated, snippet) tuples.
    Empty list ⇒ no violations."""
    text = spec_path.read_text(encoding="utf-8")
    fr_matches = list(_FR_PATTERN.finditer(text))
    violations: list[tuple[str, str, str]] = []

    for i, m in enumerate(fr_matches):
        fr_id = m.group(1)
        start = m.start()
        end = fr_matches[i + 1].start() if i + 1 < len(fr_matches) else len(text)
        body = text[start:end]
        lower_body = body.lower()
        if any(marker in lower_body for marker in _ALLOWED_DISAVOWAL_MARKERS):
            continue
        for term, category in _FORBIDDEN_TERMS:
            if term in lower_body:
                snippet = body[:200].replace("\n", " ").strip()
                violations.append((fr_id, category, snippet))
                break
    return violations


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print(f"usage: {argv[0]} [spec.md]", file=sys.stderr)
        return 2
    if len(argv) == 2:
        spec_path = Path(argv[1])
    else:
        repo_root = Path(__file__).resolve().parents[1]
        spec_path = repo_root / "specs/003-labeling-framework/spec.md"
    if not spec_path.is_file():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        return 2

    violations = audit_spec(spec_path)
    if not violations:
        print(f"OK: no scope-honesty violations in {spec_path}")
        return 0
    print(
        f"FAIL: {len(violations)} scope-honesty violation(s) in {spec_path}:",
        file=sys.stderr,
    )
    for fr_id, category, snippet in violations:
        print(f"  {fr_id} [{category}]: {snippet[:140]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
