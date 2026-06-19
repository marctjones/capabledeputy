"""HTTP authentication helpers for remote MCP upstreams."""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

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


def httpx_auth_from_config(config: UpstreamAuthConfig | None) -> httpx.Auth | None:
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
    raise ValueError(f"unsupported auth.type: {config.type}")
