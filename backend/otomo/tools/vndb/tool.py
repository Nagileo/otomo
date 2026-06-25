"""VNDB（galgame / 视觉小说数据库）查询工具——galgame canonical 事实源，补 Bangumi。

VNDB v2 (kana) HTTPS API：POST 查询、无需 token。评分制 **0-100**（与 Bangumi 0-10 不同，作答须注明）。
属 Source Router 的 Canonical Facts 层：galgame 的权威评分/发售/简介，Bangumi gal 数据不全时用它补。
"""
from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings

_VNDB_VN_API = "https://api.vndb.org/kana/vn"


class VNSearchArgs(BaseModel):
    keyword: str = Field(..., description="galgame / 视觉小说名（中日英均可）")
    limit: int = Field(5, ge=1, le=15)


class VNBrief(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str = ""
    alttitle: str | None = None
    rating: float | None = None   # VNDB 评分，满分 100
    votecount: int = 0
    released: str | None = None


class VNSearchResult(BaseModel):
    query: str
    count: int
    results: list[VNBrief] = Field(default_factory=list)


class SearchVNTool(Tool):
    name = "search_visual_novels"
    description = (
        "在 VNDB（galgame / 视觉小说权威数据库）搜索作品，拿 canonical 评分（**满分 100**）/ 发售日 / 简介。"
        "Bangumi 的 galgame 数据不全、或想要 gal 圈权威评分时用它补。引用须注明 VNDB（满分100）并附 vndb.org 链接。"
    )
    args_model = VNSearchArgs
    result_model = VNSearchResult

    async def run(self, args: VNSearchArgs) -> ToolResult[VNSearchResult]:
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout) as c:
                r = await c.post(
                    _VNDB_VN_API,
                    json={
                        "filters": ["search", "=", args.keyword],
                        "fields": "id, title, alttitle, rating, votecount, released",
                        "results": args.limit,
                    },
                )
                r.raise_for_status()
                data = r.json()
        except (httpx.HTTPError, httpx.TransportError) as e:
            return ToolResult(ok=False, error=f"VNDB 查询失败：{type(e).__name__}")

        items = [VNBrief.model_validate(v) for v in (data.get("results") or [])]
        return ToolResult(
            ok=True,
            data=VNSearchResult(query=args.keyword, count=len(items), results=items),
            sources=[
                Citation(title=f"VNDB — {v.title}", url=f"https://vndb.org/{v.id}", source="vndb")
                for v in items[:5]
            ],
        )


def build_vndb_tools() -> list[Tool]:
    return [SearchVNTool()]
