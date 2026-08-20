from __future__ import annotations

import httpx
import pytest

from otomo import config
from otomo.api.admin import _validate_bilibili_cookie_text
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
