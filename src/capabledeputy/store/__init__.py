"""Shared SQLite store lifecycle: non-destructive migration + backup (#315/#321)."""

from capabledeputy.store.lifecycle import (
    Migration,
    RecoveryOutcome,
    StoreMigrationError,
    prepare_managed_db,
)

__all__ = ["Migration", "RecoveryOutcome", "StoreMigrationError", "prepare_managed_db"]
