"""AniList（英文圈动画/漫画库）查询——Source Router 的 Canonical 兜底添头。

GraphQL API（graphql.anilist.co），无 token，评分满分 100。
**关键约束：中文名搜不到，须用日文原名 / 英文 / 罗马音**（可先用 search_subjects 拿 Bangumi 的 name 日文原名再来搜）。
定位：主源是 Bangumi，这里作"查不到再补英文圈数据/别名/评分"的兜底，主体不动摇。
"""
from __future__ import annotations

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings

_ANILIST_API = "https://graphql.anilist.co"
_QUERY = (
    "query($s:String,$t:MediaType){Page(perPage:%d){media(search:$s,type:$t)"
    "{id title{romaji native english} averageScore seasonYear format episodes}}}"
)


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

    async def run(self, args: AniListArgs) -> ToolResult[AniListResult]:
        mtype = "ANIME" if args.type == "anime" else "MANGA"
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout) as c:
                r = await c.post(
                    _ANILIST_API,
                    json={"query": _QUERY % args.limit, "variables": {"s": args.keyword, "t": mtype}},
                )
                r.raise_for_status()
                data = r.json()
        except (httpx.HTTPError, httpx.TransportError) as e:
            return ToolResult(ok=False, error=f"AniList 查询失败：{type(e).__name__}")

        media = ((data.get("data") or {}).get("Page") or {}).get("media") or []
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
                Citation(title=f"AniList — {i.title_romaji}", url=f"https://anilist.co/{args.type}/{i.id}", source="anilist")
                for i in items[:5]
            ],
        )


def build_anilist_tools() -> list[Tool]:
    return [SearchAniListTool()]
