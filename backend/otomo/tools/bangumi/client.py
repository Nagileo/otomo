"""手写的 thin async Bangumi 客户端（不接 Bangumi-MCP/bgm-cli）。

要点（见 docs/02-data-sources）：
- **强制 User-Agent**，通用 UA 会被拒。
- 读接口免 token；带 token 可解锁 R18 与用户私有数据。
- A1 用进程内 TTL 缓存做"礼貌限流"占位，A5 换 Redis。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ...config import settings

SUBJECT_TYPE = {"book": 1, "anime": 2, "music": 3, "game": 4, "real": 6}
_RETRY_STATUS = {500, 502, 503, 504}  # Bangumi 网关偶发 5xx，需重试


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
    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retries: int = 3,
    ) -> Any:
        """带 5xx / 网络错误重试（指数退避）。4xx 直接抛。"""
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                r = await self._client.request(method, path, params=params, json=json_body)
                if r.status_code in _RETRY_STATUS:
                    last_exc = httpx.HTTPStatusError(
                        f"{r.status_code} from {path}", request=r.request, response=r
                    )
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.TransportError as e:  # 连接/超时类
                last_exc = e
                await asyncio.sleep(0.5 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        key = f"GET {path} {sorted((params or {}).items())}"
        if (cached := self._cache.get(key)) is not None:
            return cached
        data = await self._request_json("GET", path, params=params)
        self._cache.set(key, data)
        return data

    async def _post(self, path: str, json_body: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
        key = f"POST {path} {params} {json_body}"
        if (cached := self._cache.get(key)) is not None:
            return cached
        data = await self._request_json("POST", path, params=params, json_body=json_body)
        self._cache.set(key, data)
        return data

    # ---- v0 端点 ---- #
    async def search_subjects(
        self,
        keyword: str = "",
        subject_type: int | None = None,
        sort: str = "match",
        limit: int = 10,
        tags: list[str] | None = None,
        offset: int = 0,
    ) -> Any:
        body: dict[str, Any] = {"keyword": keyword, "sort": sort}
        filt: dict[str, Any] = {}
        if subject_type:
            filt["type"] = [subject_type]
        if tags:
            filt["tag"] = tags
        if filt:
            body["filter"] = filt
        return await self._post(
            "/v0/search/subjects", body, params={"limit": min(limit, 50), "offset": offset}
        )

    async def get_subject(self, subject_id: int) -> Any:
        return await self._get(f"/v0/subjects/{subject_id}")

    async def get_subject_characters(self, subject_id: int) -> Any:
        return await self._get(f"/v0/subjects/{subject_id}/characters")

    async def get_subject_persons(self, subject_id: int) -> Any:
        """该作品的 staff（导演/脚本/原作/动画制作公司等），每条带 relation=职责。"""
        return await self._get(f"/v0/subjects/{subject_id}/persons")

    async def get_subject_relations(self, subject_id: int) -> Any:
        """该作品的**关联条目（跨媒体）**：改编/原作/续集/不同演绎等，可跨 type
        （galgame↔动画↔小说↔音乐）。每条带 relation=关系、type=条目类型。"""
        return await self._get(f"/v0/subjects/{subject_id}/subjects")

    async def get_episodes(
        self, subject_id: int, ep_type: int | None = None, limit: int = 100, offset: int = 0
    ) -> Any:
        """作品分集列表：每集 id(ep_id)/sort(全局序)/ep(本类型内集号)/type/airdate/name/comment(讨论数)。
        ep_type: 0 正片 / 1 SP / 2 OP / 3 ED / 4 预告等（不传=全部）。"""
        params: dict[str, Any] = {"subject_id": subject_id, "limit": min(limit, 200), "offset": offset}
        if ep_type is not None:
            params["type"] = ep_type
        return await self._get("/v0/episodes", params)

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

    async def get_me(self) -> Any:
        """用 token 取当前用户信息（含 username）。需要 BANGUMI_TOKEN。"""
        return await self._get("/v0/me")

    async def get_user_collections(
        self, username: str, subject_type: int = 2, collection_type: int | None = 2,
        limit: int = 50, offset: int = 0,
    ) -> Any:
        """用户收藏。subject_type:2=动画；collection_type:1想看/2看过/3在看/4搁置/5抛弃（None=全部）。公开收藏免 token。"""
        params: dict[str, Any] = {"subject_type": subject_type, "limit": min(limit, 50), "offset": offset}
        if collection_type is not None:
            params["type"] = collection_type
        return await self._get(f"/v0/users/{username}/collections", params)

    async def get_all_user_collections(
        self, username: str, subject_type: int = 2, collection_type: int | None = None,
        max_items: int = 300,
    ) -> list[dict]:
        """分页拉取收藏（默认全部状态），用于口味聚合 / 排除已看。"""
        items: list[dict] = []
        offset = 0
        while len(items) < max_items:
            page = await self.get_user_collections(username, subject_type, collection_type, 50, offset)
            batch = page.get("data") or []
            items.extend(batch)
            if len(batch) < 50:
                break
            offset += 50
        return items[:max_items]
