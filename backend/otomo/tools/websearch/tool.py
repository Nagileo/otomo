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

# 降级兜底顺序（免费/便宜优先）：首选引擎失败或配额满时按此顺序往下试
_FALLBACK_ORDER = ["tavily", "serper", "bocha", "exa"]


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

    def __init__(self, provider: str | None = None) -> None:
        self.primary = (provider or settings.websearch_provider).lower()
        self.quality = settings.websearch_quality_provider.lower()

    def _chain(self, high_quality: bool) -> list[str]:
        """降级链：首选引擎在前，其余按兜底顺序在后，只保留已配 key 的。"""
        preferred = self.quality if high_quality else self.primary
        order = [preferred] + [p for p in _FALLBACK_ORDER if p != preferred]
        return [p for p in dict.fromkeys(order) if settings.websearch_key(p)]

    async def run(self, args: WebSearchArgs) -> ToolResult[WebSearchResult]:
        chain = self._chain(args.high_quality)
        if not chain:
            return ToolResult(ok=False, error="未配置任何搜索 key：在 .env 设 WEBSEARCH_<ENGINE>_KEY")
        last = ""
        for provider in chain:  # 逐个尝试；报错(含 403 配额满)或空结果就降级到下一个
            try:
                hits = await _search(provider, settings.websearch_key(provider), args.query, args.max_results, settings.http_timeout)
            except Exception as e:  # noqa: BLE001
                last = f"{provider}: {type(e).__name__}"
                continue
            if hits:
                return ToolResult(
                    ok=True,
                    data=WebSearchResult(query=args.query, provider=provider, hits=[WebHit(**h) for h in hits]),
                    sources=[Citation(title=(h["title"] or h["url"])[:60], url=h["url"], source="web") for h in hits if h.get("url")],
                )
        return ToolResult(ok=False, error=f"全网搜索均无结果或失败（{last}）")


def build_websearch_tools() -> list[Tool]:
    return [WebSearchTool()]
