"""HTTP authentication helpers for remote MCP upstreams."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from capabledeputy.upstream.config import UpstreamAuthConfig

if TYPE_CHECKING:
    from google.auth.credentials import Credentials


class BearerTokenAuth(httpx.Auth):
    """Static bearer-token auth for remote MCP endpoints."""

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("bearer auth requires a non-empty token")
        self._token = token

    def sync_auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


class GoogleAdcAuth(httpx.Auth):
    """Google Application Default Credentials auth for remote MCP.

    The token refresh path is synchronous because httpx.Auth's sync
    flow is what the MCP SDK accepts. Refresh is guarded by a lock so
    concurrent stream setup does not race the credentials object.
    """

    def __init__(self, scopes: tuple[str, ...] = (), quota_project_id: str = "") -> None:
        self._scopes = scopes
        self._quota_project_id = quota_project_id
        self._credentials: Credentials | None = None
        self._lock = threading.Lock()

    def sync_auth_flow(self, request: httpx.Request):
        credentials = self._valid_credentials()
        request.headers["Authorization"] = f"Bearer {credentials.token}"
        yield request

    def _valid_credentials(self) -> Credentials:
        with self._lock:
            credentials = self._credentials
            if credentials is None:
                credentials = self._load_credentials()
                self._credentials = credentials
            if not credentials.valid:
                from google.auth.transport.requests import Request

                credentials.refresh(Request())
            return credentials

    def _load_credentials(self) -> Credentials:
        try:
            import google.auth
        except ImportError as e:  # pragma: no cover - dependency is declared.
            raise RuntimeError(
                "google_adc auth requires google-auth; install CapDep with Google extras"
            ) from e

        credentials, _project_id = google.auth.default(
            scopes=self._scopes or None,
            quota_project_id=self._quota_project_id or None,
        )
        if self._scopes and getattr(credentials, "requires_scopes", False):
            credentials = credentials.with_scopes(self._scopes)
        if self._quota_project_id and hasattr(credentials, "with_quota_project"):
            credentials = credentials.with_quota_project(self._quota_project_id)
        return credentials


@dataclass(frozen=True)
class OAuth2Endpoints:
    authorization_url: str
    token_url: str


class OAuth2TokenAuth(httpx.Auth):
    """OAuth2 bearer auth backed by CapDep's local token cache."""

    def __init__(
        self,
        config: UpstreamAuthConfig,
        *,
        server_name: str = "default",
        requested_scopes: tuple[str, ...] = (),
    ) -> None:
        if config.type != "oauth2":
            raise ValueError("OAuth2TokenAuth requires auth.type oauth2")
        self._config = config
        self._server_name = server_name
        self._requested_scopes = _requested_scopes(config, requested_scopes)
        self._token_cache = oauth_token_cache_path(config, server_name)
        self._lock = threading.Lock()

    @property
    def token_cache(self) -> Path:
        return self._token_cache

    def sync_auth_flow(self, request: httpx.Request):
        token = self._valid_access_token()
        request.headers["Authorization"] = f"Bearer {token}"
        yield request

    def _valid_access_token(self) -> str:
        with self._lock:
            token_data = _read_token_cache(self._token_cache)
            if token_data and _token_is_valid(token_data):
                _ensure_scope_subset(
                    token_data,
                    self._config,
                    self._requested_scopes,
                    server_name=self._server_name,
                )
                return str(token_data["access_token"])
            if token_data and token_data.get("refresh_token"):
                token_data = refresh_oauth2_token(
                    self._config,
                    token_data,
                    server_name=self._server_name,
                )
                _ensure_scope_subset(
                    token_data,
                    self._config,
                    self._requested_scopes,
                    server_name=self._server_name,
                )
                return str(token_data["access_token"])
        raise RuntimeError(
            "oauth2 token is missing or expired; run "
            f"`capdep oauth login --server {self._server_name}`",
        )


