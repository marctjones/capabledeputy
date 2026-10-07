"""Opt-in read-only provider evidence for #479, with metadata-only recording.

Preflight is the default. Live reads require retention/exposure review and an
expected daemon revision. This never grants SEND_EMAIL or approves an action.
The private session still contains mail; a redacted report does not erase it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.policy.capabilities import Capability, CapabilityKind

MODES = ("turn_level", "dual_llm", "programmatic", "reference", "sealed")
TOOLS = {"quarantined.summarize_mail", "quarantined.fetch_and_extract", "mail.read_recent"}


def project_outcomes(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Do not trust even nominally numeric provider fields to be safe strings."""
    rows = []
    for outcome in result.get("tool_outcomes", []):
        if not isinstance(outcome, dict):
            continue
        output = outcome.get("output")
        output = output if isinstance(output, dict) else {}
        row = {
            "tool": outcome.get("tool_name") if outcome.get("tool_name") in TOOLS else "other",
            "decision": outcome.get("decision")
            if outcome.get("decision") in {"allow", "deny", "require_approval", "suggest"}
            else "other",
            "error": bool(outcome.get("error")),
        }
        for key in ("ok", "complete"):
            if type(output.get(key)) is bool:
                row[key] = output[key]
        for key in ("matched_count", "fetched_count", "summarized_count"):
            if type(output.get(key)) is int and output[key] >= 0:
                row[key] = output[key]
        rows.append(row)
    return rows


def audit_is_redacted(events: list[dict[str, Any]]) -> bool:
    returned = [e for e in events if e.get("event_type") == "tool.returned"]
    return bool(returned) and all(
        (p := e.get("payload", {})).get("output_redacted") is True
        and "output" not in p
        and re.fullmatch(r"[0-9a-f]{64}", str(p.get("output_digest", ""))) is not None
        and type(p.get("output_size_bytes")) is int
        and p["output_size_bytes"] >= 0
        for e in returned
    )


async def run(client: DaemonClient, args: argparse.Namespace, report: dict[str, Any]) -> None:
    version = await asyncio.wait_for(client.call("daemon.code_version"), 10)
    revision = version.get("git_rev", "")
    report["daemon_revision_matches"] = revision == args.expected_revision
    report["daemon_dirty"] = version.get("git_dirty") != "clean"
    if re.fullmatch(r"[0-9a-f]{40}", revision):
        report["daemon_revision"] = revision
    if revision != args.expected_revision:
        report["status"] = "blocked_daemon_revision"
        return
    if not args.live:
        report["status"] = "preflight_only"
        return
    if not args.retention_reviewed or not args.required_mode:
        report["status"] = "blocked_privacy_or_exposure_review"
        return
    report["required_modes"] = args.required_mode
    session_id = None
    started = time.monotonic()
    try:
        session = await client.call(
            "session.new",
            {
                "purpose_handle": "inbox",
                "owner": "live-mail-acceptance",
                "intent": "Read-only real email summary acceptance",
                "required_modes": args.required_mode,
            },
        )
        session_id = session["id"]
        if session.get("enforcement_mode") != "strict":
            report["status"] = "blocked_enforcement_posture"
            return
        if set(session.get("required_modes") or []) != set(args.required_mode):
            report["status"] = "blocked_exposure_constraint"
            return
        if any(c.get("kind") == "SEND_EMAIL" for c in session.get("capability_set", [])):
            report["status"] = "blocked_send_authority"
            return
        capability = Capability(
            kind=CapabilityKind.IMAP_READ,
            pattern="*",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        await client.call(
            "operator.grant_capability",
            {"session_id": session_id, "capability": capability.to_dict()},
        )
        result = await asyncio.wait_for(
            client.call(
                "session.send",
                {
                    "session_id": session_id,
                    "max_iterations": 5,
                    "message": "Summarize my two most recent real emails. Read their bodies using "
                    "quarantined.summarize_mail. Do not use demo data, draft, or send. "
                    "Clearly report incomplete or truncated coverage.",
                },
            ),
            timeout=240,
        )
        report["tools"] = project_outcomes(result)
        report["summary_produced"] = any(r.get("summarized_count", 0) > 0 for r in report["tools"])
        audit = await client.call("audit.list", {"session_id": session_id, "limit": 100})
        report["returned_audit_redacted"] = audit_is_redacted(audit.get("events", []))
        report["status"] = (
            "read_completed_quality_unreviewed"
            if report["summary_produced"] and report["returned_audit_redacted"]
            else "failed_read_or_audit"
        )
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        if session_id is not None:
            try:
                await asyncio.wait_for(client.call("session.abort", {"session_id": session_id}), 10)
                report["session_aborted"] = True
            except Exception:
                report["session_aborted"] = False
                report["status"] = "failed_cleanup"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--report", type=Path, required=True, help="New metadata-only JSON file")
    parser.add_argument("--live", action="store_true", help="Perform bounded personal-mail reads")
    parser.add_argument(
        "--retention-reviewed", action="store_true", help="Operator checked #454 retention posture"
    )
    parser.add_argument("--required-mode", choices=MODES, action="append")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_revision):
        parser.error("expected revision must be a full Git SHA")
    report: dict[str, Any] = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "expected_revision": args.expected_revision,
        "live_requested": args.live,
        "send_authority_granted": False,
        "human_quality_review": None,
        "observed_inbox_receipt": None,
        "status": "not_started",
    }
    # Reserve output before any real read. Never overwrite or follow a symlink.
    fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as evidence:
        try:
            await run(DaemonClient(default_socket_path()), args, report)
        except Exception as exc:
            report["status"] = "failed"
            report["error_type"] = type(exc).__name__  # no provider exception text
        finally:
            json.dump(report, evidence, indent=2, allow_nan=False)
            evidence.write("\n")
            evidence.flush()
            os.fsync(evidence.fileno())
    print(json.dumps(report, allow_nan=False))
    return 0 if report["status"] in {"preflight_only", "read_completed_quality_unreviewed"} else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
