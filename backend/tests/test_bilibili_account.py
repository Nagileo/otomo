from __future__ import annotations

import asyncio
import httpx
import pytest

from otomo import config
from otomo.api.admin import _asr_integration_status, _validate_bilibili_cookie_text
from otomo.bilibili_account import BilibiliQrLoginService, _netscape_cookie_text
from otomo.tools.release.qbittorrent import check_qbittorrent
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
    cookie_path = tmp_path / "bilibili_cookies.txt"
    cookie_path.write_text(COOKIE_TEXT, encoding="utf-8")
    monkeypatch.setattr(config.settings, "bilibili_cookies_file", str(cookie_path))
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


def test_netscape_cookie_export_requires_sessdata_and_never_returns_it_to_browser():
    cookies = httpx.Cookies()
    cookies.set("SESSDATA", "server-secret", domain=".bilibili.com", path="/")
    cookies.set("bili_jct", "csrf", domain=".bilibili.com", path="/")
    value = _netscape_cookie_text(cookies)
    assert "Netscape HTTP Cookie File" in value
    assert "SESSDATA\tserver-secret" in value
    with pytest.raises(RuntimeError):
        _netscape_cookie_text(httpx.Cookies())


def test_bilibili_qr_flow_is_owner_scoped_and_writes_server_cookie_file(tmp_path, monkeypatch):
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
    target = tmp_path / "bili.txt"
    service = BilibiliQrLoginService(str(target))
    login = asyncio.run(service.start("alice"))
    assert login["status"] == "waiting"
    assert asyncio.run(service.poll("bob", login["login_id"]))["status"] == "expired"
    connected = asyncio.run(service.poll("alice", login["login_id"]))
    assert connected == {"status": "connected", "message": "B站登录态已安全保存到服务器"}
    assert "server-secret" not in str(connected)
    saved = target.read_text(encoding="utf-8")
    assert "SESSDATA\tserver-secret" in saved


def test_qbittorrent_diagnostic_authenticates_without_adding_torrent(monkeypatch):
    monkeypatch.setattr(config.settings, "qbittorrent_url", "https://qbit.example.test")
    monkeypatch.setattr(config.settings, "qbittorrent_username", "alice")
    monkeypatch.setattr(config.settings, "qbittorrent_password", "secret")
    calls: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **_kwargs):
            calls.append(url)
            return httpx.Response(200, request=httpx.Request("POST", url), text="Ok.")

        async def get(self, url, **_kwargs):
            calls.append(url)
            return httpx.Response(200, request=httpx.Request("GET", url), text="5.0.4")

    monkeypatch.setattr("otomo.tools.release.qbittorrent.httpx.AsyncClient", FakeClient)
    status = asyncio.run(check_qbittorrent())
    assert status["authenticated"] is True
    assert status["version"] == "5.0.4"
    assert all("torrents/add" not in url for url in calls)


def test_local_asr_health_is_explicit_and_does_not_fake_worker_checks(monkeypatch):
    monkeypatch.setattr(config.settings, "asr_provider", "local")
    status = asyncio.run(_asr_integration_status())
    assert status["configured"] is True
    assert status["healthy"] is True
    assert status["provider"] == "local"
