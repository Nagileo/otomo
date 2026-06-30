from __future__ import annotations

import asyncio
from types import SimpleNamespace

from otomo import config
from otomo.api.app import _request_client
from otomo.auth import AuthStore, BangumiToken, build_authorization_url


def test_auth_store_oauth_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "bangumi_oauth_client_id", "app-id")
    monkeypatch.setattr(config.settings, "bangumi_oauth_redirect_uri", "http://localhost/cb")
    store = AuthStore(tmp_path)

    url = build_authorization_url(store, "auth-session")
    assert "https://bgm.tv/oauth/authorize" in url
    assert "client_id=app-id" in url
    assert "state=" in url

    state_value = url.split("state=", 1)[1]
    state = store.consume_oauth_state(state_value)
    assert state.auth_session_id == "auth-session"


def test_auth_identity_from_saved_token(tmp_path):
    store = AuthStore(tmp_path)
    store.save_token(
        BangumiToken(
            auth_session_id="sid",
            access_token="token",
            refresh_token="refresh",
            user_id=123,
            username="Nagileo",
        )
    )
    identity = store.identity("sid")
    assert identity.authenticated
    assert identity.username == "Nagileo"
    assert identity.user_id == 123


def test_auth_logout_deletes_token(tmp_path):
    store = AuthStore(tmp_path)
    store.save_token(BangumiToken(auth_session_id="sid", access_token="token", username="u"))
    store.delete_token("sid")
    assert not store.identity("sid").authenticated


def test_request_client_with_web_session_does_not_use_global_token(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "bangumi_token", "developer-global-token")
    app = SimpleNamespace(state=SimpleNamespace(auth=AuthStore(tmp_path)))

    client = asyncio.run(_request_client(app, "browser-session"))
    try:
        assert client._client.headers.get("Authorization") is None
    finally:
        asyncio.run(client.aclose())


def test_request_client_without_web_session_keeps_global_token(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "bangumi_token", "developer-global-token")
    app = SimpleNamespace(state=SimpleNamespace(auth=AuthStore(tmp_path)))

    client = asyncio.run(_request_client(app, None))
    try:
        assert client._client.headers.get("Authorization") == "Bearer developer-global-token"
    finally:
        asyncio.run(client.aclose())
