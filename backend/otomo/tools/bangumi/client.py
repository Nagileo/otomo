"""手写的 thin async Bangumi 客户端（不接 Bangumi-MCP/bgm-cli）。

要点（见 docs/02-data-sources）：
- **强制 User-Agent**，通用 UA 会被拒。
- 读接口免 token；带 token 可解锁 R18 与用户私有数据。
- A1 用进程内 TTL 缓存做"礼貌限流"占位，A5 换 Redis。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from ...config import settings

SUBJECT_TYPE = {"book": 1, "anime": 2, "music": 3, "game": 4, "real": 6}


class _TTLCache:
    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        hit = self._store.get(key)
        if not hit:
            return None
        ts, val = hit
        if time.monotonic() - ts > self.ttl:
            self._store.pop(key, None)
            return None
        return val

    def set(self, key: str, val: Any) -> None:
        self._store[key] = (time.monotonic(), val)


class BangumiClient:
    def __init__(
        self,
        base_url: str | None = None,
        user_agent: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
        cache_ttl: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.bangumi_api_base).rstrip("/")
        headers = {
            "User-Agent": user_agent or settings.bangumi_user_agent,
            "Accept": "application/json",
        }
        token = token if token is not None else settings.bangumi_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout or settings.http_timeout,
        )
        self._cache = _TTLCache(cache_ttl if cache_ttl is not None else settings.cache_ttl)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "BangumiClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ---- 底层 ---- #
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        key = f"GET {path} {sorted((params or {}).items())}"
        if (cached := self._cache.get(key)) is not None:
            return cached
        r = await self._client.get(path, params=params)
        r.raise_for_status()
        data = r.json()
        self._cache.set(key, data)
        return data

    async def _post(self, path: str, json_body: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
        key = f"POST {path} {params} {json_body}"
        if (cached := self._cache.get(key)) is not None:
            return cached
        r = await self._client.post(path, json=json_body, params=params)
        r.raise_for_status()
        data = r.json()
        self._cache.set(key, data)
        return data

    # ---- v0 端点 ---- #
    async def search_subjects(
        self,
        keyword: str,
        subject_type: int | None = None,
        sort: str = "match",
        limit: int = 10,
    ) -> Any:
        body: dict[str, Any] = {"keyword": keyword, "sort": sort}
        if subject_type:
            body["filter"] = {"type": [subject_type]}
        return await self._post("/v0/search/subjects", body, params={"limit": min(limit, 50)})

    async def get_subject(self, subject_id: int) -> Any:
        return await self._get(f"/v0/subjects/{subject_id}")

    async def get_subject_characters(self, subject_id: int) -> Any:
        return await self._get(f"/v0/subjects/{subject_id}/characters")

    async def search_characters(self, keyword: str, limit: int = 10) -> Any:
        return await self._post(
            "/v0/search/characters", {"keyword": keyword}, params={"limit": min(limit, 50)}
        )

    async def get_character_persons(self, character_id: int) -> Any:
        """该角色的 CV（声优）/出演者，每条带 subject 上下文。"""
        return await self._get(f"/v0/characters/{character_id}/persons")

    async def search_persons(self, keyword: str, limit: int = 10) -> Any:
        return await self._post(
            "/v0/search/persons", {"keyword": keyword}, params={"limit": min(limit, 50)}
        )

    async def get_person_subjects(self, person_id: int) -> Any:
        """该人物（声优/staff）参与的作品。"""
        return await self._get(f"/v0/persons/{person_id}/subjects")
