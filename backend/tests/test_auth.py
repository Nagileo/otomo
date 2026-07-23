from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from otomo import config
from otomo.api.app import _request_client, app
from otomo.auth import AuthStore, BangumiToken, build_authorization_url
from otomo.share import CreateShareSnapshotRequest


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


def test_auth_store_encrypts_saved_tokens(tmp_path):
    store = AuthStore(tmp_path)
    store.save_token(
        BangumiToken(
            auth_session_id="sid",
            access_token="access-secret",
            refresh_token="refresh-secret",
            username="u",
        )
    )
    raw = store._read("bangumi_token", "sid")
    assert raw is not None
    assert "access_token" not in raw
    assert "refresh_token" not in raw
    assert raw["access_token_enc"].startswith("fernet:")
    assert raw["refresh_token_enc"].startswith("fernet:")
    loaded = store.load_token("sid")
    assert loaded is not None
    assert loaded.access_token == "access-secret"
    assert loaded.refresh_token == "refresh-secret"


def test_auth_session_roundtrip_and_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "session_ttl_seconds", 3600)
    store = AuthStore(tmp_path)
    session = store.get_or_create_session()
    assert session.auth_session_id
    assert session.csrf_token
    loaded = store.load_session(session.auth_session_id)
    assert loaded is not None
    assert loaded.csrf_token == session.csrf_token


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


def test_http_session_is_cookie_only_and_not_exposed(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "auth_store_path", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setattr(config.settings, "session_store_path", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.setattr(config.settings, "share_store_path", str(tmp_path / "shares.sqlite3"))
    monkeypatch.setattr(config.settings, "subscription_store_path", str(tmp_path / "subs.sqlite3"))
    monkeypatch.setattr(config.settings, "ltm_store_path", str(tmp_path / "ltm.sqlite3"))
    monkeypatch.setattr(config.settings, "quota_store_path", str(tmp_path / "quota.json"))
    monkeypatch.setattr(config.settings, "subscription_scheduler_enabled", False)

    with TestClient(app) as client:
        first_response = client.get("/auth/session")
        assert first_response.status_code == 200
        assert "auth_session_id" not in first_response.json()
        assert "httponly" in first_response.headers["set-cookie"].lower()
        first_cookie = client.cookies.get(config.settings.session_cookie_name)
        client.cookies.clear()
        client.get("/auth/session")
        second_cookie = client.cookies.get(config.settings.session_cookie_name)
        assert first_cookie and second_cookie and first_cookie != second_cookie

        # Legacy/body/query identifiers are not an identity input anymore.
        client.cookies.clear()
        client.cookies.set(
            config.settings.session_cookie_name,
            first_cookie,
            domain="testserver.local",
            path="/",
        )
        response = client.get(f"/auth/session?auth_session_id={second_cookie}")
        assert response.status_code == 200
        assert client.cookies.get(
            config.settings.session_cookie_name,
            domain="testserver.local",
            path="/",
        ) == first_cookie


def test_private_share_requires_owner_cookie(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "auth_store_path", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setattr(config.settings, "session_store_path", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.setattr(config.settings, "share_store_path", str(tmp_path / "shares.sqlite3"))
    monkeypatch.setattr(config.settings, "subscription_store_path", str(tmp_path / "subs.sqlite3"))
    monkeypatch.setattr(config.settings, "ltm_store_path", str(tmp_path / "ltm.sqlite3"))
    monkeypatch.setattr(config.settings, "quota_store_path", str(tmp_path / "quota.json"))
    monkeypatch.setattr(config.settings, "subscription_scheduler_enabled", False)

    with TestClient(app) as client:
        client.get("/auth/session")
        session_id = client.cookies.get(config.settings.session_cookie_name)
        assert session_id
        app.state.auth.save_token(
            BangumiToken(auth_session_id=session_id, access_token="token", username="alice")
        )
        private = app.state.share_store.create(
            CreateShareSnapshotRequest(
                type="subject_dossier",
                title="private",
                visibility="private_preview",
            ),
            owner_key="user:alice",
            created_by="alice",
        )
        public = app.state.share_store.create(
            CreateShareSnapshotRequest(type="subject_dossier", title="public"),
            owner_key="user:alice",
            created_by="alice",
        )

        own = client.get(f"/share/snapshots/{private.id}")
        assert own.status_code == 200
        assert "created_by" not in own.json()["snapshot"]
        client.cookies.clear()
        assert client.get(f"/share/snapshots/{private.id}").status_code == 404
        assert client.get(f"/share/snapshots/{public.id}").status_code == 200
