"""季番分诊：按季拉番 + Bangumi 实时评分 + 导视精准外链。

按 air_date 范围拉某季动画 + 实时评分（播出时评分天然联动）；附本季**导视外链**——
数据向（名作之壁吧/yuc）+ 评价向（泛式/瓶子君等漫评 UP）。
画像排序（必追/可等/不适合）由 agent 拿结果 + get_taste_profile 编排，不写死进工具。
"""
from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ..bangumi.client import BangumiClient
from ..bangumi.models import SubjectBrief

_SEASON_NAME = {1: "冬", 4: "春", 7: "夏", 10: "秋"}


def _air_range(year: int, month: int) -> tuple[str, str]:
    start = f"{year}-{month:02d}-01"
    end = f"{year + 1}-01-01" if month == 10 else f"{year}-{month + 3:02d}-01"
    return start, end


def _guides(year: int, month: int) -> list["GuideLink"]:
    gq = quote(f"{year}年{month}月 新番导视")
    rq = quote(f"{year}年{month}月 新番 推荐")
    return [
        GuideLink(site="名作之壁吧", url=f"https://search.bilibili.com/all?keyword={gq}", note="数据向新番导视（最推）"),
        GuideLink(site="yuc.wiki", url=f"https://yuc.wiki/{year}{month:02d}/", note="放送时间表/数据（可用 list_yuc_season 读取）"),
        GuideLink(site="漫评 UP（泛式/瓶子君/台长等）", url=f"https://search.bilibili.com/all?keyword={rq}", note="评价向导视/推荐视频"),
    ]


class SeasonArgs(BaseModel):
    year: int = Field(..., description="年份，如 2024")
    month: Literal[1, 4, 7, 10] = Field(..., description="季度起始月：1 冬 / 4 春 / 7 夏 / 10 秋")
    limit: int = Field(15, ge=1, le=30)


class YearAnimeArgs(BaseModel):
    year: int = Field(..., description="年份，如 2027；可查未来年份，结果仅代表 Bangumi 已收录且有播出日期的动画")
    limit_per_season: int = Field(20, ge=1, le=30, description="每季度最多返回多少部")


class GuideLink(BaseModel):
    site: str
    url: str
    note: str


class SeasonResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    season: str
    count: int
    anime: list[SubjectBrief] = Field(default_factory=list)
    guides: list[GuideLink] = Field(default_factory=list)


class YearAnimeResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    year: int
    count: int
    seasons: list[SeasonResult] = Field(default_factory=list)


class ListSeasonAnimeTool(Tool):
    name = "list_season_anime"
    description = (
        "列某季新番（年 + 季度月 1冬/4春/7夏/10秋），带 Bangumi **实时评分**、按热度排，"
        "并附本季**导视外链**（名作之壁吧/yuc 数据向、泛式/瓶子君等漫评向）。"
        "用于『X 年 X 月番有什么 / 这季追什么 / 新番导视』。"
        "**拿到后请配合 get_taste_profile 给用户分诊：必追 / 可等完结 / 不适合你，并附导视链接**。"
    )
    args_model = SeasonArgs
    result_model = SeasonResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def _fetch_season(self, year: int, month: int, limit: int) -> SeasonResult:
        start, end = _air_range(year, month)
        raw = await self.client.search_subjects(
            "", subject_type=2, sort="heat", limit=limit, air_date=[f">={start}", f"<{end}"]
        )
        anime = [SubjectBrief.from_raw(s) for s in (raw.get("data") or []) if s.get("id")]
        return SeasonResult(
            season=f"{year} 年 {month} 月（{_SEASON_NAME[month]}）番",
            count=len(anime),
            anime=anime,
            guides=_guides(year, month),
        )

    async def run(self, args: SeasonArgs) -> ToolResult[SeasonResult]:
        result = await self._fetch_season(args.year, args.month, args.limit)
        return ToolResult(
            ok=True,
            data=result,
            sources=[
                Citation(title=s.name_cn or s.name, url=f"https://bgm.tv/subject/{s.id}", source="bangumi", image=s.image)
                for s in result.anime[:5]
            ],
        )


class ListYearAnimeTool(Tool):
    name = "list_year_anime"
    description = (
        "按全年四个季度列某年动画（1/4/7/10 月番），每季按 Bangumi heat 排。"
        "用于『2027 年有什么番 / 明年有哪些动画化 / 某年新番总览』。"
        "未来年份只代表 Bangumi **已收录且有 air_date** 的条目；查不到时不要断言没公开，只说当前 Bangumi 未收录。"
    )
    args_model = YearAnimeArgs
    result_model = YearAnimeResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client
        self._season_tool = ListSeasonAnimeTool(client)

    async def run(self, args: YearAnimeArgs) -> ToolResult[YearAnimeResult]:
        seasons = [
            await self._season_tool._fetch_season(args.year, month, args.limit_per_season)
            for month in (1, 4, 7, 10)
        ]
        anime = [s for season in seasons for s in season.anime]
        return ToolResult(
            ok=True,
            data=YearAnimeResult(year=args.year, count=len(anime), seasons=seasons),
            sources=[
                Citation(title=s.name_cn or s.name, url=f"https://bgm.tv/subject/{s.id}", source="bangumi", image=s.image)
                for s in anime[:8]
            ],
        )


def build_season_tools(client: BangumiClient) -> list[Tool]:
    return [ListSeasonAnimeTool(client), ListYearAnimeTool(client)]
