"""Google Workspace OAuth helper.

Handles the one-time browser consent + token caching for the bundled
gworkspace MCP server. Tokens cache to:

  $XDG_CONFIG_HOME/capabledeputy/secrets/gworkspace-token.json

The operator runs `capdep gworkspace-setup` once; subsequent daemon
spawns reuse the cached refresh token.

OAuth flow:
  1. Operator runs setup; capdep opens browser to consent URL
  2. Browser POSTs to a localhost ephemeral port
  3. capdep captures the code, exchanges for token
  4. Token cached to disk (mode 0600)
  5. MCP server reads token at startup; uses refresh_token if expired

Operator must FIRST create a Google Cloud project + OAuth client
(Desktop application) and download `credentials.json`. Document
this in the README; the credentials.json belongs to the operator
(not committed to the repo).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Scopes the gworkspace server requests. Operator can narrow these
# per-deployment by editing this file before running setup.
DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar",
)


def _config_dir() -> Path:
    override = os.environ.get("XDG_CONFIG_HOME")
    base = Path(override) if override else Path.home() / ".config"
    return base / "capabledeputy"


def credentials_path() -> Path:
    """Operator-supplied OAuth client credentials.json location."""
    return _config_dir() / "secrets" / "gworkspace-credentials.json"


def token_path() -> Path:
    """Cached refresh token after consent."""
    return _config_dir() / "secrets" / "gworkspace-token.json"


def load_credentials() -> Any:
    """Load cached Google credentials; refresh if expired.

    Returns a google.oauth2.credentials.Credentials object, or raises
    if no token cached + no client credentials available.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    tok_path = token_path()
    if not tok_path.is_file():
        raise FileNotFoundError(
            f"No cached Google token at {tok_path}. Run `capdep gworkspace-setup` first.",
        )
    creds = Credentials.from_authorized_user_file(str(tok_path), list(DEFAULT_SCOPES))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Re-cache refreshed token
        tok_path.write_text(creds.to_json(), encoding="utf-8")
        tok_path.chmod(0o600)
    return creds


def run_consent_flow(scopes: tuple[str, ...] = DEFAULT_SCOPES) -> Path:
    """Run the OAuth consent flow + cache the token.

    Reads operator-supplied client credentials from
    ~/.config/capabledeputy/secrets/gworkspace-credentials.json.

    Opens a browser, captures the redirect, exchanges for token,
    writes token (mode 0o600) to gworkspace-token.json.

    Returns the path to the cached token.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    cred_path = credentials_path()
    if not cred_path.is_file():
        raise FileNotFoundError(
            f"Operator must provide OAuth client credentials at {cred_path}. "
            f"Create a Desktop OAuth client in Google Cloud Console and "
            f"save the downloaded credentials.json there (mode 0600).",
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), list(scopes))
    creds = flow.run_local_server(port=0)
    tok_path = token_path()
    tok_path.parent.mkdir(parents=True, exist_ok=True)
    tok_path.write_text(creds.to_json(), encoding="utf-8")
    tok_path.chmod(0o600)
    return tok_path


def has_cached_token() -> bool:
    return token_path().is_file()
