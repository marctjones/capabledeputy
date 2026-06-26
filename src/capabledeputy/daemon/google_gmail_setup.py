"""Daemon-owned setup helpers for official Google Workspace MCP servers."""

from __future__ import annotations

import json
import os
import textwrap
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from anyio.to_thread import run_sync as run_sync_in_worker_thread

from capabledeputy.upstream.config import UpstreamServerConfig
from capabledeputy.upstream.http_auth import oauth_token_cache_path, perform_oauth2_login
from capabledeputy.upstream.server_yaml import ServerYamlConfig

GOOGLE_GMAIL_SERVER = "google-gmail"
GOOGLE_CALENDAR_SERVER = "google-calendar"
GOOGLE_DRIVE_SERVER = "google-drive"


@dataclass(frozen=True)
class GoogleOAuthService:
    server_id: str
    display_name: str
    url: str
    scopes: tuple[str, ...]
    disabled_kinds: tuple[str, ...] = ()
    tool_overrides: dict[str, dict[str, Any]] | None = None
    strict: bool = False


GOOGLE_OAUTH_SERVICES: dict[str, GoogleOAuthService] = {
    GOOGLE_GMAIL_SERVER: GoogleOAuthService(
        server_id=GOOGLE_GMAIL_SERVER,
        display_name="Google Gmail",
        url="https://gmailmcp.googleapis.com/mcp/v1",
        scopes=(
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/gmail.settings.basic",
        ),
        disabled_kinds=("SEND_EMAIL",),
        tool_overrides={
            "create_draft": {
                "capability_kind": "GMAIL_DRAFT",
                "additional_labels": ["confidential.personal"],
                "target_arg": "to",
            },
            "create_label": {
                "capability_kind": "MODIFY_FS",
                "additional_labels": ["confidential.personal"],
            },
            "get_thread": {
                "capability_kind": "GMAIL_READ",
                "additional_labels": ["confidential.personal", "untrusted.user_input"],
            },
            "label_message": {
                "capability_kind": "MODIFY_FS",
                "additional_labels": ["confidential.personal"],
            },
            "label_thread": {
                "capability_kind": "MODIFY_FS",
                "additional_labels": ["confidential.personal"],
            },
            "list_drafts": {
                "capability_kind": "GMAIL_READ",
                "additional_labels": ["confidential.personal"],
            },
            "list_labels": {
                "capability_kind": "GMAIL_READ",
                "additional_labels": ["confidential.personal"],
            },
            "search_threads": {
                "capability_kind": "GMAIL_READ",
                "additional_labels": ["confidential.personal", "untrusted.user_input"],
            },
            "unlabel_message": {
                "capability_kind": "MODIFY_FS",
                "additional_labels": ["confidential.personal"],
            },
            "unlabel_thread": {
                "capability_kind": "MODIFY_FS",
                "additional_labels": ["confidential.personal"],
            },
        },
        strict=True,
    ),
    GOOGLE_CALENDAR_SERVER: GoogleOAuthService(
        server_id=GOOGLE_CALENDAR_SERVER,
        display_name="Google Calendar",
        url="https://calendarmcp.googleapis.com/mcp/v1",
        scopes=("https://www.googleapis.com/auth/calendar.readonly",),
    ),
    GOOGLE_DRIVE_SERVER: GoogleOAuthService(
        server_id=GOOGLE_DRIVE_SERVER,
        display_name="Google Drive",
        url="https://drivemcp.googleapis.com/mcp/v1",
        scopes=("https://www.googleapis.com/auth/drive.readonly",),
    ),
}


@dataclass(frozen=True)
class GoogleOAuthPaths:
    server_id: str
    config_home: Path
    servers_dir: Path
    server_yaml: Path
    oauth_dir: Path
    client_id_file: Path
    client_secret_file: Path


GmailOAuthPaths = GoogleOAuthPaths


