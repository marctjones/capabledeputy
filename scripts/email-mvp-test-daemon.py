#!/usr/bin/env python3
"""Isolated real daemon for the native supervised-email acceptance GUI.

Run from repository root:
  .venv/bin/python scripts/email-mvp-test-daemon.py \\
    --socket /tmp/capdep-email-mvp.sock --state-dir /tmp/capdep-email-mvp

No account settings are loaded. IMAP, SMTP, and LLM responses are synthetic.
Type 'Summarize my recent emails.' in the connected GUI. The normal daemon
handlers enforce the source, draft, review, approval, and delivery lifecycle.
State and read-only SMTP evidence survive process restart in --state-dir.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from unittest.mock import patch

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.daemon_integration import wait_for_socket
from tests.test_email_mvp_acceptance import SUMMARY_PROMPT, build_email_mvp_daemon


async def run(socket_path: Path, state_dir: Path, smtp_outcome: str):
    if socket_path.exists():
        with socket.socket(socket.AF_UNIX) as probe:
            try:
                probe.connect(str(socket_path))
            except ConnectionRefusedError:
                pass
            else:
                raise RuntimeError("The requested socket is owned by a live daemon")
    daemon, app, paths, transport = await build_email_mvp_daemon(state_dir, socket_path)
    transport.mode = {
        "accepted": "accepted",
        "failed_before_submission": "failed",
        "outcome_unknown": "unknown",
    }[smtp_outcome]
    with patch("capabledeputy.tools.native.email.deliver", transport.deliver):
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(daemon.serve)
                await wait_for_socket(paths.socket)
                print(
                    json.dumps(
                        {
                            "event": "email_mvp_fixture_ready",
                            "synthetic": True,
                            "socket": str(paths.socket),
                            "state_dir": str(state_dir),
                            "evidence": str(transport.evidence_path),
                            "prompt": SUMMARY_PROMPT,
                            "smtp_outcome": smtp_outcome,
                        }
                    ),
                    flush=True,
                )
        finally:
            await app.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--smtp-outcome",
        choices=("accepted", "failed_before_submission", "outcome_unknown"),
        default="accepted",
    )
    args = parser.parse_args()
    anyio.run(run, args.socket, args.state_dir, args.smtp_outcome)
