"""AniList（英文圈动画/漫画库）查询——Source Router 的 Canonical 兜底添头。

GraphQL API（graphql.anilist.co），无 token，评分满分 100。
**关键约束：中文名搜不到，须用日文原名 / 英文 / 罗马音**（可先用 search_subjects 拿 Bangumi 的 name 日文原名再来搜）。
定位：主源是 Bangumi，这里作"查不到再补英文圈数据/别名/评分"的兜底，主体不动摇。
"""
from __future__ import annotations

import asyncio
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings

_ANILIST_API = "https://graphql.anilist.co"
_MEDIA_FIELDS = "id title{romaji native english} averageScore seasonYear format episodes"
_QUERY = (
    "query($s:String,$t:MediaType){Page(perPage:%d){media(search:$s,type:$t)"
    "{" + _MEDIA_FIELDS + "}}}"
)
_BATCH_SIZE = 8


class AniListArgs(BaseModel):
    keyword: str = Field(
        ..., description="作品名——**用日文原名 / 英文 / 罗马音**搜（中文名搜不到；可先 search_subjects 拿 Bangumi 的 name）"
    )
    type: Literal["anime", "manga"] = "anime"
    limit: int = Field(5, ge=1, le=10)


class AniListMedia(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    title_romaji: str = ""
    title_native: str = ""
    title_english: str | None = None
    score: int | None = None   # 满分 100
    year: int | None = None
    format: str | None = None
    episodes: int | None = None


class AniListResult(BaseModel):
    query: str
    count: int
    results: list[AniListMedia] = Field(default_factory=list)


class SearchAniListTool(Tool):
    name = "search_anilist"
    description = (
        "在 AniList（英文圈动画/漫画库）搜作品，拿 canonical 评分（满分 100）/ 年份 / 集数 / 别名。"
        "Bangumi 查不到、或想要英文圈评分/别名时的**兜底**。"
        "**用日文原名或英文名搜，中文名搜不到**（可先 search_subjects 拿日文 name 再来搜）。引用注明 AniList（满分100）。"
    )
    args_model = AniListArgs
    result_model = AniListResult

    @staticmethod
    def _result(args: AniListArgs, media: list[dict]) -> ToolResult[AniListResult]:
        items = [
            AniListMedia(
                id=m["id"],
                title_romaji=(m.get("title") or {}).get("romaji") or "",
                title_native=(m.get("title") or {}).get("native") or "",
                title_english=(m.get("title") or {}).get("english"),
                score=m.get("averageScore"),
                year=m.get("seasonYear"),
                format=m.get("format"),
                episodes=m.get("episodes"),
            )
            for m in media
            if m.get("id")
        ]
        return ToolResult(
            ok=True,
            data=AniListResult(query=args.keyword, count=len(items), results=items),
            sources=[
                Citation(title=f"AniList — {item.title_romaji}", url=f"https://anilist.co/{args.type}/{item.id}", source="anilist")
                for item in items[:5]
            ],
        )

    async def run_many(self, requests: list[AniListArgs]) -> list[ToolResult[AniListResult]]:
        """Resolve independent title searches with GraphQL aliases.

        Recommendation verification commonly checks 16 finalists. Sending one
        HTTP request per title adds no evidence quality; aliases preserve every
        individual search while reducing connection and rate-limit overhead.
        """
        if not requests:
            return []
        output: list[ToolResult[AniListResult] | None] = [None] * len(requests)

        async def fetch_chunk(client: httpx.AsyncClient, indexed: list[tuple[int, AniListArgs]]) -> None:
            definitions = ",".join(f"$s{idx}:String" for idx, _args in indexed)
            selections = []
            variables: dict[str, str] = {}
            for idx, args in indexed:
                mtype = "ANIME" if args.type == "anime" else "MANGA"
                selections.append(
                    f"q{idx}:Page(perPage:{args.limit})"
                    f"{{media(search:$s{idx},type:{mtype}){{{_MEDIA_FIELDS}}}}}"
                )
                variables[f"s{idx}"] = args.keyword
            query = f"query({definitions}){{{''.join(selections)}}}"
            try:
                response = await client.post(_ANILIST_API, json={"query": query, "variables": variables})
                response.raise_for_status()
                data = (response.json().get("data") or {})
            except (httpx.HTTPError, httpx.TransportError) as exc:
                for idx, _args in indexed:
                    output[idx] = ToolResult(ok=False, error=f"AniList 查询失败：{type(exc).__name__}")
                return
            for idx, args in indexed:
                media = ((data.get(f"q{idx}") or {}).get("media") or [])
                output[idx] = self._result(args, media)

        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout) as c:
                chunks = [
                    list(enumerate(requests))[start:start + _BATCH_SIZE]
                    for start in range(0, len(requests), _BATCH_SIZE)
                ]
                await asyncio.gather(
                    *(fetch_chunk(c, chunk) for chunk in chunks),
                )
        except (httpx.HTTPError, httpx.TransportError) as exc:
            return [ToolResult(ok=False, error=f"AniList 查询失败：{type(exc).__name__}") for _args in requests]

        return [
            result if result is not None else ToolResult(ok=False, error="AniList 批量查询未返回结果")
            for result in output
        ]

    async def run(self, args: AniListArgs) -> ToolResult[AniListResult]:
        # 单标题入口保留原 GraphQL 变量查询；推荐批量核验走 run_many。
        mtype = "ANIME" if args.type == "anime" else "MANGA"
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout) as c:
                response = await c.post(
                    _ANILIST_API,
                    json={"query": _QUERY % args.limit, "variables": {"s": args.keyword, "t": mtype}},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, httpx.TransportError) as exc:
            return ToolResult(ok=False, error=f"AniList 查询失败：{type(exc).__name__}")
        media = ((data.get("data") or {}).get("Page") or {}).get("media") or []
        return self._result(args, media)


def build_anilist_tools() -> list[Tool]:
    return [SearchAniListTool()]
