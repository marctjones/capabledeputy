from __future__ import annotations

import httpx
import pytest

from capabledeputy.upstream.config import UpstreamAuthConfig
from capabledeputy.upstream.http_auth import BearerTokenAuth, GoogleAdcAuth, httpx_auth_from_config


def test_bearer_token_auth_adds_authorization_header() -> None:
    auth = BearerTokenAuth("tok")
    request = httpx.Request("GET", "https://example.test/mcp")
    flow = auth.sync_auth_flow(request)
    authed = next(flow)
    assert authed.headers["Authorization"] == "Bearer tok"


def test_bearer_auth_can_load_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_TEST_TOKEN", "envtok")
    auth = httpx_auth_from_config(
        UpstreamAuthConfig(type="bearer", token_env="CAPDEP_TEST_TOKEN")
    )
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
