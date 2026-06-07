"""Tests for the credential vault (#13)."""

from __future__ import annotations

import pytest

from capabledeputy.upstream.credential_vault import (
    CredentialVaultError,
    load_credential_vault,
    parse_credential_vault,
)


def test_empty_vault_is_noop() -> None:
    v = parse_credential_vault(None)
    assert v.env_for("gmail") == {}
    assert v.refs_for("gmail") == []


def test_parse_and_lookup() -> None:
    v = parse_credential_vault(
        {
            "credentials": [
                {
                    "server": "gmail",
                    "env": {"TOKEN": "s3cret", "OTHER": "x"},
                    "capability_kinds": ["GMAIL_READ"],
                },
            ],
        },
    )
    assert v.env_for("gmail") == {"TOKEN": "s3cret", "OTHER": "x"}
    # refs never include the value
    assert v.refs_for("gmail") == ["gmail:OTHER", "gmail:TOKEN"]
    assert "s3cret" not in " ".join(v.refs_for("gmail"))
    assert v.env_for("absent") == {}


def test_malformed_fails_closed() -> None:
    with pytest.raises(CredentialVaultError):
        parse_credential_vault({"credentials": "not-a-list"})
    with pytest.raises(CredentialVaultError):
        parse_credential_vault({"credentials": [{"env": {"X": "y"}}]})  # no server
    with pytest.raises(CredentialVaultError):
        parse_credential_vault({"credentials": [{"server": "a", "env": "nope"}]})
    with pytest.raises(CredentialVaultError):
        parse_credential_vault(
            {"credentials": [{"server": "a", "env": {}}, {"server": "a", "env": {}}]},
        )


def test_load_rejects_world_readable(tmp_path) -> None:
    p = tmp_path / "vault.yaml"
    p.write_text("credentials: []\n")
    p.chmod(0o644)  # group/other readable
    with pytest.raises(CredentialVaultError):
        load_credential_vault(p)


def test_load_accepts_0600(tmp_path) -> None:
    p = tmp_path / "vault.yaml"
    p.write_text("credentials:\n  - server: gmail\n    env: {TOKEN: s}\n")
    p.chmod(0o600)
    v = load_credential_vault(p)
    assert v.env_for("gmail") == {"TOKEN": "s"}


def test_load_absent_is_noop(tmp_path) -> None:
    v = load_credential_vault(tmp_path / "nope.yaml")
    assert not v.entries


def test_shipped_example_parses(tmp_path) -> None:
    # The example ships 0644-ish in-repo; copy + chmod to load it.
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    src = (repo / "configs" / "vault.example.yaml").read_text()
    p = tmp_path / "vault.yaml"
    p.write_text(src)
    p.chmod(0o600)
    v = load_credential_vault(p)
    assert "gmail" in v.entries
    assert v.refs_for("gmail") == ["gmail:GOOGLE_OAUTH_TOKEN"]
