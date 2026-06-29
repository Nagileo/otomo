from __future__ import annotations

from otomo import config
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
