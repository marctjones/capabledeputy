"""T120 tripwire (FR-014): no module under src/capabledeputy/ imports
or implements an apply path keyed on 'suggestion', 'pending_ratification',
or 'unratified_apply'.

This is a STRUCTURAL invariant — it ensures the spec's "unratified
suggestions ⇒ zero effect" rule holds in 003 by *absence* of any
plumbing. Once the suggest/ratify channel ships in a follow-on spec,
this tripwire MUST be replaced with a real behavioral test ("unratified
⇒ 0 effect"); leaving the absence check in place after the channel
exists would silently hide regressions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Forbidden tokens. Each appears anywhere as a substring in any .py
# file under src/capabledeputy/ would fail the tripwire. Comments and
# docstrings are excluded by the inner pre-filter that strips them
# before searching — references in a docstring should be free.
_FORBIDDEN = (
    "unratified_apply",
    "pending_ratification",
)

# `suggestion` is too common a substring to ban outright (it appears
# legitimately in many places); the proper check is for an *apply path*
# keyed on it — i.e., the function-name pattern apply_suggestion(...) or
# unratified_apply(...). We only catch the former with regex; the
# latter is in _FORBIDDEN above as a direct substring.
_SUGGESTION_APPLY_PATTERN = re.compile(r"\bapply_suggestion\b")


def _src_files() -> list[Path]:
    src = Path("src/capabledeputy")
    return sorted(src.rglob("*.py"))


def _strip_comments_and_docstrings(text: str) -> str:
    """Crude but sufficient: drop lines that start with # and triple-
    quoted regions. False positives on tripwire are worse than missing
    legit references, so we err on the side of stripping aggressively."""
    out: list[str] = []
    in_docstring = False
    for line in text.splitlines():
        stripped = line.strip()
        if in_docstring:
            if '"""' in stripped or "'''" in stripped:
                in_docstring = False
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            # Could be a one-line docstring or the start of a block.
            count_triple = stripped.count('"""') + stripped.count("'''")
            if count_triple >= 2:
                continue  # one-line docstring, skip
            in_docstring = True
            continue
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


@pytest.mark.invariant
def test_no_unratified_apply_path() -> None:
    """Tripwire: forbidden tokens MUST NOT appear in non-comment code."""
    offenders: list[tuple[Path, str]] = []
    for path in _src_files():
        text = path.read_text(encoding="utf-8")
        code = _strip_comments_and_docstrings(text)
        for token in _FORBIDDEN:
            if token in code:
                offenders.append((path, token))
        if _SUGGESTION_APPLY_PATTERN.search(code):
            offenders.append((path, "apply_suggestion(...)"))
    assert not offenders, (
        f"FR-014 tripwire fired — found unratified-apply plumbing: {offenders}. "
        "If the suggest/ratify channel is now real, replace this tripwire with "
        "a behavioral test ('unratified ⇒ 0 effect')."
    )