def google_oauth_service(service_id: str) -> GoogleOAuthService:
    try:
        return GOOGLE_OAUTH_SERVICES[service_id]
    except KeyError as exc:
        raise ValueError(f"unknown Google OAuth service: {service_id}") from exc


def google_oauth_paths(
    service_id: str,
    config_home: Path | None = None,
) -> GoogleOAuthPaths:
    service = google_oauth_service(service_id)
    base = config_home or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    capdep = base / "capabledeputy"
    oauth_dir = capdep / "oauth"
    return GoogleOAuthPaths(
        server_id=service.server_id,
        config_home=base,
        servers_dir=capdep / "servers.d",
        server_yaml=capdep / "servers.d" / f"{service.server_id}.yaml",
        oauth_dir=oauth_dir,
        client_id_file=oauth_dir / f"{service.server_id}-client-id",
        client_secret_file=oauth_dir / f"{service.server_id}-client-secret",
    )


def gmail_oauth_paths(config_home: Path | None = None) -> GmailOAuthPaths:
    return google_oauth_paths(GOOGLE_GMAIL_SERVER, config_home)


def google_oauth_status(
    service_id: str,
    config_home: Path | None = None,
) -> dict[str, Any]:
    service = google_oauth_service(service_id)
    paths = google_oauth_paths(service.server_id, config_home)
    server_config = _load_google_server_config(paths.server_yaml)
    token_path = _token_path(service.server_id, server_config, config_home)
    return {
        "server": service.server_id,
        "service_id": service.server_id,
        "display_name": service.display_name,
        "configured": paths.server_yaml.is_file(),
        "client_id_configured": paths.client_id_file.is_file(),
        "client_secret_configured": paths.client_secret_file.is_file(),
        "token_configured": token_path.is_file(),
        "server_yaml": str(paths.server_yaml),
        "client_id_file": str(paths.client_id_file),
        "client_secret_file": str(paths.client_secret_file),
        "token_cache": str(token_path),
        "restart_required": True,
    }


def google_oauth_statuses(config_home: Path | None = None) -> dict[str, Any]:
    return {
        "services": [
            google_oauth_status(service_id, config_home) for service_id in GOOGLE_OAUTH_SERVICES
        ],
    }


def gmail_oauth_status(config_home: Path | None = None) -> dict[str, Any]:
    return google_oauth_status(GOOGLE_GMAIL_SERVER, config_home)


def configure_google_oauth_client(
    service_id: str,
    *,
    client_id: str,
    client_secret: str,
    config_home: Path | None = None,
) -> dict[str, Any]:
    """Persist OAuth client settings and a managed Google MCP server config."""
    service = google_oauth_service(service_id)
    client_id = client_id.strip()
    client_secret = client_secret.strip()
    if not client_id:
        raise ValueError(f"{service.display_name} OAuth client ID is required")
    if not client_secret:
        raise ValueError(f"{service.display_name} OAuth client secret is required")

    paths = google_oauth_paths(service.server_id, config_home)
    paths.oauth_dir.mkdir(parents=True, exist_ok=True)
    paths.servers_dir.mkdir(parents=True, exist_ok=True)
    _write_secret_file(paths.client_id_file, client_id)
    _write_secret_file(paths.client_secret_file, client_secret)
    paths.server_yaml.write_text(_google_server_yaml(paths, service), encoding="utf-8")
    os.chmod(paths.server_yaml, 0o600)
    return google_oauth_status(service.server_id, config_home)


def configure_gmail_oauth_client(
    *,
    client_id: str,
    client_secret: str,
    config_home: Path | None = None,
) -> dict[str, Any]:
    return configure_google_oauth_client(
        GOOGLE_GMAIL_SERVER,
        client_id=client_id,
        client_secret=client_secret,
        config_home=config_home,
    )


