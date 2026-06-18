from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4


def short_socket_path(name: str = "test.sock") -> Path:
    """Return a macOS-safe Unix-socket path for tests.

    Pytest's tmp_path can live under a long per-user temp root, and AF_UNIX
    paths have a small platform limit. Keep the socket itself under /tmp while
    callers continue to store databases/logs in tmp_path.
    """
    base = Path("/tmp") / "capdep-tests" / f"{os.getpid()}-{uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)
    return base / name
