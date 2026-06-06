"""Cookbook P1.6 — audit log hash-chain verifier.

Walks the active audit.jsonl line by line and confirms each line's
`prev_hash` field equals the SHA-256 of the prior line's bytes. Any
deviation surfaces with the line number where the chain breaks —
that's where someone edited, inserted, deleted, or reordered an
event.

The verifier is tolerant of legacy lines (no `prev_hash` field):
those predate the chain and form a "pre-chain prefix." We report
the count and the index of the first chained line so the operator
can see exactly where the cryptographic guarantee begins.

By default the verifier walks the ACTIVE file. Pass
`include_rotated=True` (CLI: `capdep audit verify --include-rotated`)
to walk the rotated archives too — `_enumerate_chain_files` orders them
oldest → archive.1 → active and the walk threads `prev_hash` across the
rotation boundaries, so a tamper anywhere in the kept history is caught
(reported as "file F line L").

This module is structured so it can run standalone (no daemon
needed): point it at any audit.jsonl and ask for a yes/no answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from capabledeputy.audit.writer import _hash_line


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of an audit chain walk. `ok` is the headline; the
    other fields detail what was checked and where any break sat.

    `tampered_at_line` is 1-indexed for human consumption (matching
    text editors / `head -n N`). 0 means no break detected.

    `n_chained` is the count of lines that had a `prev_hash` field
    AND verified. `n_legacy_prefix` counts the leading lines that
    predate the chain. `n_lines` is the total non-empty lines.
    """

    ok: bool
    n_lines: int
    n_chained: int
    n_legacy_prefix: int
    tampered_at_line: int  # 1-indexed; 0 ⇒ no break
    reason: str
    path: Path
    # Roadmap v2 #2 — cross-file walk. When verify_audit_chain is
    # called with include_rotated=True, the walker steps through
    # archive files first (oldest → archive.1 → active) and
    # threads prev_hash across the boundaries. files_walked lists
    # them in walk order so the CLI can show "verified across
    # archive.3, archive.2, archive.1, active" and surface a
    # break-at-line as "file F line L" instead of just "line L".
    # When include_rotated=False (default), files_walked is just
    # the active file.
    files_walked: tuple[Path, ...] = ()
    tampered_at_file: Path | None = None


