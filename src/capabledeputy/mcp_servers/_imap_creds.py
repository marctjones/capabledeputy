"""IMAP/SMTP credential discovery for the bundled imap MCP server.

Reads operator-supplied credentials from a YAML file at
$XDG_CONFIG_HOME/capabledeputy/secrets/imap-config.yaml (mode 0o600).

Schema:
  imap:
    host: imap.gmail.com
    port: 993
    username: you@gmail.com
    # Either password_file (preferred) or password (inline; warned).
    password_file: ~/.config/capabledeputy/secrets/imap-password
  smtp:
    host: smtp.gmail.com
    port: 465
    # Defaults to imap.username / same password_file if omitted.
    username: you@gmail.com
    password_file: ~/.config/capabledeputy/secrets/imap-password

The password file should contain ONLY the password (e.g., the
16-character Gmail App Password) on a single line, chmod 0600.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


def _config_dir() -> Path:
    override = os.environ.get("XDG_CONFIG_HOME")
    base = Path(override) if override else Path.home() / ".config"
    return base / "capabledeputy"


def config_path() -> Path:
    return _config_dir() / "secrets" / "imap-config.yaml"


@dataclass(frozen=True)
class ImapConfig:
    host: str
    port: int
    username: str
    password: str


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str


@dataclass(frozen=True)
class ImapServerConfig:
    imap: ImapConfig
    smtp: SmtpConfig


def _read_password_from_file(path_str: str) -> str:
    path = Path(os.path.expanduser(path_str))
    if not path.is_file():
        raise FileNotFoundError(
            f"IMAP password file not found: {path}. "
            f"Create it with the password on a single line; chmod 0600.",
        )
    return path.read_text(encoding="utf-8").strip()


def load_config() -> ImapServerConfig:
    """Load IMAP + SMTP credentials. Fail-closed on missing config."""
    cfg_path = config_path()
    if not cfg_path.is_file():
        raise FileNotFoundError(
            f"IMAP config not found at {cfg_path}. "
            f"Run `capdep imap-setup` to create it, or write it by hand "
            f"per docs/imap-setup.md.",
        )
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"IMAP config malformed: {cfg_path}")

    imap_raw = raw.get("imap") or {}
    smtp_raw = raw.get("smtp") or {}

    # Resolve IMAP password
    if "password" in imap_raw:
        imap_password = str(imap_raw["password"])
    elif "password_file" in imap_raw:
        imap_password = _read_password_from_file(str(imap_raw["password_file"]))
    else:
        raise ValueError("imap config requires `password` or `password_file`")

    # SMTP credentials default to IMAP if omitted
    smtp_username = str(smtp_raw.get("username", imap_raw.get("username", "")))
    if "password" in smtp_raw:
        smtp_password = str(smtp_raw["password"])
    elif "password_file" in smtp_raw:
        smtp_password = _read_password_from_file(str(smtp_raw["password_file"]))
    else:
        smtp_password = imap_password  # share

    return ImapServerConfig(
        imap=ImapConfig(
            host=str(imap_raw.get("host", "imap.gmail.com")),
            port=int(imap_raw.get("port", 993)),
            username=str(imap_raw["username"]),
            password=imap_password,
        ),
        smtp=SmtpConfig(
            host=str(smtp_raw.get("host", "smtp.gmail.com")),
            port=int(smtp_raw.get("port", 465)),
            username=smtp_username,
            password=smtp_password,
        ),
    )


def has_config() -> bool:
    return config_path().is_file()
