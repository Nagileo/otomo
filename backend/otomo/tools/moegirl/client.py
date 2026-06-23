"""手写的 thin 萌娘百科客户端（zh.moegirl.org.cn/api.php）。

红线（docs/02 §2）：只用白名单端点（opensearch + prop=extracts/info），**按需取 + 内存缓存、绝不入库**，
回答须挂可见来源链接、声明为摘要、不用于训练。礼貌：描述性 UA、低并发、5xx/网络重试。
"""
from __future__ import annotations

import asyncio
import time
import unicodedata
from typing import Any

import httpx

from ...config import settings

_RETRY_STATUS = {429, 500, 502, 503, 504}


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


class MoegirlClient:
    def __init__(
        self,
        api_base: str | None = None,
        user_agent: str | None = None,
        timeout: float | None = None,
        cache_ttl: float | None = None,
    ) -> None:
        self.api_base = api_base or settings.moegirl_api_base
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": user_agent or settings.moegirl_user_agent,
                "Accept": "application/json",
            },
            timeout=timeout or settings.http_timeout,
        )
        self._cache = _TTLCache(cache_ttl if cache_ttl is not None else settings.cache_ttl)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, params: dict[str, Any]) -> Any:
        key = str(sorted(params.items()))
        if (cached := self._cache.get(key)) is not None:
            return cached
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = await self._client.get(self.api_base, params=params)
                if r.status_code in _RETRY_STATUS:
                    last = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                self._cache.set(key, data)
                return data
            except httpx.TransportError as e:
                last = e
                await asyncio.sleep(0.5 * (attempt + 1))
        assert last is not None
        raise last

    async def opensearch(self, query: str, limit: int = 5) -> list[str]:
        """标题/别名补全（萌娘 API 无全文搜索，只能按标题）。返回候选标题列表。"""
        q = unicodedata.normalize("NFC", query)
        data = await self._get(
            {"action": "opensearch", "search": q, "limit": limit, "format": "json"}
        )
        # 形如 [query, [titles], [descs], [urls]]
        if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
            return data[1]
        return []

    async def extract(self, title: str, intro_only: bool = False) -> dict[str, Any] | None:
        """取页面纯文本正文（已剥模板/ref），含 fullurl/lastrevid。intro_only=True 只取导言。"""
        t = unicodedata.normalize("NFC", title)
        params: dict[str, Any] = {
            "action": "query",
            "prop": "extracts|info",
            "inprop": "url",
            "explaintext": 1,
            "exsectionformat": "plain",
            "redirects": 1,
            "titles": t,
            "format": "json",
        }
        if intro_only:
            params["exintro"] = 1
        data = await self._get(params)
        pages = (data.get("query") or {}).get("pages") or {}
        for _pid, page in pages.items():
            if "missing" in page:
                return None
            return {
                "pageid": page.get("pageid"),
                "title": page.get("title", t),
                "extract": page.get("extract", "") or "",
                "fullurl": page.get("fullurl") or page.get("canonicalurl"),
                "lastrevid": page.get("lastrevid"),
            }
        return None
