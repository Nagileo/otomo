from __future__ import annotations

import asyncio
import httpx
import pytest

from otomo import config
from otomo.api.admin import _asr_integration_status, _validate_bilibili_cookie_text
from otomo.auth import AuthStore
from otomo.bilibili_account import BilibiliCredentialStore, BilibiliQrLoginService, _netscape_cookie_text
from otomo.security_context import tenant_scope
from otomo.tools.release.qbittorrent import DownloaderPushRequest, check_qbittorrent, push_to_qbittorrent
from otomo.tools.videos import tool as videos_tool


COOKIE_TEXT = """# Netscape HTTP Cookie File
#HttpOnly_.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tsecret-session
.bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\tcsrf-value
.example.com\tTRUE\t/\tFALSE\t0\tforeign\tmust-not-leak
"""


def test_cookie_validation_accepts_httponly_sessdata_and_rejects_other_domains():
    _validate_bilibili_cookie_text(COOKIE_TEXT)
    with pytest.raises(Exception) as exc:
        _validate_bilibili_cookie_text(
            "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tnope\n"
        )
    assert getattr(exc.value, "status_code", None) == 422


def test_bilibili_account_loader_keeps_cookie_server_side(tmp_path, monkeypatch):
    account_path = tmp_path / "bilibili.sqlite3"
    auth = AuthStore(tmp_path / "auth")
    monkeypatch.setattr(config.settings, "bilibili_account_store_path", str(account_path))
    monkeypatch.setattr(config.settings, "auth_store_path", str(tmp_path / "auth" / "auth.sqlite3"))
    BilibiliCredentialStore(account_path, cipher=auth.cipher).save("alice", COOKIE_TEXT)
    seen: dict[str, object] = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers")
        request = httpx.Request("GET", str(url))
        return httpx.Response(
            200,
            request=request,
            json={"code": 0, "data": {"isLogin": True, "uname": "alice", "mid": 42}},
        )

    monkeypatch.setattr(videos_tool.httpx, "get", fake_get)
    with tenant_scope("alice", authenticated=True):
        status = videos_tool.verify_bilibili_account()
    assert status == {
        "configured": True,
        "authenticated": True,
        "username": "alice",
        "user_id": 42,
    }
    cookie_header = str((seen["headers"] or {}).get("Cookie"))
    assert "SESSDATA=secret-session" in cookie_header
    assert "bili_jct=csrf-value" in cookie_header
    assert "foreign" not in cookie_header
    assert "secret-session" not in str(status)
    assert b"secret-session" not in account_path.read_bytes()


def test_bilibili_accounts_are_isolated_by_bangumi_owner(tmp_path):
    store = BilibiliCredentialStore(tmp_path / "bili.sqlite3", cipher=AuthStore(tmp_path / "auth").cipher)
    store.save("alice", COOKIE_TEXT)
    bob_cookie = COOKIE_TEXT.replace("secret-session", "bob-secret")
    store.save("bob", bob_cookie)
    assert "secret-session" in store.get("alice")
    assert "bob-secret" not in store.get("alice")
    assert "bob-secret" in store.get("bob")


def test_netscape_cookie_export_requires_sessdata_and_never_returns_it_to_browser():
    cookies = httpx.Cookies()
    cookies.set("SESSDATA", "server-secret", domain=".bilibili.com", path="/")
    cookies.set("bili_jct", "csrf", domain=".bilibili.com", path="/")
    value = _netscape_cookie_text(cookies)
    assert "Netscape HTTP Cookie File" in value
    assert "SESSDATA\tserver-secret" in value
    with pytest.raises(RuntimeError):
        _netscape_cookie_text(httpx.Cookies())


