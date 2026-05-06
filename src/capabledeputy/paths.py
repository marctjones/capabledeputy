"""Default filesystem paths for daemon state and audit log."""

from __future__ import annotations

import os
from pathlib import Path


def default_data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "capabledeputy"
    return Path.home() / ".local" / "share" / "capabledeputy"


def default_state_db_path() -> Path:
    return default_data_dir() / "state.db"


def default_audit_log_path() -> Path:
    return default_data_dir() / "audit.jsonl"
