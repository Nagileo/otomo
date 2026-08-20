"""qBittorrent Web API helper for explicit, confirmed downloader pushes."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from ...config import settings
from .._concurrency import gather_limited


@dataclass
class DownloaderPushRequest:
    url: str
    category: str = ""
    save_path: str = ""
    paused: bool = False


def downloader_enabled() -> bool:
    return not downloader_config_error()


def downloader_config_error() -> str:
    missing = []
    raw_url = settings.qbittorrent_url.strip()
    if not raw_url:
        missing.append("QBITTORRENT_URL")
    elif (parsed := urlparse(raw_url)).scheme not in {"http", "https"} or not parsed.hostname:
        return "qBittorrent 配置错误：QBITTORRENT_URL 必须是有效的 http(s) WebUI 地址"
    if not settings.qbittorrent_username.strip():
        missing.append("QBITTORRENT_USERNAME")
    if not settings.qbittorrent_password:
        missing.append("QBITTORRENT_PASSWORD")
    return "qBittorrent 未配置：缺少 " + " / ".join(missing) if missing else ""


def downloader_public_status() -> dict:
    parsed = urlparse(settings.qbittorrent_url.strip())
    return {
        "configured": downloader_enabled() and not downloader_config_error(),
        "host": parsed.hostname or "",
        "scheme": parsed.scheme or "",
        "category": settings.qbittorrent_category,
        "save_path_configured": bool(settings.qbittorrent_save_path),
        "error": downloader_config_error(),
    }


def _browser_origin_headers(base: str) -> dict[str, str]:
    """qBittorrent 5.x rejects login without a same-host Origin/Referer."""
    parsed = urlparse(base)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return {"Origin": origin, "Referer": f"{base.rstrip('/')}/"}


def _valid_web_api_version(value: str) -> bool:
    try:
        major = int(value.strip().split(".", 1)[0])
    except (TypeError, ValueError):
        return False
    return major >= 2


async def _validate_torrent_reference(value: str) -> str:
    reference = value.strip()
    parsed = urlparse(reference)
    if parsed.scheme == "magnet":
        if "xt=urn:btih:" not in reference.lower() and "xt=urn:btmh:" not in reference.lower():
            raise ValueError("magnet 缺少 btih/btmh 内容标识")
        return reference
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("下载器只接受公网 HTTPS 种子 URL 或有效 magnet 链接")
    host = parsed.hostname.rstrip(".").lower()
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        loop = asyncio.get_running_loop()
        infos = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM),
        )
        addresses = list({ipaddress.ip_address(info[4][0]) for info in infos})
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("下载器种子 URL 不能指向 localhost、内网或保留地址")
    return reference


async def _login_and_versions(client: httpx.AsyncClient, base: str) -> tuple[str, str]:
    login = await client.post(
        f"{base}/api/v2/auth/login",
        data={"username": settings.qbittorrent_username, "password": settings.qbittorrent_password},
    )
    login.raise_for_status()
    if login.text.strip().lower() not in {"ok.", "ok"}:
        raise RuntimeError("qBittorrent 登录失败，请检查账号密码、WebUI Host 白名单与来源校验")
    version = await client.get(f"{base}/api/v2/app/version")
    version.raise_for_status()
    web_api = await client.get(f"{base}/api/v2/app/webapiVersion")
    web_api.raise_for_status()
    web_api_version = web_api.text.strip()[:40]
    if not _valid_web_api_version(web_api_version):
        raise RuntimeError(f"qBittorrent Web API 版本不可识别或过旧：{web_api_version or 'empty'}")
    return version.text.strip()[:40], web_api_version


async def check_qbittorrent() -> dict:
    """Authenticate and read the Web API version without creating a task."""
    status = downloader_public_status()
    if not status["configured"]:
        return {**status, "reachable": False, "authenticated": False}
    base = settings.qbittorrent_url.strip().rstrip("/")
    try:
        async with httpx.AsyncClient(
            timeout=min(settings.release_feed_timeout, 8),
            headers=_browser_origin_headers(base),
        ) as client:
            version, web_api_version = await _login_and_versions(client, base)
        return {
            **status,
            "reachable": True,
            "authenticated": True,
            "version": version,
            "web_api_version": web_api_version,
            "error": "",
        }
    except RuntimeError as exc:
        return {
            **status,
            "reachable": True,
            "authenticated": False,
            "error": str(exc)[:180],
        }
    except httpx.HTTPStatusError as exc:
        return {
            **status,
            "reachable": True,
            "authenticated": False,
            "error": f"HTTP {exc.response.status_code}: {str(exc)[:140]}",
        }
    except httpx.HTTPError as exc:
        return {
            **status,
            "reachable": False,
            "authenticated": False,
            "error": f"{type(exc).__name__}: {str(exc)[:160]}",
        }


async def push_to_qbittorrent(req: DownloaderPushRequest) -> dict:
    """Push a torrent URL or magnet link to qBittorrent.

    This function performs the write. Call it only from an explicit confirmation
    path, never from a model-visible read tool.
    """
    if not downloader_enabled():
        msg = downloader_config_error() or "qBittorrent 用户名未配置。"
        raise RuntimeError(msg)
    base = settings.qbittorrent_url.strip().rstrip("/")
    if not req.url.strip():
        raise ValueError("缺少 torrent_url/magnet")
    reference = await _validate_torrent_reference(req.url)
    async def _post() -> dict:
        async with httpx.AsyncClient(
            timeout=settings.release_feed_timeout,
            headers=_browser_origin_headers(base),
        ) as client:
            version, web_api_version = await _login_and_versions(client, base)
            data = {
                "urls": reference,
                "paused": "true" if req.paused else "false",
            }
            if req.category or settings.qbittorrent_category:
                data["category"] = req.category or settings.qbittorrent_category
            if req.save_path or settings.qbittorrent_save_path:
                data["savepath"] = req.save_path or settings.qbittorrent_save_path
            add = await client.post(f"{base}/api/v2/torrents/add", data=data)
            add.raise_for_status()
            return {
                "ok": add.text.strip().lower() in {"ok.", "ok", ""},
                "status_code": add.status_code,
                "response": add.text[:200],
                "category": data.get("category", ""),
                "save_path": data.get("savepath", ""),
                "version": version,
                "web_api_version": web_api_version,
            }

    result = await gather_limited([_post()], host="qbittorrent")
    first = result[0]
    if isinstance(first, BaseException):
        raise first
    return first