def verify_audit_chain(
    path: Path,
    *,
    include_rotated: bool = False,
) -> VerifyResult:
    """Walk `path` (and optionally its rotated archives) and verify
    every chained line. Returns a structured result rather than
    raising — the CLI surfaces the fields directly.

    Algorithm:
      1. Build the file list: just `path` by default; with
         include_rotated, prepend rotated archives in chronological
         order (oldest first: path.N → ... → path.2 → path.1 →
         path).
      2. For each file, read as bytes; for each non-empty line:
         a. Parse JSON; if it fails, declare tamper at this line.
         b. If `prev_hash` is absent, count as legacy prefix
            (allowed as long as we haven't seen any chained line
            yet — once chain has begun, missing prev_hash is
            tamper).
         c. If `prev_hash` is present, compare to the SHA-256 of
            the previous CHAINED line's bytes (which may live in
            the PREVIOUS file when crossing a rotation boundary).
            Mismatch ⇒ tamper.
      3. Return aggregate counts + first-break index.

    A fresh file (no events) is OK with n_lines=0. With
    include_rotated, an active file that doesn't exist while
    rotated archives do is also OK (the chain ended at the last
    archived event).
    """
    files_to_walk = _enumerate_chain_files(path, include_rotated)

    if not files_to_walk or not any(p.is_file() for p in files_to_walk):
        return VerifyResult(
            ok=True,
            n_lines=0,
            n_chained=0,
            n_legacy_prefix=0,
            tampered_at_line=0,
            reason=f"no file at {path}",
            path=path,
            files_walked=tuple(files_to_walk),
        )

    n_legacy_prefix = 0
    n_chained = 0
    total_lines = 0
    prev_line_bytes: bytes | None = None
    chain_started = False
    for file_path in files_to_walk:
        if not file_path.is_file():
            continue
        raw = file_path.read_bytes()
        if not raw:
            continue
        lines = [line for line in raw.splitlines(keepends=True) if line.strip()]
        for idx, line in enumerate(lines, start=1):
            total_lines += 1
            try:
                d = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return VerifyResult(
                    ok=False,
                    n_lines=total_lines,
                    n_chained=n_chained,
                    n_legacy_prefix=n_legacy_prefix,
                    tampered_at_line=idx,
                    reason=(
                        f"line {idx} of {file_path.name} is not valid JSON "
                        "— file tampered or truncated"
                    ),
                    path=path,
                    files_walked=tuple(files_to_walk),
                    tampered_at_file=file_path,
                )
            if "prev_hash" not in d:
                if chain_started:
                    return VerifyResult(
                        ok=False,
                        n_lines=total_lines,
                        n_chained=n_chained,
                        n_legacy_prefix=n_legacy_prefix,
                        tampered_at_line=idx,
                        reason=(
                            f"line {idx} of {file_path.name} is missing "
                            "prev_hash but the chain began earlier — event "
                            "removed or reverted to legacy form"
                        ),
                        path=path,
                        files_walked=tuple(files_to_walk),
                        tampered_at_file=file_path,
                    )
                n_legacy_prefix += 1
                prev_line_bytes = line
                continue
            # Chained line.
            claimed = d["prev_hash"]
            if prev_line_bytes is None:
                # First chained line of the entire walk — `claimed`
                # may be None (fresh file) OR a hash carried over
                # from an EVEN OLDER archive we're not verifying.
                # Accept and start tracking.
                chain_started = True
                n_chained += 1
                prev_line_bytes = line
                continue
            actual = _hash_line(prev_line_bytes)
            if claimed != actual:
                return VerifyResult(
                    ok=False,
                    n_lines=total_lines,
                    n_chained=n_chained,
                    n_legacy_prefix=n_legacy_prefix,
                    tampered_at_line=idx,
                    reason=(
                        f"line {idx} of {file_path.name} prev_hash "
                        f"{claimed[:16]}… does not match actual hash of "
                        f"the prior line ({actual[:16]}…) — file tampered"
                    ),
                    path=path,
                    files_walked=tuple(files_to_walk),
                    tampered_at_file=file_path,
                )
            chain_started = True
            n_chained += 1
            prev_line_bytes = line
    files_with_content = [p for p in files_to_walk if p.is_file() and p.stat().st_size]
    reason = (
        f"chain verified: {n_chained} chained line(s)"
        + (f", {n_legacy_prefix} legacy prefix" if n_legacy_prefix else "")
        + (f" across {len(files_with_content)} file(s)" if len(files_with_content) > 1 else "")
    )
    return VerifyResult(
        ok=True,
        n_lines=total_lines,
        n_chained=n_chained,
        n_legacy_prefix=n_legacy_prefix,
        tampered_at_line=0,
        reason=reason,
        path=path,
        files_walked=tuple(files_to_walk),
    )


def _enumerate_chain_files(
    active_path: Path,
    include_rotated: bool,
) -> list[Path]:
    """Build the walk-order file list for verification.

    Without rotation: just `[active_path]`.
    With rotation: rotated archives are at `<active>.<N>` per the
    writer's `_rotated_path` convention. Walk order is
    chronological — oldest archive first → ... → archive.1 →
    active. Probes archive indices upward from 1 until a missing
    index is found (typical max_rotated=3 means archives 1..3 may
    exist). A gap (e.g. archive.1 missing but archive.2 present)
    is possible if the operator manually removed a file; we treat
    the gap as the end of the chain to avoid silently skipping
    archived events.
    """
    if not include_rotated:
        return [active_path]
    archives: list[Path] = []
    n = 1
    while True:
        archive = active_path.with_suffix(active_path.suffix + f".{n}")
        if not archive.is_file():
            break
        archives.append(archive)
        n += 1
    # archives[0] is the YOUNGEST archive (path.1). The chain walks
    # oldest → newest → active, so reverse before prepending.
    archives.reverse()
    return [*archives, active_path]
