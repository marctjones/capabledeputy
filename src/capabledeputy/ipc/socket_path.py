"""Resolve the Unix socket path used by the daemon."""

from __future__ import annotations

import os
from pathlib import Path


def default_socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "capdep.sock"
    return Path("/tmp") / f"capdep-{os.getuid()}.sock"