async def run_google_oauth_login(
    service_id: str,
    *,
    open_browser: bool = True,
    timeout_seconds: int = 180,
    config_home: Path | None = None,
) -> dict[str, Any]:
    service = google_oauth_service(service_id)
    paths = google_oauth_paths(service.server_id, config_home)
    server_config = _load_google_server_config(paths.server_yaml)
    if server_config is None or server_config.auth is None:
        raise RuntimeError(f"{service.display_name} MCP OAuth is not configured yet")

    def _login() -> Path:
        return perform_oauth2_login(
            server_config.auth,  # type: ignore[arg-type]
            server_name=server_config.name,
            open_browser=open_browser,
            timeout_seconds=timeout_seconds,
        )

    token_path = await run_sync_in_worker_thread(_login)
    status = google_oauth_status(service.server_id, config_home)
    return {**status, "token_cache": str(token_path)}


async def run_gmail_oauth_login(
    *,
    open_browser: bool = True,
    timeout_seconds: int = 180,
    config_home: Path | None = None,
) -> dict[str, Any]:
    return await run_google_oauth_login(
        GOOGLE_GMAIL_SERVER,
        open_browser=open_browser,
        timeout_seconds=timeout_seconds,
        config_home=config_home,
    )


def revoke_google_oauth_token(
    service_id: str,
    config_home: Path | None = None,
) -> dict[str, Any]:
    service = google_oauth_service(service_id)
    status = google_oauth_status(service.server_id, config_home)
    with suppress(FileNotFoundError):
        Path(status["token_cache"]).unlink()
    return google_oauth_status(service.server_id, config_home)


def _load_google_server_config(path: Path) -> UpstreamServerConfig | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return None
    parsed = ServerYamlConfig.from_dict(raw, filename=str(path))
    return parsed.server_config


def _load_gmail_server_config(path: Path) -> UpstreamServerConfig | None:
    return _load_google_server_config(path)


def _token_path(
    service_id: str,
    server_config: UpstreamServerConfig | None,
    config_home: Path | None = None,
) -> Path:
    if server_config is not None and server_config.auth is not None:
        return oauth_token_cache_path(server_config.auth, server_config.name)
    return google_oauth_paths(service_id, config_home).oauth_dir / f"{service_id}.json"


def _write_secret_file(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value)
        f.write("\n")


def _google_server_yaml(paths: GoogleOAuthPaths, service: GoogleOAuthService) -> str:
    body = {
        "schema_version": 1,
        "name": service.server_id,
        "transport": "streamable_http",
        "url": service.url,
        "auth": {
            "type": "oauth2",
            "client_id_file": str(paths.client_id_file),
            "client_secret_file": str(paths.client_secret_file),
            "token_cache": str(paths.oauth_dir / f"{service.server_id}.json"),
            "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "scopes": list(service.scopes),
            "extra_authorize_params": {
                "access_type": "offline",
                "prompt": "consent",
            },
        },
        "inherent_labels": ["confidential.personal", "untrusted.user_input"],
        "disabled_kinds": list(service.disabled_kinds),
        "tool_overrides": service.tool_overrides or {},
        "strict": service.strict,
    }
    rendered = yaml.safe_dump(body, sort_keys=False)
    header = textwrap.dedent(
        f"""\
        # Managed by CapDep daemon setup.google.configure_oauth for {service.server_id}.
        # OAuth client secret values live in mode-0600 files under ../oauth/.
        """,
    )
    return header + rendered


def _gmail_server_yaml(paths: GmailOAuthPaths) -> str:
    return _google_server_yaml(paths, google_oauth_service(GOOGLE_GMAIL_SERVER))


def redacted_google_oauth_payload(status: dict[str, Any]) -> dict[str, Any]:
    """Return an audit-safe shape for setup actions."""
    return {
        key: value for key, value in status.items() if key not in {"client_id", "client_secret"}
    }


def redacted_gmail_oauth_payload(status: dict[str, Any]) -> dict[str, Any]:
    return redacted_google_oauth_payload(status)


def token_cache_summary(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"present": False}
    return {
        "present": bool(data.get("access_token")),
        "has_refresh_token": bool(data.get("refresh_token")),
        "expires_at": data.get("expires_at"),
    }
