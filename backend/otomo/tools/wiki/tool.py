"""中文维基百科 RAG 工具（关系/剧情多源强化）。

相比萌娘：维基有**全文搜索**（list=search），能按内容找页面（不止标题），且 CC BY-SA license 干净。
补萌娘没有/不够的关系、剧情、背景。每次调用临时 httpx，无生命周期管理。
"""
from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from .._rag import chunk_text, rank_chunks


class WikiArgs(BaseModel):
    query: str = Field(..., description="想了解的作品/角色/关系/剧情问题（维基支持全文搜索）")


class WikiResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    found: bool
    snippets: list[str] = Field(default_factory=list)


async def _get(client: httpx.AsyncClient, params: dict[str, Any]) -> Any:
    r = await client.get(settings.wiki_api_base, params=params)
    r.raise_for_status()
    return r.json()


class WikiSearchTool(Tool):
    name = "wiki_search"
    description = (
        "从中文维基百科检索作品/角色的关系、剧情、背景等（维基支持全文搜索、来源权威）。"
        "与 lore_search（萌娘）互补：维基更中性/有全文搜索，萌娘更全梗/设定。返回片段并附来源。"
    )
    args_model = WikiArgs
    result_model = WikiResult

    async def run(self, args: WikiArgs) -> ToolResult[WikiResult]:
        try:
            return await self._run(args)
        except (httpx.HTTPError, httpx.TransportError):
            # 中国大陆通常直连不通（GFW）→ 优雅降级，提示改用 web_search
            return ToolResult(
                ok=False,
                error="维基百科不可达（中国大陆通常需代理）；请改用 web_search（其结果也覆盖维基内容）或 lore_search（萌娘）。",
            )

    async def _run(self, args: WikiArgs) -> ToolResult[WikiResult]:
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.wiki_user_agent, "Accept": "application/json"},
            timeout=settings.http_timeout,
        ) as c:
            sr = await _get(c, {
                "action": "query", "list": "search", "srsearch": args.query,
                "srlimit": 1, "format": "json",
            })
            results = (sr.get("query") or {}).get("search") or []
            if not results:
                return ToolResult(ok=True, data=WikiResult(title="", found=False))
            title = results[0]["title"]
            ex = await _get(c, {
                "action": "query", "prop": "extracts|info", "inprop": "url",
                "explaintext": 1, "exsectionformat": "plain", "redirects": 1,
                "titles": title, "format": "json",
            })
            pages = (ex.get("query") or {}).get("pages") or {}
            page = next(iter(pages.values()), {})
            extract = page.get("extract") or ""
            if not extract:
                return ToolResult(ok=True, data=WikiResult(title=title, found=False))
            snippets = rank_chunks(args.query, chunk_text(extract))
            cite = Citation(
                title=f"维基百科 — {page.get('title', title)}",
                url=page.get("fullurl") or page.get("canonicalurl") or "",
                source="wikipedia",
            )
            return ToolResult(
                ok=True,
                data=WikiResult(title=page.get("title", title), found=True, snippets=snippets),
                sources=[cite],
            )


def build_wiki_tools() -> list[Tool]:
    return [WikiSearchTool()]
