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


def _media_channels(subject_type: int, platform: str, title: str) -> tuple[list[WatchSource], list[WatchSource], list[str]]:
    """anime 之外的媒介渠道（2026-07-05 与用户核定的现实图景）：

    - galgame：正版购买 = DLsite / Steam / Fanza；讨论与补丁 = 批评空间 / KF绯月。
    - comic：简中正版 = B漫；资源站（拷贝/再漫画）明确标注非正版。
    - novel/LN：正版 = BOOK☆WALKER 台湾 / 日亚（web 连载正版基本只有购买渠道）；
      真白萌（web 社区翻译）/ 轻之国度（文库）是社区资源，标注"支持正版请购买"。
    """
    q = quote(title)
    buy: list[WatchSource] = []
    community: list[WatchSource] = []
    notes: list[str] = []
    if subject_type == SUBJECT_TYPE["game"]:
        buy = [
            WatchSource(label="DLsite", url=f"https://www.dlsite.com/maniax/fsr/=/keyword/{q}", source="channel", site="dlsite", regions=["JP"], official=True, confidence=0.4, note="购买渠道搜索入口（同人/商业 galgame 主站）"),
            WatchSource(label="Steam", url=f"https://store.steampowered.com/search/?term={q}", source="channel", site="steam", official=True, confidence=0.4, note="购买渠道搜索入口（全年龄/国际版常在此）"),
            WatchSource(label="Fanza (DMM)", url=f"https://dlsoft.dmm.co.jp/search/?searchstr={q}", source="channel", site="fanza", regions=["JP"], official=True, confidence=0.35, note="购买渠道搜索入口（需日区网络）"),
        ]
        community = [
            WatchSource(label="批评空间", url=f"https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/kensaku.php?category=game&word_category=name&word={q}", source="channel", site="egs", official=False, confidence=0.4, note="日本 gal 圈评价检索（review_subject 也能查）"),
            WatchSource(label="绯月 KF", url="https://bbs.kfpromax.com/", source="channel", site="kf", official=False, confidence=0.3, note="galgame 社区讨论/补丁；站内搜索作品名"),
        ]
        notes.append("galgame 无流媒体概念：正版=购买渠道，讨论/补丁走社区。")
    elif subject_type == SUBJECT_TYPE["book"]:
        is_comic = "漫画" in platform
        if is_comic:
            buy = [
                WatchSource(label="哔哩哔哩漫画", url=f"https://manga.bilibili.com/search?keyword={q}", source="channel", site="bilibili_manga", regions=["CN"], official=True, confidence=0.5, note="简中正版平台搜索入口（收录看版权）"),
            ]
            community = [
                WatchSource(label="拷贝漫画", url=f"https://www.mangacopy.com/search?q={q}", source="channel", site="copymanga", official=False, confidence=0.3, note="资源站（非正版，域名常变更）；支持正版请优先 B漫"),
                WatchSource(label="再漫画", url="https://zaimanhua.com/", source="channel", site="zaimanhua", official=False, confidence=0.25, note="资源站（非正版）；站内搜索作品名"),
            ]
            notes.append("简中漫画正版以 B漫 为主，未收录时资源站兜底（已标注非正版性质）。")
        else:
            buy = [
                WatchSource(label="BOOK☆WALKER 台湾", url=f"https://www.bookwalker.com.tw/search?w={q}", source="channel", site="bookwalker", regions=["TW"], official=True, confidence=0.45, note="繁中电子书正版购买"),
                WatchSource(label="Amazon.co.jp", url=f"https://www.amazon.co.jp/s?k={q}", source="channel", site="amazon_jp", regions=["JP"], official=True, confidence=0.4, note="日文原版（Kindle/文库）购买"),
            ]
            community = [
                WatchSource(label="真白萌", url="https://masiro.me/", source="channel", site="masiro", official=False, confidence=0.3, note="web 小说社区翻译（站内搜索）；支持正版请购买"),
                WatchSource(label="轻之国度", url="https://www.lightnovel.us/", source="channel", site="lightnovel", official=False, confidence=0.3, note="文库社区资源（站内搜索）；支持正版请购买"),
            ]
            notes.append("轻小说没有『正版在线看』渠道：正版=购买电子书/实体，web 连载翻译属社区资源。")
    return buy, community, notes


class WhereToWatchTool(Tool):
    name = "where_to_watch"
    description = (
        "查询作品的观看/购买渠道。anime：bangumi-data 官方 onair 站点 → yuc B站配信 → B站搜索兜底；"
        "galgame：DLsite/Steam/Fanza 购买 + 批评空间/绯月KF 社区；comic/轻小说：B漫/BOOK☆WALKER 正版 + 资源站（标注性质）。"
        "用于『在哪看 / 在哪买 / B站有吗 / 正版平台』；只返回外链，不抓取/播放内容。"
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
        # 不限定 anime：game/book 也走同一入口分流
        raw = await self.client.search_subjects(args.title, None, limit=5)
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
        await emit_tool_progress(tool=self.name, summary="解析 Bangumi 条目", current=1, total=4)
        raw = await self._resolve(args)
        if not raw:
            return ToolResult(ok=False, error="需要 subject_id 或可解析的作品标题")
        subject = SubjectBrief.from_raw(raw)
        title = subject.name_cn or subject.name or args.title
        stype = int(raw.get("type") or 2)
        if stype in {SUBJECT_TYPE["game"], SUBJECT_TYPE["book"]}:
            # galgame / comic / 轻小说：无流媒体路径，走购买+社区渠道分流
            buy, community, media_notes = _media_channels(stype, str(raw.get("platform") or ""), title)
            await emit_tool_progress(tool=self.name, summary=f"渠道入口完成：{len(buy)} 购买 / {len(community)} 社区", current=4, total=4)
            result = WhereToWatchResult(
                subject_id=subject.id,
                title=title,
                title_jp=subject.name,
                air_date=subject.date or "",
                image=subject.image,
                official_sources=buy,
                search_fallbacks=community,
                offline_hint=stype == SUBJECT_TYPE["book"],
                mapping_notes=media_notes,
                caveats=[
                    "购买/资源入口均为搜索链接，收录与价格以站内为准；资源站已标注非正版性质，支持正版请优先购买渠道。",
                    "Otomo 只提供外链，不代理、不抓取、不下载任何内容。",
                ],
            )
            sources = [Citation(title=title, url=f"https://bgm.tv/subject/{subject.id}", source="bangumi", image=subject.image)]
            sources.extend(Citation(title=s.label, url=s.url, source="channel") for s in [*buy, *community][:5])
            return ToolResult(ok=True, data=result, sources=sources[:8])
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
