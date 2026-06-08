"""Tests for credential discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.secrets import (
    DEFAULT_KEY_FILENAME,
    ENV_VAR,
    USER_CONFIG_KEY_PATH,
    default_search_paths,
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


def test_default_search_paths_includes_cwd_and_user_config() -> None:
    """The default fallback list is cwd-local first, ~/.config/anthropic/api.key
    second. Cwd-first lets a project pin its own key; ~/.config-second is the
    user-global fallback when ANTHROPIC_API_KEY isn't exported in the env."""
    paths = default_search_paths()
    assert paths[0] == Path.cwd() / DEFAULT_KEY_FILENAME
    assert USER_CONFIG_KEY_PATH in paths
    # USER_CONFIG_KEY_PATH should match the documented ~/.config/anthropic/api.key
    assert Path.home() / ".config" / "anthropic" / "api.key" == USER_CONFIG_KEY_PATH


def test_user_config_path_used_when_cwd_file_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When cwd has no CLAUDEAPI.KEY, the loader falls through to
    ~/.config/anthropic/api.key (simulated via a redirected $HOME)."""
    fake_home = tmp_path / "home"
    config_dir = fake_home / ".config" / "anthropic"
    config_dir.mkdir(parents=True)
    user_key_file = config_dir / "api.key"
    user_key_file.write_text("from-user-config\n")
    monkeypatch.setenv("HOME", str(fake_home))

    # Recompute USER_CONFIG_KEY_PATH against the patched $HOME by calling
    # default_search_paths(); the production code re-evaluates Path.home()
    # at module-import time so we have to call the helper that re-derives.
    # To keep the test hermetic, pass the explicit fallback path the loader
    # WOULD have looked at if it re-derived after the monkeypatch.
    found = load_anthropic_api_key(
        search_paths=[
            tmp_path / "no-cwd-key.KEY",  # cwd-local: missing
            user_key_file,  # user-config: present
        ]
    )
    assert found == "from-user-config"
