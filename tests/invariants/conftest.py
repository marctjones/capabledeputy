"""Shared fixtures for invariant tests (003 T015).

In-memory daemon + frozen-clock fixtures live here so individual
invariant tests can stay terse. As invariant tests land (T016, T017,
T018, T119, T120, then per-phase tests), they pull from here.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def configs_dir() -> Path:
    """Path to the repo's `configs/` dir as seen from pytest's cwd
    (repo root). Tests that need a clean configs dir should use
    `tmp_path` themselves and write stubs."""
    return Path("configs")
