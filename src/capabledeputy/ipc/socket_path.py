"""Resolve the Unix socket path used by the daemon."""

from __future__ import annotations

import os
from pathlib import Path


def default_socket_path() -> Path:
    override = os.environ.get("CAPDEP_SOCKET")
    if override:
        return Path(override)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "capdep.sock"
    return Path("/tmp") / f"capdep-{os.getuid()}.sock"


def operator_token_path(socket_path: Path) -> Path:
    """Sibling file holding the daemon's per-run operator auth token.

    Same-user-readable (0600) like the socket itself — see
    capabledeputy.daemon.authz for what this token can and can't prove.
    """
    return socket_path.with_name(socket_path.name + ".operator-token")
