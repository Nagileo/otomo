"""长门番堂 yuc.wiki 季番表抓取工具。

定位：新番导视的数据源补充。Bangumi 用于条目/评分/收藏状态；yuc 用于放送表、
官网/PV、制作阵容和题材标签的当季导视信息。
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from .._cache import acached

_BASE = "https://yuc.wiki/"
_SEASON_NAME = {1: "冬", 4: "春", 7: "夏", 10: "秋"}


class YucSeasonArgs(BaseModel):
    year: int = Field(..., description="年份，如 2026")
    month: int = Field(..., ge=1, le=12, description="季度起始月，通常是 1/4/7/10")
    limit: int = Field(20, ge=1, le=80)


class YucStreamUrl(BaseModel):
    site: str
    label: str
    url: str
    kind: str = "official_stream"


class YucAnime(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str | None = None
    title_cn: str
    title_jp: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    broadcast: str | None = None
    studio: str | None = None
    official_url: str | None = None
    pv_url: str | None = None
    bili_url: str | None = None
    stream_urls: list[YucStreamUrl] = Field(default_factory=list)
    image: str | None = None
    staff_summary: str | None = None
    cast_summary: str | None = None


class YucSeasonResult(BaseModel):
    year: int
    month: int
    season: str
    count: int
    source_url: str
    anime: list[YucAnime] = Field(default_factory=list)


def _clean(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    return unescape(value).strip()


def _first(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, flags=re.S | re.I)
    if not m:
        return None
    value = _clean(m.group(1))
    return value or None


def _is_bili_stream(url: str) -> bool:
    return "bilibili.com/bangumi/play/" in url or "bilibili.com/bangumi/media/" in url


def _links(table: str) -> tuple[str | None, str | None, list[YucStreamUrl]]:
    official, pv = None, None
    streams: list[YucStreamUrl] = []
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', table, flags=re.S | re.I):
        label = _clean(text)
        url = urljoin(_BASE, href)
        if label == "动画官网":
            official = url
        elif label == "PV":
            pv = url
        if _is_bili_stream(url):
            streams.append(YucStreamUrl(site="bilibili", label=label or "Bilibili 正版", url=url))
    return official, pv, streams


def _studio(staff: str | None) -> str | None:
    if not staff:
        return None
    # 动画制作后可能跟多家公司，yuc 用 <br>（_clean 后变换行）分隔；后续行不含字段冒号才算制作公司。
    m = re.search(r"动画制作[:：]\s*([^\n]+(?:\n[^\n：:]+)*)", staff)
    if not m:
        return None
    # 多家公司用顿号连接，避免"A-1 Pictures Psyde Kick Studio"被空格粘连成一家。
    studios = [re.sub(r"\s+", " ", line).strip() for line in m.group(1).split("\n")]
    return "、".join(dict.fromkeys(s for s in studios if s)) or None


def _parse(html: str, limit: int) -> list[YucAnime]:
    block_re = re.compile(
        r"<!--#(?P<code>[A-Z]\d+).*?"
        r"<img[^>]+(?:data-src|src)=\"(?P<img>[^\"]+)\".*?"
        r"<table[^>]*>(?P<table>.*?)</table>",
        flags=re.S | re.I,
    )
    items: list[YucAnime] = []
    for m in block_re.finditer(html):
        table = m.group("table")
        title_cn = _first(r'<p class="title_cn[^"]*">(.*?)</p>', table)
        title_jp = _first(r'<p class="title_jp[^"]*">(.*?)</p>', table)
        if not title_cn and not title_jp:
            continue
        category = _first(r'<td class="type_a[^"]*">(.*?)</td>', table)
        tag_text = _first(r'<td class="type_tag[^"]*">(.*?)</td>', table) or ""
        staff = _first(r'<td[^>]+class="staff[^"]*"[^>]*>(.*?)</td>', table)
        cast = _first(r'<td[^>]+class="cast[^"]*"[^>]*>(.*?)</td>', table)
        official, pv, streams = _links(table)
        item = YucAnime(
            code=m.group("code"),
            title_cn=title_cn or title_jp or "",
            title_jp=title_jp,
            category=category,
            tags=[t.strip() for t in re.split(r"[/／、，,\s]+", tag_text) if t.strip()],
            broadcast=_first(r'<p class="broadcast[^"]*">(.*?)</p>', table),
            studio=_studio(staff),
            official_url=official,
            pv_url=pv,
            bili_url=streams[0].url if streams else None,
            stream_urls=streams,
            image=urljoin(_BASE, m.group("img")),
            staff_summary=staff,
            cast_summary=cast,
        )
        items.append(item)
        if len(items) >= limit:
            break
    return items


@acached()
async def _yuc_fetch(url: str) -> str:
    async with httpx.AsyncClient(
        timeout=settings.http_timeout,
        headers={"User-Agent": settings.bangumi_user_agent},
    ) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text


class ListYucSeasonTool(Tool):
    name = "list_yuc_season"
    description = (
        "读取 yuc.wiki/长门番堂某季新番表，返回当季番名、题材标签、放送时间、官网/PV、制作阵容。"
        "用于新番导视、播出表、制作阵容初筛；评分/收藏状态仍用 list_season_anime/Bangumi。"
    )
    args_model = YucSeasonArgs
    result_model = YucSeasonResult

    async def run(self, args: YucSeasonArgs) -> ToolResult[YucSeasonResult]:
        month = min((1, 4, 7, 10), key=lambda m: abs(m - args.month))
        url = f"{_BASE}{args.year}{month:02d}/"
        try:
            html = await _yuc_fetch(url)
        except (httpx.HTTPError, httpx.TransportError) as e:
            return ToolResult(ok=False, error=f"yuc.wiki 查询失败：{type(e).__name__}")

        items = _parse(html, args.limit)
        season = f"{args.year} 年 {month} 月（{_SEASON_NAME.get(month, '')}）番"
        return ToolResult(
            ok=True,
            data=YucSeasonResult(
                year=args.year, month=month, season=season, count=len(items), source_url=url, anime=items
            ),
            sources=[
                Citation(
                    title=i.title_cn,
                    url=i.official_url or url,
                    source="yuc",
                    image=i.image,
                )
                for i in items[:5]
            ],
        )


def build_yuc_tools() -> list[Tool]:
    return [ListYucSeasonTool()]
