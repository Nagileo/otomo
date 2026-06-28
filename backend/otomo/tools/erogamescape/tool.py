"""ErogameScape / 批判空间查询工具。

定位：galgame 圈层评分证据源。Bangumi game 仍是主源；需要更贴近 gal 圈口碑、
中央値、平均值、排名位、数据数时，用本工具补充。外站偶发慢/不可达，失败时优雅返回错误。
"""
from __future__ import annotations

import re
from html import unescape
from typing import Literal
from urllib.parse import quote, urlencode, urljoin

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


class EGSRankArgs(BaseModel):
    sort: Literal["median", "average", "count"] = Field(
        "median", description="排行榜口径：median=中央値，average=平均值，count=数据数"
    )
    year: int | None = Field(None, description="可选发售年份；不传则全站排行")
    erogame_only: bool = Field(True, description="是否追加 erogame=t；galgame 推荐默认 true")
    coterie: bool = Field(False, description="是否追加 coterie=t")
    min_votes: int = Field(30, ge=0, le=5000, description="最低数据数过滤，避免极低样本高分")
    limit: int = Field(20, ge=1, le=50)


class EGSGame(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    title: str
    brand: str | None = None
    released: str | None = None
    median: int | None = None
    average: float | None = None
    stdev: int | None = None
    vote_count: int | None = None
    total_count: int | None = None
    rank_position: int | None = None
    labels: list[str] = Field(default_factory=list)
    url: str


class EGSResult(BaseModel):
    query: str
    count: int
    source_url: str
    results: list[EGSGame] = Field(default_factory=list)


class EGSRankResult(BaseModel):
    sort: str
    year: int | None = None
    count: int
    source_url: str
    min_votes: int = 0
    results: list[EGSGame] = Field(default_factory=list)


def _clean_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return unescape(value).strip()


def _to_int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.isdigit() else None


def _to_float(value: str) -> float | None:
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        return None


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


def _rank_path(args: EGSRankArgs) -> str:
    if args.year:
        path = "toukei_year_count.php" if args.sort == "count" else "toukei_year_median.php"
        params: dict[str, str | int] = {"year": args.year}
    else:
        path = {
            "median": "toukei_median.php",
            "average": "toukei_avg.php",
            "count": "toukei_datacount.php",
        }[args.sort]
        params = {}
    if args.erogame_only:
        params["erogame"] = "t"
    if args.coterie:
        params["coterie"] = "t"
    return path + (("?" + urlencode(params)) if params else "")


def _parse_rank_results(html: str, limit: int, min_votes: int = 0) -> list[EGSGame]:
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I)
    headers: list[str] = []
    items: list[EGSGame] = []
    raw_rank = 0
    for row in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I)
        if not cells:
            continue
        clean_cells = [_clean_html(c) for c in cells]
        if "<th" in row.lower():
            headers = clean_cells
            continue
        game = re.search(
            r'href="(?P<href>game\.php\?game=(?P<id>\d+)[^"]*)"[^>]*>(?P<title>.*?)</a>',
            row,
            flags=re.S | re.I,
        )
        if not game:
            continue
        raw_rank += 1
        by_header = {h: clean_cells[i] for i, h in enumerate(headers[: len(clean_cells)])}
        vote_count = _to_int(by_header.get("データ数", ""))
        if min_votes and (vote_count or 0) < min_votes:
            continue
        item = EGSGame(
            id=int(game.group("id")),
            title=_clean_html(game.group("title")),
            brand=clean_cells[1] if len(clean_cells) > 1 else None,
            median=_to_int(by_header.get("中央値", "")),
            average=_to_float(by_header.get("平均値", "")),
            stdev=_to_int(by_header.get("標準偏差", "")),
            vote_count=vote_count,
            total_count=_to_int(by_header.get("データ総数", "")),
            rank_position=raw_rank,
            url=urljoin(_BASE, game.group("href")),
        )
        items.append(item)
        if len(items) >= limit:
            break
    return items


class SearchErogameScapeTool(Tool):
    name = "search_erogamescape"
    description = (
        "在 ErogameScape / 批判空间搜索 galgame，返回中央値、标准差、数据数、品牌和发售日。"
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


class RankErogameScapeTool(Tool):
    name = "rank_erogamescape"
    description = (
        "读取 ErogameScape / 批判空间排行榜，返回排名位、中央値、平均值、标准差、数据数。"
        "用于 galgame 推荐的前置召回、口碑排行、按年份查高口碑作品。外站可能访问不稳定；引用需注明批判空间。"
        "rank_position 是批判空间原始榜位，已按 min_votes 过滤掉低样本作品，故序号可能不连续（跳号属正常）。"
    )
    args_model = EGSRankArgs
    result_model = EGSRankResult

    async def run(self, args: EGSRankArgs) -> ToolResult[EGSRankResult]:
        path = _rank_path(args)
        url = urljoin(_BASE, path)
        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout,
                headers={"User-Agent": settings.bangumi_user_agent},
            ) as c:
                r = await c.get(url)
                r.raise_for_status()
        except (httpx.HTTPError, httpx.TransportError) as e:
            return ToolResult(ok=False, error=f"ErogameScape 排行查询失败：{type(e).__name__}")

        items = _parse_rank_results(r.text, args.limit, args.min_votes)
        return ToolResult(
            ok=True,
            data=EGSRankResult(
                sort=args.sort,
                year=args.year,
                count=len(items),
                source_url=url,
                min_votes=args.min_votes,
                results=items,
            ),
            sources=[
                Citation(title=f"ErogameScape #{i.rank_position} — {i.title}", url=i.url, source="erogamescape")
                for i in items[:5]
            ],
        )


def build_erogamescape_tools() -> list[Tool]:
    return [SearchErogameScapeTool(), RankErogameScapeTool()]
