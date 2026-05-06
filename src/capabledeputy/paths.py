"""Default filesystem paths for daemon state and audit log.

Resolution order for each path:
  1. Explicit env override (CAPDEP_STATE_DB, CAPDEP_AUDIT_LOG, CAPDEP_SOCKET).
  2. CAPDEP_DATA_DIR + filename (for state.db and audit.jsonl).
  3. XDG default + capabledeputy/ subdir + filename.
  4. ~/.local/share/capabledeputy/ + filename.

Container deployments override (1) directly so volume mounts are
self-documenting and the daemon makes no assumptions about $HOME.
"""

from __future__ import annotations

import os
from pathlib import Path


def default_data_dir() -> Path:
    override = os.environ.get("CAPDEP_DATA_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "capabledeputy"
    return Path.home() / ".local" / "share" / "capabledeputy"


def default_state_db_path() -> Path:
    override = os.environ.get("CAPDEP_STATE_DB")
    if override:
        return Path(override)
    return default_data_dir() / "state.db"


def default_audit_log_path() -> Path:
    override = os.environ.get("CAPDEP_AUDIT_LOG")
    if override:
        return Path(override)
    return default_data_dir() / "audit.jsonl"