def test_bilibili_qr_flow_is_owner_scoped_encrypted_and_cross_process_safe(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            request = httpx.Request("GET", url)
            if "generate" in url:
                return httpx.Response(
                    200,
                    request=request,
                    json={"code": 0, "data": {"qrcode_key": "qr-key", "url": "https://passport.bilibili.com/qr"}},
                )
            return httpx.Response(
                200,
                request=request,
                headers=[
                    ("set-cookie", "SESSDATA=server-secret; Domain=.bilibili.com; Path=/; HttpOnly; Secure"),
                    ("set-cookie", "bili_jct=csrf; Domain=.bilibili.com; Path=/"),
                ],
                json={"code": 0, "data": {"code": 0, "message": "ok"}},
            )

    monkeypatch.setattr("otomo.bilibili_account.httpx.AsyncClient", FakeClient)
    store = BilibiliCredentialStore(tmp_path / "bili.sqlite3", cipher=AuthStore(tmp_path / "auth").cipher)
    service = BilibiliQrLoginService(store)
    login = asyncio.run(service.start("alice"))
    assert login["status"] == "waiting"
    pending_bytes = (tmp_path / "bili.sqlite3").read_bytes()
    assert b"qr-key" not in pending_bytes
    assert b"passport.bilibili.com/qr" not in pending_bytes
    assert asyncio.run(service.poll("bob", login["login_id"]))["status"] == "expired"
    # Polling through another service instance models a different web worker.
    connected = asyncio.run(BilibiliQrLoginService(store).poll("alice", login["login_id"]))
    assert connected == {"status": "connected", "message": "B站登录态已加密保存到你的独立账号"}
    assert "server-secret" not in str(connected)
    saved = store.get("alice")
    assert "SESSDATA\tserver-secret" in saved
    assert b"server-secret" not in (tmp_path / "bili.sqlite3").read_bytes()


def test_bilibili_authenticated_memory_caches_are_partitioned_by_owner(monkeypatch):
    calls: list[str] = []

    def fake_headers(owner=None):
        return {"X-Test-Owner": str(owner or "public")}

    def fake_get(url, **kwargs):
        calls.append(str((kwargs.get("headers") or {}).get("X-Test-Owner")))
        return httpx.Response(
            200,
            request=httpx.Request("GET", str(url)),
            json={"code": 0, "data": {"replies": []}},
        )

    monkeypatch.setattr(videos_tool, "_bili_headers", fake_headers)
    monkeypatch.setattr(videos_tool.httpx, "get", fake_get)
    aid = 917_304_821
    videos_tool._sync_bili_replies(aid, 7, "alice")
    videos_tool._sync_bili_replies(aid, 7, "bob")
    videos_tool._sync_bili_replies(aid, 7, "alice")
    assert calls == ["alice", "bob"]


def test_qbittorrent_diagnostic_authenticates_without_adding_torrent(monkeypatch):
    monkeypatch.setattr(config.settings, "qbittorrent_url", "https://qbit.example.test")
    monkeypatch.setattr(config.settings, "qbittorrent_username", "alice")
    monkeypatch.setattr(config.settings, "qbittorrent_password", "secret")
    calls: list[str] = []
    client_headers: dict[str, str] = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            client_headers.update(kwargs.get("headers") or {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **_kwargs):
            calls.append(url)
            return httpx.Response(200, request=httpx.Request("POST", url), text="Ok.")

        async def get(self, url, **_kwargs):
            calls.append(url)
            value = "2.11.3" if url.endswith("webapiVersion") else "5.0.4"
            return httpx.Response(200, request=httpx.Request("GET", url), text=value)

    monkeypatch.setattr("otomo.tools.release.qbittorrent.httpx.AsyncClient", FakeClient)
    status = asyncio.run(check_qbittorrent())
    assert status["authenticated"] is True
    assert status["version"] == "5.0.4"
    assert status["web_api_version"] == "2.11.3"
    assert client_headers == {
        "Origin": "https://qbit.example.test",
        "Referer": "https://qbit.example.test/",
    }
    assert all("torrents/add" not in url for url in calls)


def test_qbittorrent_confirmed_push_uses_verified_session_and_expected_fields(monkeypatch):
    monkeypatch.setattr(config.settings, "qbittorrent_url", "https://qbit.example.test")
    monkeypatch.setattr(config.settings, "qbittorrent_username", "alice")
    monkeypatch.setattr(config.settings, "qbittorrent_password", "secret")
    monkeypatch.setattr(config.settings, "qbittorrent_category", "otomo")
    monkeypatch.setattr(config.settings, "qbittorrent_save_path", "")
    added: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            added["headers"] = kwargs.get("headers")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            if url.endswith("torrents/add"):
                added["data"] = kwargs.get("data")
            return httpx.Response(200, request=httpx.Request("POST", url), text="Ok.")

        async def get(self, url, **_kwargs):
            value = "2.11.3" if url.endswith("webapiVersion") else "5.0.4"
            return httpx.Response(200, request=httpx.Request("GET", url), text=value)

    monkeypatch.setattr("otomo.tools.release.qbittorrent.httpx.AsyncClient", FakeClient)
    result = asyncio.run(push_to_qbittorrent(DownloaderPushRequest(
        url="magnet:?xt=urn:btih:ABC123",
        paused=True,
    )))
    assert result["ok"] is True
    assert result["web_api_version"] == "2.11.3"
    assert added["headers"] == {
        "Origin": "https://qbit.example.test",
        "Referer": "https://qbit.example.test/",
    }
    assert added["data"] == {
        "urls": "magnet:?xt=urn:btih:ABC123",
        "paused": "true",
        "category": "otomo",
    }


def test_qbittorrent_rejects_local_or_malformed_torrent_references_before_login(monkeypatch):
    monkeypatch.setattr(config.settings, "qbittorrent_url", "https://qbit.example.test")
    monkeypatch.setattr(config.settings, "qbittorrent_username", "alice")
    monkeypatch.setattr(config.settings, "qbittorrent_password", "secret")
    with pytest.raises(ValueError, match="localhost|内网|保留"):
        asyncio.run(push_to_qbittorrent(DownloaderPushRequest(url="https://127.0.0.1/private.torrent")))
    with pytest.raises(ValueError, match="btih/btmh"):
        asyncio.run(push_to_qbittorrent(DownloaderPushRequest(url="magnet:?dn=missing-hash")))


def test_local_asr_health_reports_missing_runtime_instead_of_fake_green(monkeypatch):
    monkeypatch.setattr(config.settings, "asr_provider", "local")
    monkeypatch.setattr(
        "otomo.api.admin.importlib.util.find_spec",
        lambda name: None if name in {"yt_dlp", "faster_whisper"} else object(),
    )
    status = asyncio.run(_asr_integration_status())
    assert status["configured"] is True
    assert status["healthy"] is False
    assert status["provider"] == "local"
    assert "yt_dlp" in status["error"]
    assert status["execution"] == "final-candidates-only"
