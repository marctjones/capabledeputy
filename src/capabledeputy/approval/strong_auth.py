"""Shared strong-authentication requirements for approval requests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Any

from capabledeputy.approval.model import ApprovalAction, ApprovalRequest


async def authenticate_approval(request: ApprovalRequest) -> dict[str, str]:
    """Ask a pinned daemon-owned native helper; never trust a client marker.

    Enrollment is an operator filesystem/configuration action, not an RPC. The
    helper and daemon are trusted installed code. This does not defend against
    an attacker able to replace the daemon or its same-user configuration.
    """
    from capabledeputy.cli._managed_config import user_config_dir

    if sys.platform != "darwin":
        raise ValueError("Required strong authentication is unavailable on this platform")
    config_path = user_config_dir() / "approval-auth.json"
    try:
        metadata = config_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise ValueError(
                "Strong authentication configuration must be an operator-owned private file"
            )
        config = json.loads(config_path.read_text())
        helper = Path(config["helper_path"])
        info = helper.lstat()
        if (
            not helper.is_absolute()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
        ):
            raise ValueError("Strong authentication helper must be an operator-owned executable")
        expected = config["sha256"]
        if (
            not isinstance(expected, str)
            or hashlib.sha256(helper.read_bytes()).hexdigest() != expected
        ):
            raise ValueError(
                "Strong authentication helper changed; verify its installation before continuing"
            )
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(
            "Required strong authentication is not configured. "
            "Set up the native approval helper before continuing."
        ) from error
    binding = hashlib.sha256(
        json.dumps(request.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    nonce = secrets.token_hex(32)
    action = {
        ApprovalAction.SEND_EMAIL: "Send this reply to",
        ApprovalAction.QUEUE_PURCHASE: "Authorize this purchase from",
        ApprovalAction.EXECUTE_DESTRUCTIVE: "Authorize this destructive action on",
    }.get(request.action, "Authorize this action for")
    reason = f"{action} {request.target}"[:180]
    try:
        process = await asyncio.create_subprocess_exec(
            str(helper),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as error:
        raise ValueError(
            "Native authentication could not start. Nothing was authorized."
        ) from error
    try:
        output, _ = await asyncio.wait_for(
            process.communicate(
                json.dumps({"binding": binding, "nonce": nonce, "reason": reason}).encode()
            ),
            timeout=60,
        )
    except TimeoutError as error:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise ValueError("Authentication timed out. Nothing was authorized.") from error
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    try:
        proof = json.loads(output)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(
            "Authentication was cancelled or unavailable. Nothing was authorized."
        ) from error
    if process.returncode != 0 or proof != {
        "binding": binding,
        "nonce": nonce,
        "authenticated": True,
    }:
        raise ValueError("Authentication was cancelled or failed. Nothing was authorized.")
    return {"method": "daemon-native-device-owner", "review_digest": binding}


def approval_requires_strong_auth(request: ApprovalRequest) -> bool:
    if request.action in {
        ApprovalAction.QUEUE_PURCHASE,
        ApprovalAction.EXECUTE_DESTRUCTIVE,
    }:
        return True
    labels = request.labels_in.to_dict()
    rendered = str(labels).lower()
    return any(token in rendered for token in ("financial", "health", "restricted", "prohibited"))


def approval_to_client_dict(
    request: ApprovalRequest,
    *,
    touch_id_policy_enabled: bool | None = None,
) -> dict[str, Any]:
    payload = request.to_dict()
    payload["requires_strong_auth"] = approval_requires_strong_auth(request)
    if touch_id_policy_enabled is not None:
        payload["touch_id_policy_enabled"] = touch_id_policy_enabled
    return payload
