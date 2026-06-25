"""ErogameScape / 批判空间查询工具。

定位：galgame 圈层评分证据源。Bangumi game 仍是主源；需要更贴近 gal 圈口碑、
中央值、数据数时，用本工具补充。外站偶发慢/不可达，失败时优雅返回错误。
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import quote, urljoin

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings

_BASE = "https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/"
_SEARCH = (
    _BASE
    + "kensaku.php?mode=normal&category=game&word_category=name&word={keyword}"
)


class EGSArgs(BaseModel):
    keyword: str = Field(..., description="galgame / 视觉小说名，建议用日文名或常用英文名")
    limit: int = Field(5, ge=1, le=10)


class EGSGame(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    title: str
    brand: str | None = None
    released: str | None = None
    median: int | None = None
    stdev: int | None = None
    vote_count: int | None = None
    labels: list[str] = Field(default_factory=list)
    url: str


class EGSResult(BaseModel):
    query: str
    count: int
    source_url: str
    results: list[EGSGame] = Field(default_factory=list)


def _clean_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return unescape(value).strip()


def _to_int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.isdigit() else None


def _parse_results(html: str, limit: int) -> list[EGSGame]:
    rows = re.findall(r"<tr>(.*?)</tr>", html, flags=re.S | re.I)
    items: list[EGSGame] = []
    for row in rows:
        game = re.search(
            r'href="(?P<href>game\.php\?game=(?P<id>\d+)[^"]*)"[^>]*>(?P<title>.*?)</a>',
            row,
            flags=re.S | re.I,
        )
        if not game:
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)
        if len(cells) < 6:
            continue
        labels = re.findall(r"\(([^()]+)\)", _clean_html(cells[0]))
        item = EGSGame(
            id=int(game.group("id")),
            title=_clean_html(game.group("title")),
            brand=_clean_html(cells[1]) or None,
            released=_clean_html(cells[2]) or None,
            median=_to_int(_clean_html(cells[3])),
            stdev=_to_int(_clean_html(cells[4])),
            vote_count=_to_int(_clean_html(cells[5])),
            labels=labels,
            url=urljoin(_BASE, game.group("href")),
        )
        items.append(item)
        if len(items) >= limit:
            break
    return items


class SearchErogameScapeTool(Tool):
    name = "search_erogamescape"
    description = (
        "在 ErogameScape / 批判空间搜索 galgame，返回中央值、标准差、数据数、品牌和发售日。"
        "用于 galgame 推荐/评价/口碑时补充圈层评分证据。Bangumi game 是主源，本工具是 gal 圈口碑辅助源。"
        "引用须注明 ErogameScape/批判空间，且外站可能访问不稳定。"
    )
    args_model = EGSArgs
    result_model = EGSResult

    async def run(self, args: EGSArgs) -> ToolResult[EGSResult]:
        url = _SEARCH.format(keyword=quote(args.keyword))
        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout,
                headers={"User-Agent": settings.bangumi_user_agent},
            ) as c:
                r = await c.get(url)
                r.raise_for_status()
        except (httpx.HTTPError, httpx.TransportError) as e:
            return ToolResult(ok=False, error=f"ErogameScape 查询失败：{type(e).__name__}")

        items = _parse_results(r.text, args.limit)
        return ToolResult(
            ok=True,
            data=EGSResult(query=args.keyword, count=len(items), source_url=url, results=items),
            sources=[
                Citation(title=f"ErogameScape — {i.title}", url=i.url, source="erogamescape")
                for i in items[:5]
            ],
        )


def build_erogamescape_tools() -> list[Tool]:
    return [SearchErogameScapeTool()]
