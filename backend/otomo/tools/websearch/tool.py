"""全网搜索工具（外部知识增强，**补充非主体**）。

定位（见 docs/04 外部知识增强）：graph+wiki 答不了（最新资讯、粉丝话语、跨源综述）时的兜底。
provider 可换（Tavily/Exa/Serper），无 key 时优雅报"未配置"。结果**标 web 来源、低置信、必挂链接**，
不与 Bangumi 可验证事实混淆。每次调用临时建 httpx，无需管理生命周期。
"""
from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings


class WebSearchArgs(BaseModel):
    query: str = Field(..., description="搜索词；查最新资讯/粉丝讨论/跨源信息时用")
    max_results: int = Field(5, ge=1, le=10)
    high_quality: bool = Field(
        False, description="需要更高质量/中文粉丝话语/深度综述时设 true，升级到更强引擎；普通查询留 false（免费引擎）"
    )


class WebHit(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    url: str
    snippet: str = ""


class WebSearchResult(BaseModel):
    query: str
    provider: str
    hits: list[WebHit] = Field(default_factory=list)


async def _search(provider: str, api_key: str, query: str, n: int, timeout: float) -> list[dict]:
    async with httpx.AsyncClient(timeout=timeout) as c:
        if provider == "tavily":
            r = await c.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": n, "search_depth": "basic"},
            )
            r.raise_for_status()
            return [
                {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": (x.get("content") or "")[:300]}
                for x in (r.json().get("results") or [])
            ]
        if provider == "exa":
            r = await c.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": api_key},
                json={"query": query, "numResults": n, "contents": {"text": {"maxCharacters": 300}}},
            )
            r.raise_for_status()
            return [
                {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": (x.get("text") or "")[:300]}
                for x in (r.json().get("results") or [])
            ]
        if provider == "serper":
            r = await c.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key},
                json={"q": query, "num": n},
            )
            r.raise_for_status()
            return [
                {"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")}
                for x in (r.json().get("organic") or [])
            ]
        if provider == "bocha":
            r = await c.post(
                "https://api.bochaai.com/v1/web-search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"query": query, "summary": True, "count": n},
            )
            r.raise_for_status()
            pages = ((r.json().get("data") or {}).get("webPages") or {}).get("value") or []
            return [
                {"title": x.get("name", ""), "url": x.get("url", ""),
                 "snippet": (x.get("summary") or x.get("snippet") or "")[:300]}
                for x in pages
            ]
        raise ValueError(f"未知 websearch provider: {provider}")


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "全网搜索兜底：当 Bangumi 图谱与萌娘/维基都答不了（最新资讯、粉丝讨论/二创氛围、跨源综述）时用。"
        "结果是**网络来源、可能不准**——作答时必须挂链接、说明是网络信息、别与已验证事实混为一谈。"
    )
    args_model = WebSearchArgs
    result_model = WebSearchResult

    def __init__(self, provider: str | None = None, api_key: str | None = None) -> None:
        self.primary = (provider or settings.websearch_provider).lower()
        self.quality = settings.websearch_quality_provider.lower()
        self._forced_key = api_key  # 测试可强制指定

    async def run(self, args: WebSearchArgs) -> ToolResult[WebSearchResult]:
        provider = self.quality if args.high_quality else self.primary
        key = self._forced_key if self._forced_key is not None else settings.websearch_key(provider)
        if not key:  # 升级引擎没配 key → 回退主引擎
            provider = self.primary
            key = self._forced_key if self._forced_key is not None else settings.websearch_key(provider)
        if not key:
            return ToolResult(
                ok=False,
                error="未配置搜索 API key：在 .env 设 WEBSEARCH_PROVIDER 与对应的 WEBSEARCH_<ENGINE>_KEY",
            )
        hits = await _search(provider, key, args.query, args.max_results, settings.http_timeout)
        return ToolResult(
            ok=True,
            data=WebSearchResult(query=args.query, provider=provider, hits=[WebHit(**h) for h in hits]),
            sources=[Citation(title=(h["title"] or h["url"])[:60], url=h["url"], source="web") for h in hits if h.get("url")],
        )


def build_websearch_tools() -> list[Tool]:
    return [WebSearchTool()]
