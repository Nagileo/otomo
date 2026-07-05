"""Official watch-source aggregation tools."""
from __future__ import annotations

from datetime import date
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from ...agent._common import emit_tool_progress
from ...agent.contracts import Citation, Tool, ToolResult
from ..bangumi.client import SUBJECT_TYPE, BangumiClient
from ..bangumi.models import SubjectBrief
from ..season.tool import _match_yuc
from ..yuc.tool import ListYucSeasonTool, YucSeasonArgs
from .data import find_item, load_bangumi_data, official_sites_for_item


class WatchSource(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str
    url: str
    source: str
    site: str = ""
    regions: list[str] = Field(default_factory=list)
    official: bool = True
    confidence: float = 1.0
    note: str = ""


class WhereToWatchArgs(BaseModel):
    subject_id: int | None = Field(None, description="Bangumi 动画 subject_id；优先使用")
    title: str = Field("", description="作品名；subject_id 为空时用于搜索 Bangumi 和 bangumi-data")
    year: int | None = Field(None, description="可选：作品播出年份，用于匹配 yuc 季番表")
    month: Literal[1, 4, 7, 10] | None = Field(None, description="可选：季度起始月，用于匹配 yuc 季番表")
    region_preference: list[str] = Field(default_factory=lambda: ["CN"], description="优先展示区域")


class WhereToWatchResult(BaseModel):
    subject_id: int | None = None
    title: str
    title_jp: str = ""
    air_date: str = ""
    image: str | None = None
    official_sources: list[WatchSource] = Field(default_factory=list)
    search_fallbacks: list[WatchSource] = Field(default_factory=list)
    offline_hint: bool = True
    mapping_notes: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


def _quarter_month(value: str | None) -> int | None:
    if not value:
        return None
    try:
        m = date.fromisoformat(value[:10]).month
    except ValueError:
        return None
    if m <= 3:
        return 1
    if m <= 6:
        return 4
    if m <= 9:
        return 7
    return 10


def _year(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]).year
    except ValueError:
        return None


def _subject_title(raw: dict) -> str:
    return str(raw.get("name_cn") or raw.get("name") or "").strip()


class WhereToWatchTool(Tool):
    name = "where_to_watch"
    description = (
        "查询某动画的正版观看入口：Bangumi 条目 → bangumi-data 官方 onair 站点 → yuc B站配信链接 → B站搜索兜底。"
        "用于『在哪看 / B站有吗 / 正版平台 / 播放入口』；只返回外链，不抓取/播放内容。"
    )
    args_model = WhereToWatchArgs
    result_model = WhereToWatchResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client
        self.yuc = ListYucSeasonTool()

    async def _resolve(self, args: WhereToWatchArgs) -> dict:
        if args.subject_id:
            return await self.client.get_subject(args.subject_id)
        if not args.title.strip():
            return {}
        raw = await self.client.search_subjects(args.title, SUBJECT_TYPE["anime"], limit=5)
        rows = raw.get("data") or []
        if not rows:
            return {}
        exact = [x for x in rows if _subject_title(x) == args.title or x.get("name") == args.title]
        return exact[0] if exact else rows[0]

    async def _yuc_sources(self, subject: SubjectBrief, year: int | None, month: int | None) -> tuple[list[WatchSource], list[str]]:
        if not year or not month:
            return [], []
        res = await self.yuc.run(YucSeasonArgs(year=year, month=month, limit=80))
        if not res.ok or not res.data:
            return [], ["yuc 未返回该季数据，已跳过配信补充。"]
        yuc, confidence, matched_by = _match_yuc(subject, res.data.anime)
        if not yuc:
            return [], ["yuc 季番表未匹配到该条目。"]
        sources = [
            WatchSource(
                label=stream.label or "Bilibili 正版",
                url=stream.url,
                source="yuc",
                site=stream.site,
                regions=["CN"],
                official=True,
                confidence=confidence,
                note=f"yuc 匹配：{matched_by}；标题 {yuc.title_cn}",
            )
            for stream in yuc.stream_urls
        ]
        return sources, [f"yuc 匹配 {matched_by} confidence={confidence:.2f}"]

    async def run(self, args: WhereToWatchArgs) -> ToolResult[WhereToWatchResult]:
        await emit_tool_progress(tool=self.name, summary="解析 Bangumi 动画条目", current=1, total=4)
        raw = await self._resolve(args)
        if not raw:
            return ToolResult(ok=False, error="需要 subject_id 或可解析的动画标题")
        subject = SubjectBrief.from_raw(raw)
        title = subject.name_cn or subject.name or args.title
        year = args.year or _year(subject.date)
        month = args.month or _quarter_month(subject.date)
        notes: list[str] = []
        official_sources: list[WatchSource] = []
        await emit_tool_progress(tool=self.name, summary="读取 bangumi-data 正版站点", current=2, total=4)
        try:
            data = await load_bangumi_data()
            item, matched_by = find_item(data, subject_id=subject.id, title=title)
            notes.append(f"bangumi-data 匹配：{matched_by}")
            if item:
                for site in official_sites_for_item(data, item):
                    official_sources.append(
                        WatchSource(
                            label=site.site_name,
                            url=site.url,
                            source=site.source,
                            site=site.site,
                            regions=site.regions,
                            official=site.official,
                            confidence=1.0 if matched_by == "bangumi_id" else 0.72,
                            note="bangumi-data onair 官方入口",
                        )
                    )
        except Exception as e:  # noqa: BLE001
            notes.append(f"bangumi-data 暂不可用：{type(e).__name__}")
        await emit_tool_progress(tool=self.name, summary="补充 yuc B站配信入口", current=3, total=4)
        yuc_sources, yuc_notes = await self._yuc_sources(subject, year, month)
        notes.extend(yuc_notes)
        seen = {x.url for x in official_sources}
        official_sources.extend(x for x in yuc_sources if x.url not in seen)
        official_sources.sort(
            key=lambda x: (
                0 if any(r.upper() in {p.upper() for p in args.region_preference} for r in x.regions) else 1,
                -x.confidence,
                x.label,
            )
        )
        search_q = quote(title)
        search_fallbacks = [
            WatchSource(
                label="Bilibili 搜索",
                url=f"https://search.bilibili.com/all?keyword={search_q}",
                source="bilibili_search",
                site="bilibili",
                regions=["CN"],
                official=False,
                confidence=0.35,
                note="搜索兜底；需用户自行判断是否为正版番剧页。",
            )
        ]
        await emit_tool_progress(tool=self.name, summary=f"观看入口完成：{len(official_sources)} 个官方候选", current=4, total=4)
        result = WhereToWatchResult(
            subject_id=subject.id,
            title=title,
            title_jp=subject.name,
            air_date=subject.date or "",
            image=subject.image,
            official_sources=official_sources,
            search_fallbacks=search_fallbacks,
            offline_hint=True,
            mapping_notes=notes,
            caveats=[
                "平台版权和上架地区会变化；结果来自 bangumi-data/yuc 缓存与当前搜索入口。",
                "Otomo 只提供正版入口和搜索兜底，不代理播放、不抓取视频内容。",
                "找不到正版入口时，可询问离线 RSS/BD 资源聚合；那会作为 link aggregation 单独处理。",
            ],
        )
        sources = [Citation(title=title, url=f"https://bgm.tv/subject/{subject.id}", source="bangumi", image=subject.image)]
        sources.extend(Citation(title=s.label, url=s.url, source=s.source) for s in official_sources[:4])
        return ToolResult(ok=True, data=result, sources=sources[:8])


def build_watch_tools(client: BangumiClient) -> list[Tool]:
    return [WhereToWatchTool(client)]
