"""Tests for credential discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.secrets import (
    DEFAULT_KEY_FILENAME,
    ENV_VAR,
    load_anthropic_api_key,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)


def test_env_var_wins_over_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_VAR, "from-env")
    key_file = tmp_path / DEFAULT_KEY_FILENAME
    key_file.write_text("from-file\n")
    found = load_anthropic_api_key(search_paths=[key_file])
    assert found == "from-env"


def test_file_used_when_env_unset(tmp_path: Path) -> None:
    key_file = tmp_path / DEFAULT_KEY_FILENAME
    key_file.write_text("  sk-from-file  \n")
    found = load_anthropic_api_key(search_paths=[key_file])
    assert found == "sk-from-file"
    # And it's now in the environment for downstream LiteLLM.
    import os

    assert os.environ[ENV_VAR] == "sk-from-file"


def test_returns_none_when_nothing_available(tmp_path: Path) -> None:
    missing = tmp_path / "nope.KEY"
    assert load_anthropic_api_key(search_paths=[missing]) is None


def test_empty_file_is_treated_as_missing(tmp_path: Path) -> None:
    key_file = tmp_path / DEFAULT_KEY_FILENAME
    key_file.write_text("   \n")
    other = tmp_path / "other.KEY"
    other.write_text("real-key\n")
    found = load_anthropic_api_key(search_paths=[key_file, other])
    assert found == "real-key"


def test_first_existing_file_wins(tmp_path: Path) -> None:
    a = tmp_path / "a.KEY"
    a.write_text("first\n")
    b = tmp_path / "b.KEY"
    b.write_text("second\n")
    assert load_anthropic_api_key(search_paths=[a, b]) == "first"