def oauth2_credential_status(
    config: UpstreamAuthConfig | None,
    *,
    server_name: str = "default",
    requested_scopes: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return a redacted operator-facing OAuth credential status."""
    if config is None or config.type != "oauth2":
        return {
            "server": server_name,
            "auth_type": "none" if config is None else config.type,
            "status": "not_oauth2",
            "connected": False,
        }

    token_cache = oauth_token_cache_path(config, server_name)
    requested = _requested_scopes(config, requested_scopes)
    base: dict[str, Any] = {
        "server": server_name,
        "auth_type": "oauth2",
        "token_cache": str(token_cache),
        "requested_scopes": list(requested),
        "connected": False,
    }
    login_action = f"capdep oauth login --server {server_name}"

    token_data = _read_token_cache(token_cache)
    if token_data is None:
        return base | {
            "status": "missing",
            "recovery": login_action,
        }

    granted = _granted_scopes(token_data, config)
    expires_at = token_data.get("expires_at")
    base |= {
        "granted_scopes": list(granted),
        "expires_at": expires_at,
        "has_refresh_token": bool(token_data.get("refresh_token")),
    }
    missing_scopes = _missing_scopes(requested, granted)
    if missing_scopes:
        return base | {
            "status": "mis_scoped",
            "missing_scopes": list(missing_scopes),
            "recovery": login_action,
        }
    if _token_is_valid(token_data):
        return base | {
            "status": "connected",
            "connected": True,
        }
    if token_data.get("refresh_token"):
        return base | {
            "status": "refreshable",
            "recovery": "token will refresh on next upstream connection",
        }
    return base | {
        "status": "expired",
        "recovery": login_action,
    }


def oauth_token_cache_path(config: UpstreamAuthConfig, server_name: str) -> Path:
    """Resolve the on-disk token cache for an OAuth-configured server."""
    if config.token_cache:
        return Path(config.token_cache).expanduser()
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", server_name).strip("._") or "default"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "capabledeputy" / "oauth" / f"{safe_name}.json"


def httpx_auth_from_config(
    config: UpstreamAuthConfig | None,
    *,
    server_name: str = "default",
    requested_scopes: tuple[str, ...] = (),
) -> httpx.Auth | None:
    """Build an httpx auth object from an upstream auth config."""
    if config is None or config.type == "none":
        return None
    if config.type == "bearer":
        token = config.token
        if not token and config.token_env:
            token = os.environ.get(config.token_env, "")
        return BearerTokenAuth(token)
    if config.type == "google_adc":
        return GoogleAdcAuth(scopes=config.scopes, quota_project_id=config.quota_project_id)
    if config.type == "oauth2":
        return OAuth2TokenAuth(
            config,
            server_name=server_name,
            requested_scopes=requested_scopes,
        )
    raise ValueError(f"unsupported auth.type: {config.type}")


def oauth2_authorization_url(
    config: UpstreamAuthConfig,
    *,
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    """Build the provider authorization URL for the OAuth2 login flow."""
    endpoints = discover_oauth2_endpoints(config)
    client_id = _required_config_secret(
        config.client_id,
        config.client_id_env,
        config.client_id_file,
        "client_id",
    )
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if config.scopes:
        params["scope"] = " ".join(config.scopes)
    if config.resource:
        params["resource"] = config.resource
    if config.audience:
        params["audience"] = config.audience
    params.update(config.extra_authorize_params)
    separator = "&" if "?" in endpoints.authorization_url else "?"
    return endpoints.authorization_url + separator + urlencode(params)


def perform_oauth2_login(
    config: UpstreamAuthConfig,
    *,
    server_name: str,
    open_browser: bool = True,
    timeout_seconds: int = 180,
    emit: Any = print,
) -> Path:
    """Run a local browser OAuth2 Authorization Code + PKCE login.

    The resulting token response is cached with 0600 file permissions and
    used by OAuth2TokenAuth for remote MCP HTTP connections.
    """
    if config.type != "oauth2":
        raise ValueError("oauth login requires auth.type oauth2")

    verifier = _new_pkce_verifier()
    challenge = _pkce_challenge(verifier)
    state = secrets.token_urlsafe(24)

    callback = _prepare_callback(config)
    server = HTTPServer((callback.host, callback.port), _callback_handler(callback.path, state))
    server.timeout = timeout_seconds
    actual_redirect_uri = callback.redirect_uri_for(server.server_port)
    auth_url = oauth2_authorization_url(
        config,
        redirect_uri=actual_redirect_uri,
        state=state,
        code_challenge=challenge,
    )

    emit(f"Open this URL to authorize {server_name}:\n{auth_url}")
    if open_browser:
        webbrowser.open(auth_url)

    result: dict[str, str] = {}
    # The handler class writes onto the HTTPServer instance for this one request.
    server.capdep_oauth_result = result  # type: ignore[attr-defined]
    server.handle_request()
    server.server_close()

    if not result:
        raise TimeoutError(f"oauth login timed out after {timeout_seconds}s")
    if result.get("error"):
        raise RuntimeError(f"oauth login failed: {result['error']}")
    code = result.get("code")
    if not code:
        raise RuntimeError("oauth login did not receive an authorization code")

    token_data = exchange_oauth2_code(
        config,
        code=code,
        redirect_uri=actual_redirect_uri,
        code_verifier=verifier,
    )
    path = oauth_token_cache_path(config, server_name)
    _write_token_cache(path, token_data)
    return path


def exchange_oauth2_code(
    config: UpstreamAuthConfig,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    """Exchange an authorization code for token data."""
    endpoints = discover_oauth2_endpoints(config)
    client_id = _required_config_secret(
        config.client_id,
        config.client_id_env,
        config.client_id_file,
        "client_id",
    )
    client_secret = _optional_config_secret(
        config.client_secret,
        config.client_secret_env,
        config.client_secret_file,
    )
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    if config.resource:
        data["resource"] = config.resource
    data.update(config.extra_token_params)
    response = httpx.post(
        endpoints.token_url,
        data=data,
        headers={"Accept": "application/json"},
        timeout=30.0,
    )
    response.raise_for_status()
    return _normalize_token_response(response.json())


def refresh_oauth2_token(
    config: UpstreamAuthConfig,
    token_data: dict[str, Any],
    *,
    server_name: str,
) -> dict[str, Any]:
    """Refresh a cached OAuth2 token and persist the updated cache."""
    endpoints = discover_oauth2_endpoints(config)
    refresh_token = str(token_data.get("refresh_token") or "")
    if not refresh_token:
        raise RuntimeError("oauth2 token cache has no refresh_token")
    client_id = _required_config_secret(
        config.client_id,
        config.client_id_env,
        config.client_id_file,
        "client_id",
    )
    client_secret = _optional_config_secret(
        config.client_secret,
        config.client_secret_env,
        config.client_secret_file,
    )
    data: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret
    if config.scopes:
        data["scope"] = " ".join(config.scopes)
    if config.resource:
        data["resource"] = config.resource
    data.update(config.extra_token_params)

    response = httpx.post(
        endpoints.token_url,
        data=data,
        headers={"Accept": "application/json"},
        timeout=30.0,
    )
    response.raise_for_status()
    refreshed = _normalize_token_response(response.json())
    refreshed.setdefault("refresh_token", refresh_token)
    _write_token_cache(oauth_token_cache_path(config, server_name), refreshed)
    return refreshed


def discover_oauth2_endpoints(config: UpstreamAuthConfig) -> OAuth2Endpoints:
    """Resolve authorization/token endpoints from config or metadata."""
    if config.authorization_url and config.token_url:
        return OAuth2Endpoints(config.authorization_url, config.token_url)

    metadata_url = config.authorization_metadata_url
    if not metadata_url and config.protected_resource_metadata_url:
        metadata_url = _authorization_metadata_from_protected_resource(
            config.protected_resource_metadata_url,
        )
    if metadata_url:
        response = httpx.get(metadata_url, headers={"Accept": "application/json"}, timeout=30.0)
        response.raise_for_status()
        metadata = response.json()
        authorization_url = str(metadata.get("authorization_endpoint") or "")
        token_url = str(metadata.get("token_endpoint") or "")
        if authorization_url and token_url:
            return OAuth2Endpoints(authorization_url, token_url)

    raise ValueError(
        "oauth2 auth requires authorization_url/token_url or OAuth authorization metadata",
    )


def _authorization_metadata_from_protected_resource(resource_metadata_url: str) -> str:
    response = httpx.get(
        resource_metadata_url,
        headers={"Accept": "application/json"},
        timeout=30.0,
    )
    response.raise_for_status()
    metadata = response.json()
    servers = metadata.get("authorization_servers") or []
    if not servers:
        raise ValueError("protected resource metadata has no authorization_servers")
    issuer = str(servers[0]).rstrip("/")
    parsed = urlparse(issuer)
    host = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.strip("/")
    candidates = []
    if path:
        candidates.append(f"{host}/.well-known/oauth-authorization-server/{path}")
    candidates.append(f"{issuer}/.well-known/oauth-authorization-server")
    if not path:
        candidates.append(f"{host}/.well-known/oauth-authorization-server")

    for candidate in candidates:
        try:
            probe = httpx.get(candidate, headers={"Accept": "application/json"}, timeout=30.0)
            if probe.status_code < 400:
                return candidate
        except httpx.HTTPError:
            continue
    # Return the standards-preferred first candidate so callers surface the
    # provider response rather than a synthetic "not found" if it changes.
    return candidates[0]


@dataclass(frozen=True)
class _CallbackConfig:
    host: str
    port: int
    path: str
    fixed_redirect_uri: str = ""

    def redirect_uri_for(self, actual_port: int) -> str:
        if self.fixed_redirect_uri:
            return self.fixed_redirect_uri
        return f"http://{self.host}:{actual_port}{self.path}"


def _prepare_callback(config: UpstreamAuthConfig) -> _CallbackConfig:
    if config.redirect_uri:
        parsed = urlparse(config.redirect_uri)
        host = parsed.hostname or config.redirect_host or "127.0.0.1"
        port = parsed.port or config.redirect_port or 0
        path = parsed.path or "/oauth/callback"
        return _CallbackConfig(
            host=host,
            port=port,
            path=path,
            fixed_redirect_uri=config.redirect_uri,
        )
    return _CallbackConfig(
        host=config.redirect_host or "127.0.0.1",
        port=config.redirect_port,
        path="/oauth/callback",
    )


def _callback_handler(callback_path: str, expected_state: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            result: dict[str, str] = self.server.capdep_oauth_result  # type: ignore[attr-defined]
            if parsed.path != callback_path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return
            params = parse_qs(parsed.query)
            state = (params.get("state") or [""])[0]
            if state != expected_state:
                result["error"] = "state mismatch"
            elif "error" in params:
                result["error"] = (params.get("error") or ["oauth error"])[0]
            else:
                result["code"] = (params.get("code") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>CapDep OAuth complete</h1>"
                b"<p>You can close this browser tab.</p></body></html>",
            )

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    return Handler


def _new_pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _required_config_secret(value: str, env_name: str, file_name: str, label: str) -> str:
    resolved = _optional_config_secret(value, env_name, file_name)
    if not resolved:
        raise ValueError(f"oauth2 auth requires {label}, {label}_env, or {label}_file")
    return resolved


def _optional_config_secret(value: str, env_name: str, file_name: str = "") -> str:
    if value:
        return value
    if env_name:
        return os.environ.get(env_name, "")
    if file_name:
        try:
            return Path(file_name).expanduser().read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""
    return ""


def _normalize_token_response(raw: dict[str, Any]) -> dict[str, Any]:
    token = dict(raw)
    if not token.get("access_token"):
        raise RuntimeError("OAuth token response did not include access_token")
    expires_in = token.get("expires_in")
    if expires_in is not None:
        token["expires_at"] = time.time() + int(expires_in)
    return token


def _token_is_valid(token_data: dict[str, Any]) -> bool:
    if not token_data.get("access_token"):
        return False
    expires_at = token_data.get("expires_at")
    if expires_at is None:
        return True
    return float(expires_at) > time.time() + 60


def _requested_scopes(
    config: UpstreamAuthConfig,
    requested_scopes: tuple[str, ...],
) -> tuple[str, ...]:
    return _normalize_scope_values(requested_scopes or config.scopes)


def _granted_scopes(token_data: dict[str, Any], config: UpstreamAuthConfig) -> tuple[str, ...]:
    raw = (
        token_data.get("scope")
        or token_data.get("scopes")
        or token_data.get("granted_scopes")
        or config.scopes
    )
    return _normalize_scope_values(raw)


def _normalize_scope_values(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        values = re.split(r"[\s,]+", raw.strip())
    elif isinstance(raw, list | tuple | set | frozenset):
        values = [str(value).strip() for value in raw]
    else:
        values = [str(raw).strip()]
    return tuple(dict.fromkeys(value for value in values if value))


def _missing_scopes(
    requested_scopes: tuple[str, ...],
    granted_scopes: tuple[str, ...],
) -> tuple[str, ...]:
    granted = set(granted_scopes)
    return tuple(scope for scope in requested_scopes if scope not in granted)


def _ensure_scope_subset(
    token_data: dict[str, Any],
    config: UpstreamAuthConfig,
    requested_scopes: tuple[str, ...],
    *,
    server_name: str,
) -> None:
    missing = _missing_scopes(requested_scopes, _granted_scopes(token_data, config))
    if missing:
        missing_text = ", ".join(missing)
        raise RuntimeError(
            "oauth2 token is missing required scopes "
            f"for {server_name}: {missing_text}; run "
            f"`capdep oauth login --server {server_name}`",
        )


def _read_token_cache(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        raise RuntimeError(f"invalid oauth2 token cache: {path}") from e


def _write_token_cache(path: Path, token_data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2, sort_keys=True)
        f.write("\n")
