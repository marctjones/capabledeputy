"""Credential vault (Issue #13) — keep upstream-server secrets out of the
daemon config, the daemon's broad environment, the audit log, and the LLM
context.

Today upstream credentials live in `UpstreamServerConfig.env` (i.e. in the
daemon config file, or `${VAR}`-expanded from the daemon's own
environment). The vault moves them into a separate, mode-0600 file that the
config references by name; the daemon resolves a server's secrets from the
vault and injects them into *that server's* subprocess env at spawn,
auditing only the vault **ref** (`server:ENVVAR`), never the value.

Scope (be honest about the boundary): stdio MCP servers are long-lived, so
secrets for those servers are materialized at **spawn**, not per dispatch.
The supervisor now passes only a minimal process-bootstrap environment plus
that server's explicit env/vault entries, so unrelated daemon environment
secrets are not inherited by long-lived upstreams. A tool that dumps *its
own* process env can still surface secrets explicitly granted to that server
— true per-call / echo-resistance for stdio needs per-call isolated execution
or a server-specific auth channel. What the vault *does* guarantee today:
secrets are not in committed configs, not in the daemon's global environment,
not in the audit log, never authored by or shown to the LLM, and never
accidentally inherited from ambient daemon env.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class CredentialVaultError(RuntimeError):
    """The vault file is malformed or has unsafe permissions. Fail-closed:
    the daemon refuses to start rather than spawn upstream servers with
    missing or world-readable credentials."""


@dataclass(frozen=True)
class VaultEntry:
    """One server's credentials. `env` maps env-var name → secret value;
    `capability_kinds` is optional, informational scoping recorded in the
    audit ref so an operator can see what the credential is *for*."""

    server: str
    env: dict[str, str] = field(default_factory=dict)
    capability_kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class CredentialVault:
    entries: dict[str, VaultEntry] = field(default_factory=dict)

    def env_for(self, server: str) -> dict[str, str]:
        """The secret env-var map to inject for `server` (empty if none)."""
        entry = self.entries.get(server)
        return dict(entry.env) if entry else {}

    def refs_for(self, server: str) -> list[str]:
        """Audit-safe references (`server:ENVVAR`) — never the values."""
        entry = self.entries.get(server)
        if not entry:
            return []
        return [f"{server}:{name}" for name in sorted(entry.env)]


def _check_permissions(path: Path) -> None:
    """Refuse a vault that is group- or world-readable (mode & 0o077).
    Mirrors how ssh refuses over-permissive key files."""
    mode = path.stat().st_mode
    if mode & 0o077:
        raise CredentialVaultError(
            f"vault {path} has unsafe permissions {stat.filemode(mode)} — "
            f"secrets must not be group/other-accessible. Run "
            f"`chmod 600 {path}`.",
        )


def parse_credential_vault(raw: Any) -> CredentialVault:
    if raw is None:
        return CredentialVault()
    if not isinstance(raw, dict):
        raise CredentialVaultError("vault root must be a mapping")
    creds = raw.get("credentials")
    if creds is None:
        return CredentialVault()
    if not isinstance(creds, list):
        raise CredentialVaultError("vault `credentials` must be a list")
    entries: dict[str, VaultEntry] = {}
    for i, item in enumerate(creds):
        if not isinstance(item, dict):
            raise CredentialVaultError(f"credentials[{i}] must be a mapping")
        server = item.get("server")
        if not server:
            raise CredentialVaultError(f"credentials[{i}] missing `server`")
        env_raw = item.get("env") or {}
        if not isinstance(env_raw, dict):
            raise CredentialVaultError(f"credentials[{i}].env must be a mapping")
        env = {str(k): str(v) for k, v in env_raw.items()}
        kinds = tuple(str(k) for k in (item.get("capability_kinds") or ()))
        if str(server) in entries:
            raise CredentialVaultError(f"credentials[{i}] duplicate server {server!r}")
        entries[str(server)] = VaultEntry(
            server=str(server),
            env=env,
            capability_kinds=kinds,
        )
    return CredentialVault(entries=entries)


def load_credential_vault(path: Path) -> CredentialVault:
    """Load the vault. Absent file ⇒ empty vault (feature off). Unsafe
    permissions or unparseable content ⇒ fail-closed."""
    if not path.is_file():
        return CredentialVault()
    _check_permissions(path)
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise CredentialVaultError(f"vault unparseable: {path} — {e}") from e
    return parse_credential_vault(raw)


def default_vault_path() -> Path:
    """`$XDG_CONFIG_HOME/capabledeputy/secrets/vault.yaml` (or the
    `~/.config` fallback)."""
    import os

    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "capabledeputy" / "secrets" / "vault.yaml"
