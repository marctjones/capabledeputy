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

Today the verifier only walks the ACTIVE file. Cross-file
verification (chain spans rotation when archives are kept) is a
follow-up: the writer preserves the chain across rotation, but the
verifier currently treats each file's chain independently.

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


def verify_audit_chain(path: Path) -> VerifyResult:
    """Walk `path` and verify every chained line. Returns a
    structured result rather than raising — the CLI surfaces the
    fields directly.

    Algorithm:
      1. Read the file as bytes; split into lines preserving the
         newline (so the hash matches what the writer produced).
      2. For each non-empty line:
         a. Parse JSON; if it fails, declare tamper at this line.
         b. If `prev_hash` is absent, count as legacy prefix
            (allowed as long as we haven't seen any chained line
            yet — once chain has begun, missing prev_hash is
            tamper).
         c. If `prev_hash` is present, compare to the SHA-256 of
            the previous CHAINED line's bytes. Mismatch ⇒ tamper.
      3. Return aggregate counts + first-break index.

    A fresh file (no events) is OK with n_lines=0.
    """
    if not path.is_file():
        return VerifyResult(
            ok=True,
            n_lines=0,
            n_chained=0,
            n_legacy_prefix=0,
            tampered_at_line=0,
            reason=f"no file at {path}",
            path=path,
        )
    raw = path.read_bytes()
    if not raw:
        return VerifyResult(
            ok=True,
            n_lines=0,
            n_chained=0,
            n_legacy_prefix=0,
            tampered_at_line=0,
            reason="empty file",
            path=path,
        )
    lines = [line for line in raw.splitlines(keepends=True) if line.strip()]
    n_legacy_prefix = 0
    n_chained = 0
    prev_line_bytes: bytes | None = None
    chain_started = False
    for idx, line in enumerate(lines, start=1):
        try:
            d = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return VerifyResult(
                ok=False,
                n_lines=len(lines),
                n_chained=n_chained,
                n_legacy_prefix=n_legacy_prefix,
                tampered_at_line=idx,
                reason=f"line {idx} is not valid JSON — file tampered or truncated",
                path=path,
            )
        if "prev_hash" not in d:
            if chain_started:
                return VerifyResult(
                    ok=False,
                    n_lines=len(lines),
                    n_chained=n_chained,
                    n_legacy_prefix=n_legacy_prefix,
                    tampered_at_line=idx,
                    reason=(
                        f"line {idx} is missing prev_hash but the chain "
                        "began earlier — event removed or reverted to "
                        "legacy form"
                    ),
                    path=path,
                )
            n_legacy_prefix += 1
            prev_line_bytes = line
            continue
        # Chained line.
        claimed = d["prev_hash"]
        if prev_line_bytes is None:
            # First chained line — `claimed` may be None (fresh file)
            # OR a hash carried over from a rotated file. We can't
            # check the latter without the rotated file, so we just
            # accept whatever is there and start tracking.
            chain_started = True
            n_chained += 1
            prev_line_bytes = line
            continue
        actual = _hash_line(prev_line_bytes)
        if claimed != actual:
            return VerifyResult(
                ok=False,
                n_lines=len(lines),
                n_chained=n_chained,
                n_legacy_prefix=n_legacy_prefix,
                tampered_at_line=idx,
                reason=(
                    f"line {idx} prev_hash {claimed[:16]}… does not match "
                    f"actual hash of line {idx - 1} ({actual[:16]}…) — "
                    "file tampered"
                ),
                path=path,
            )
        chain_started = True
        n_chained += 1
        prev_line_bytes = line
    return VerifyResult(
        ok=True,
        n_lines=len(lines),
        n_chained=n_chained,
        n_legacy_prefix=n_legacy_prefix,
        tampered_at_line=0,
        reason=(
            f"chain verified: {n_chained} chained line(s)"
            + (f", {n_legacy_prefix} legacy prefix" if n_legacy_prefix else "")
        ),
        path=path,
    )
