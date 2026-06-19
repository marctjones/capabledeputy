from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from capabledeputy.upstream.config import UpstreamAuthConfig
from capabledeputy.upstream.http_auth import (
    BearerTokenAuth,
    GoogleAdcAuth,
    OAuth2TokenAuth,
    discover_oauth2_endpoints,
    httpx_auth_from_config,
    oauth2_authorization_url,
)


def test_bearer_token_auth_adds_authorization_header() -> None:
    auth = BearerTokenAuth("tok")
    request = httpx.Request("GET", "https://example.test/mcp")
    flow = auth.sync_auth_flow(request)
    authed = next(flow)
    assert authed.headers["Authorization"] == "Bearer tok"


def test_bearer_auth_can_load_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_TEST_TOKEN", "envtok")
    auth = httpx_auth_from_config(UpstreamAuthConfig(type="bearer", token_env="CAPDEP_TEST_TOKEN"))
    assert isinstance(auth, BearerTokenAuth)


def test_bearer_auth_requires_token() -> None:
    with pytest.raises(ValueError, match="non-empty token"):
        httpx_auth_from_config(UpstreamAuthConfig(type="bearer"))


def test_google_adc_auth_config_builds_auth_object() -> None:
    auth = httpx_auth_from_config(
        UpstreamAuthConfig(
            type="google_adc",
            scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        )
    )
    assert isinstance(auth, GoogleAdcAuth)


def test_oauth2_auth_reads_access_token_from_cache(tmp_path) -> None:
    cache = tmp_path / "token.json"
    cache.write_text(
        json.dumps({"access_token": "cached-token", "expires_at": time.time() + 3600}),
        encoding="utf-8",
    )
    auth = httpx_auth_from_config(
        UpstreamAuthConfig(
            type="oauth2",
            client_id="client",
            authorization_url="https://auth.example/authorize",
            token_url="https://auth.example/token",
            token_cache=str(cache),
        ),
        server_name="slack",
    )
    assert isinstance(auth, OAuth2TokenAuth)

    request = httpx.Request("GET", "https://example.test/mcp")
    authed = next(auth.sync_auth_flow(request))

    assert authed.headers["Authorization"] == "Bearer cached-token"


def test_oauth2_auth_missing_cache_tells_operator_to_login(tmp_path) -> None:
    auth = httpx_auth_from_config(
        UpstreamAuthConfig(
            type="oauth2",
            client_id="client",
            authorization_url="https://auth.example/authorize",
            token_url="https://auth.example/token",
            token_cache=str(tmp_path / "missing.json"),
        ),
        server_name="github",
    )
    assert isinstance(auth, OAuth2TokenAuth)

    with pytest.raises(RuntimeError, match="capdep oauth login --server github"):
        next(auth.sync_auth_flow(httpx.Request("GET", "https://example.test/mcp")))


def test_oauth2_authorization_url_uses_pkce_scope_and_extra_params() -> None:
    config = UpstreamAuthConfig(
        type="oauth2",
        client_id="client",
        authorization_url="https://auth.example/authorize",
        token_url="https://auth.example/token",
        scopes=("scope:a", "scope:b"),
        resource="https://resource.example",
        extra_authorize_params={"access_type": "offline"},
    )

    url = oauth2_authorization_url(
        config,
        redirect_uri="http://127.0.0.1:8765/oauth/callback",
        state="state-1",
        code_challenge="challenge-1",
    )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.example"
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["client"]
    assert qs["scope"] == ["scope:a scope:b"]
    assert qs["state"] == ["state-1"]
    assert qs["code_challenge"] == ["challenge-1"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["resource"] == ["https://resource.example"]
    assert qs["access_type"] == ["offline"]


def test_oauth2_endpoint_discovery_supports_protected_resource_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **_kwargs) -> httpx.Response:
        if url == "https://resource.example/.well-known/oauth-protected-resource":
            return httpx.Response(
                200,
                json={"authorization_servers": ["https://auth.example/oauth"]},
                request=httpx.Request("GET", url),
            )
        if url == "https://auth.example/.well-known/oauth-authorization-server/oauth":
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://auth.example/oauth/authorize",
                    "token_endpoint": "https://auth.example/oauth/token",
                },
                request=httpx.Request("GET", url),
            )
        return httpx.Response(404, text="missing", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    endpoints = discover_oauth2_endpoints(
        UpstreamAuthConfig(
            type="oauth2",
            protected_resource_metadata_url=(
                "https://resource.example/.well-known/oauth-protected-resource"
            ),
        )
    )

    assert endpoints.authorization_url == "https://auth.example/oauth/authorize"
    assert endpoints.token_url == "https://auth.example/oauth/token"
