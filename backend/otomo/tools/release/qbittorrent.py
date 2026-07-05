"""qBittorrent Web API helper for explicit, confirmed downloader pushes."""
from __future__ import annotations

from dataclasses import dataclass

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
    return bool(settings.qbittorrent_url.strip() and settings.qbittorrent_username.strip())


def downloader_config_error() -> str:
    if settings.qbittorrent_url.strip():
        return ""
    return "qBittorrent 未配置：需要 QBITTORRENT_URL / QBITTORRENT_USERNAME / QBITTORRENT_PASSWORD。"


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
    async def _post() -> dict:
        async with httpx.AsyncClient(timeout=settings.release_feed_timeout) as client:
            login = await client.post(
                f"{base}/api/v2/auth/login",
                data={
                    "username": settings.qbittorrent_username,
                    "password": settings.qbittorrent_password,
                },
            )
            login.raise_for_status()
            if login.text.strip().lower() not in {"ok.", "ok"}:
                raise RuntimeError("qBittorrent 登录失败，请检查账号密码或 WebUI 设置")
            data = {
                "urls": req.url.strip(),
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
            }

    result = await gather_limited([_post()], host="qbittorrent")
    first = result[0]
    if isinstance(first, BaseException):
        raise first
    return first
