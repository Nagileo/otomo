"""季番分诊：按季拉番 + Bangumi 实时评分 + 导视精准外链。

按 air_date 范围拉某季动画 + 实时评分（播出时评分天然联动）；附本季**导视外链**——
数据向（名作之壁吧/yuc）+ 评价向（泛式/瓶子君等漫评 UP）。
画像排序（必追/可等/不适合）由 agent 拿结果 + get_taste_profile 编排，不写死进工具。
"""
from __future__ import annotations

import asyncio
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...memory import LongTermMemory
from ...profile import compute_taste_profile
from ...security_context import current_principal
from .._concurrency import gather_limited
from ..bangumi.client import SUBJECT_TYPE, BangumiClient
from ..bangumi.models import SubjectBrief
from ..calendar.tool import BroadcastCalendarArgs, BroadcastCalendarTool
from ..discovery.tool import GetTrendingSubjectsTool, TrendingArgs
from ..series_progress import SeriesCandidateStatus, SeriesRelationMemo, collection_map, inspect_series_candidate
from ..videos.tool import (
    BiliGuideSearchArgs,
    BiliVideoMeta,
    BiliVideoCommentsArgs,
    GetBiliVideoCommentsTool,
    GuideVideoHit,
    GuideVideoLink,
    SearchBiliGuideVideosTool,
    SubjectVertical,
    _guide_links,
    verify_guide_video_links,
)
from ..yuc.tool import ListYucSeasonTool, YucAnime, YucSeasonArgs

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


class SeasonGuideBriefArgs(BaseModel):
    year: int = Field(..., description="年份，如 2026")
    month: Literal[1, 4, 7, 10] = Field(..., description="季度起始月：1/4/7/10")
    mode: Literal["auto", "preseason", "guide", "hot"] = Field(
        "auto",
        description="auto=按季度阶段选择；preseason=播前导视；guide=按用户口味；hot=热播/讨论优先",
    )
    limit: int = Field(10, ge=1, le=20)
    username: str | None = Field(None, description="Bangumi 用户名；不传则尝试当前账号，失败就做非个性化导视")
    focus_tags: list[str] | None = Field(None, description="用户临时偏好，如 ['百合','日常','治愈']")
    enrich_tags: bool = Field(True, description="是否补 Bangumi 详情标签；默认开，能提升分诊质量")
    include_video_comments: bool = Field(
        False,
        description="是否抽样读取最终通过核验的 B站季度视频评论；用于观众期待/担心点，不作为事实源",
    )
    comment_video_limit: int = Field(2, ge=1, le=3, description="最多读取几个导视视频的评论")
    comment_limit: int = Field(20, ge=5, le=50, description="每个导视视频最多读取多少条评论")
    verify_guide_videos: bool = Field(True, description="是否对路由出的白名单 UP 做真实 B站视频命中验证")
    guide_verify_limit: int = Field(2, ge=0, le=4, description="整季最多验证几个导视源；0 表示只做路由不搜索")
    discover_beyond_whitelist: bool = Field(True, description="是否用严格门槛补充发现非白名单导视视频")
    guide_result_limit: int = Field(4, ge=1, le=6, description="整季最多展示多少个已核验 B站视频")
    verify_item_videos: bool = Field(
        False,
        description="是否逐部搜索具体漫评；默认关闭以避免整季产生大量 B站请求，仅在用户明确要求时开启",
    )
    preferred_guide_sources: list[str] | None = Field(
        None,
        description="按顺序排列的导视 UP 偏好；传入后只启用这些来源，网页/Discord 可共享长期设置",
    )
    primary_guide_source: str | None = Field(None, description="优先导视 UP，会排在已启用来源最前")


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


class SeasonGuideItem(BaseModel):
    subject_id: int
    title: str
    pre_air_wish: int | None = None   # 开播前想看数（netaba.re 快照，期待度；hot 模式 top 条目才补）
    title_jp: str | None = None
    yuc_title: str | None = None
    match_confidence: float = 0.0
    matched_by: str = "bangumi_only"
    mapping_warning: str | None = None
    bangumi_score: float | None = None
    rank: int | None = None
    air_date: str | None = None
    broadcast: str | None = None
    studio: str | None = None
    tags: list[str] = Field(default_factory=list)
    match_tags: list[str] = Field(default_factory=list)
    fit_score: float = 0.0
    fit: Literal["strong", "maybe", "wait", "unknown"] = "unknown"
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    hotness: float = 0.0
    hotness_level: Literal["none", "warm", "hot", "surge"] = "none"
    doing: int | None = None
    trending_rank: int | None = None
    trending_collects: int | None = None
    episode_comment_avg: float | None = None
    episode_comment_peak: int | None = None
    hotness_evidence: list[str] = Field(default_factory=list)
    verticals: list[SubjectVertical] = Field(default_factory=list)
    guide_videos: list[GuideVideoLink] = Field(default_factory=list)
    official_url: str | None = None
    pv_url: str | None = None
    bili_url: str | None = None
    stream_urls: list[dict] = Field(default_factory=list)
    image: str | None = None
    series_status: SeriesCandidateStatus | None = None


class GuideCommentDigest(BaseModel):
    video_title: str
    author: str
    url: str
    aid: int
    count: int
    opinion_summary: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class SeasonGuideBriefResult(BaseModel):
    season: str
    mode: Literal["preseason", "guide", "hot"] = "guide"
    requested_mode: Literal["auto", "preseason", "guide", "hot"] = "auto"
    phase: Literal["upcoming", "airing", "archive"] = "airing"
    count: int
    personalized: bool = False
    profile_tags: list[str] = Field(default_factory=list)
    focus_tags: list[str] = Field(default_factory=list)
    items: list[SeasonGuideItem] = Field(default_factory=list)
    guide_videos: list[GuideVideoLink] = Field(default_factory=list)
    pending_guide_sources: list[GuideVideoLink] = Field(default_factory=list)
    guide_source_preferences: list[str] = Field(default_factory=list)
    guide_discovery_warnings: list[str] = Field(default_factory=list)
    guide_comment_digests: list[GuideCommentDigest] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class HotSignals(BaseModel):
    doing: int | None = None
    trending_rank: int | None = None
    trending_collects: int | None = None
    episode_comment_avg: float | None = None
    episode_comment_peak: int | None = None
    hotness: float = 0.0
    level: Literal["none", "warm", "hot", "surge"] = "none"
    evidence: list[str] = Field(default_factory=list)


def _season_phase(year: int, month: int, *, today: date | None = None) -> Literal["upcoming", "airing", "archive"]:
    current = today or date.today()
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 10 else date(year, month + 3, 1)
    if current < start:
        return "upcoming"
    if current >= end:
        return "archive"
    return "airing"


def _effective_mode(
    requested: Literal["auto", "preseason", "guide", "hot"],
    phase: Literal["upcoming", "airing", "archive"],
) -> Literal["preseason", "guide", "hot"]:
    if requested != "auto":
        return requested
    if phase == "upcoming":
        return "preseason"
    if phase == "archive":
        return "guide"
    return "hot"


def _norm_title(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _match_yuc(subject: SubjectBrief, yuc_items: list[YucAnime]) -> tuple[YucAnime | None, float, str]:
    keys = {_norm_title(subject.name), _norm_title(subject.name_cn)}
    keys.discard("")
    for item in yuc_items:
        ykeys = {_norm_title(item.title_cn), _norm_title(item.title_jp)}
        ykeys.discard("")
        if keys & ykeys:
            return item, 0.82, "normalized_title_exact"
        if any(k and y and (k in y or y in k) and min(len(k), len(y)) >= 4 for k in keys for y in ykeys):
            return item, 0.55, "normalized_title_partial"
    return None, 0.0, "bangumi_only"


def _fit_item(tags: list[str], score: float | None, wanted: list[str]) -> tuple[str, list[str], str, float]:
    matches = [t for t in wanted if any(t in tag or tag in t for tag in tags)]
    fit_score = round(len(matches) * 3.0 + ((score or 0) / 10.0), 3)
    if matches:
        return "strong", matches[:4], "题材标签命中你的偏好", fit_score
    if score and score >= 8:
        return "maybe", [], "Bangumi 早期口碑较强，可以重点观察", fit_score
    if score and score < 6.5:
        return "wait", [], "当前评分偏低，建议等更多反馈", fit_score
    return "unknown", [], "信息还不充分，适合先看 PV/导视判断", fit_score


def _fit_rank(fit: str) -> int:
    return {"strong": 3, "maybe": 2, "unknown": 1, "wait": 0}.get(fit, 0)


def _unique(values: list[str]) -> list[str]:
    return [v for v in dict.fromkeys(x.strip() for x in values if x and x.strip())]


def _evidence(
    subject: SubjectBrief, yuc: YucAnime | None, match_tags: list[str], fit: str, match_confidence: float = 0.0
) -> list[str]:
    out: list[str] = []
    if subject.score:
        out.append(f"Bangumi 评分 {subject.score}" + (f" / rank {subject.rank}" if subject.rank else ""))
    if yuc and yuc.broadcast:
        out.append(f"yuc 放送：{yuc.broadcast}")
    if yuc and yuc.studio:
        out.append(f"制作：{yuc.studio}")
    if yuc and match_confidence < 0.8:
        out.append("yuc/Bangumi 标题为弱匹配，制作/放送信息需谨慎引用")
    if match_tags:
        out.append("匹配偏好：" + "、".join(match_tags))
    if fit == "wait":
        out.append("建议等更多播出反馈")
    return out


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
        seasons = await gather_limited(
            [self._season_tool._fetch_season(args.year, month, args.limit_per_season) for month in (1, 4, 7, 10)],
            host="bangumi",
            return_exceptions=False,
        )
        anime = [s for season in seasons for s in season.anime]
        return ToolResult(
            ok=True,
            data=YearAnimeResult(year=args.year, count=len(anime), seasons=seasons),
            sources=[
                Citation(title=s.name_cn or s.name, url=f"https://bgm.tv/subject/{s.id}", source="bangumi", image=s.image)
                for s in anime[:8]
            ],
        )


class SeasonGuideBriefTool(Tool):
    name = "season_guide_brief"
    description = (
        "聚合某季新番导视：Bangumi 条目/评分 + yuc 放送表/制作阵容 + B站偏好来源与全站补充发现 + 用户标签分诊。"
        "用于『这季怎么追 / 新番导视 / 按我口味看 7 月番』。"
        "默认只返回导视入口；include_video_comments=true 时会抽样读取少量公开视频评论，作为话语源摘要。"
    )
    args_model = SeasonGuideBriefArgs
    result_model = SeasonGuideBriefResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory | None = None) -> None:
        self.client = client
        self.ltm = ltm
        self._season_tool = ListSeasonAnimeTool(client)
        self._yuc_tool = ListYucSeasonTool()
        self._bili_comments_tool = GetBiliVideoCommentsTool()
        self._bili_search_tool = SearchBiliGuideVideosTool()

    def _guide_source_preferences(self, args: SeasonGuideBriefArgs) -> list[str]:
        enabled = args.preferred_guide_sources
        primary = (args.primary_guide_source or "").strip()
        principal = current_principal()
        username = (
            principal.username
            if principal and principal.authenticated
            else (args.username or "").strip()
        )
        if enabled is None and username and self.ltm is not None:
            try:
                preference = self.ltm.load_user(username).season_guide_preferences
                enabled = preference.enabled_sources or None
                primary = primary or preference.primary_source.strip()
            except Exception:  # noqa: BLE001 - preferences are optional enrichment
                enabled = None
        ordered = list(dict.fromkeys(x.strip() for x in (enabled or []) if x.strip()))
        if primary:
            ordered = [primary, *[name for name in ordered if name != primary]]
        return ordered

    async def _hot_signal_maps(self, subjects: list[SubjectBrief]) -> dict[int, HotSignals]:
        signals: dict[int, HotSignals] = {s.id: HotSignals() for s in subjects}
        if not subjects:
            return signals

        async def calendar_map() -> dict[int, int]:
            out: dict[int, int] = {}
            try:
                res = await BroadcastCalendarTool(self.client).run(BroadcastCalendarArgs(day="week", only_mine=False))
            except Exception:  # noqa: BLE001
                return out
            if not res.ok or not res.data:
                return out
            for day in res.data.days:
                for item in day.items:
                    if item.id and item.doing is not None:
                        out[int(item.id)] = int(item.doing)
            return out

        async def trending_map() -> dict[int, tuple[int, int | None]]:
            out: dict[int, tuple[int, int | None]] = {}
            try:
                res = await GetTrendingSubjectsTool().run(TrendingArgs(subject_type="anime", limit=24))
            except Exception:  # noqa: BLE001
                return out
            if not res.ok or not res.data:
                return out
            for idx, item in enumerate(res.data.items, start=1):
                out[int(item.id)] = (idx, item.collects)
            return out

        async def episode_stats(subject_id: int) -> tuple[int, float | None, int | None]:
            try:
                raw = await self.client.get_episodes(subject_id, ep_type=0, limit=80)
            except Exception:  # noqa: BLE001
                return subject_id, None, None
            counts = []
            today = date.today().isoformat()
            for row in raw.get("data") or []:
                air = row.get("airdate") or ""
                if air and air > today:
                    continue
                value = row.get("comment") or 0
                try:
                    counts.append(int(value))
                except (TypeError, ValueError):
                    continue
            if not counts:
                return subject_id, None, None
            return subject_id, round(sum(counts) / max(len(counts), 1), 2), max(counts)

        cal_res, trend_res, ep_res = await asyncio.gather(
            calendar_map(),
            trending_map(),
            gather_limited([episode_stats(s.id) for s in subjects], host="bangumi"),
        )
        max_doing = max(cal_res.values(), default=0)
        avg_values = [x[1] for x in ep_res if not isinstance(x, Exception) and x[1] is not None]
        max_avg = max(avg_values, default=0.0)
        for sid, sig in signals.items():
            doing = cal_res.get(sid)
            if doing is not None:
                sig.doing = doing
                sig.evidence.append(f"Bangumi 日历 doing {doing}")
            if sid in trend_res:
                rank, collects = trend_res[sid]
                sig.trending_rank = rank
                sig.trending_collects = collects
                sig.evidence.append(f"Bangumi trending 第 {rank} 位")
            for row in ep_res:
                if isinstance(row, Exception):
                    continue
                ep_sid, avg, peak = row
                if ep_sid == sid:
                    sig.episode_comment_avg = avg
                    sig.episode_comment_peak = peak
                    if peak is not None:
                        sig.evidence.append(f"分集讨论峰值 {peak} 条")
                    break
            doing_norm = math.log1p(doing or 0) / math.log1p(max_doing) if max_doing else 0.0
            trend_norm = 0.0
            if sig.trending_rank:
                trend_norm = max(0.0, 1.0 - ((sig.trending_rank - 1) / 24.0))
            disc_norm = math.log1p(sig.episode_comment_avg or 0) / math.log1p(max_avg) if max_avg else 0.0
            sig.hotness = round(0.5 * doing_norm + 0.3 * trend_norm + 0.2 * disc_norm, 4)
            sig.level = "surge" if sig.hotness >= 0.78 else "hot" if sig.hotness >= 0.55 else "warm" if sig.hotness >= 0.25 else "none"
        return signals

    async def _enrich_pre_air_hype(self, items: list["SeasonGuideItem"]) -> None:
        """hot 模式 top 条目补开播前期待度（netaba.re 播前 wish 快照）。
        失败静默——期待度是加分信息，不能拖垮导视主流程。"""
        from ..netabare.tool import SubjectTrendArgs, SubjectTrendTool

        tool = SubjectTrendTool()
        results = await gather_limited(
            [tool.run(SubjectTrendArgs(subject_id=item.subject_id)) for item in items],
            host="netabare",
            return_exceptions=True,
        )
        for item, res in zip(items, results, strict=False):
            if isinstance(res, Exception) or not getattr(res, "ok", False) or res.data is None:
                continue
            if res.data.pre_air_wish:
                item.pre_air_wish = res.data.pre_air_wish
                item.hotness_evidence.append(f"开播前想看 {res.data.pre_air_wish} 人")

    async def _profile_tags(self, username: str | None) -> tuple[bool, list[str], list[dict]]:
        try:
            user = username
            if not user:
                me = await self.client.get_me()
                user = me.get("username") or str(me.get("id"))
            items = await self.client.get_all_user_collections(
                user, SUBJECT_TYPE["anime"], collection_type=None, max_items=1000
            )
        except Exception:  # noqa: BLE001
            return False, [], []
        profile = compute_taste_profile(user, items)
        return True, [t["tag"] for t in profile.top_tags[:10]], items

    async def _verify_yuc_match(
        self, subject: SubjectBrief, yuc: YucAnime | None, confidence: float, matched_by: str
    ) -> tuple[float, str]:
        if not yuc or confidence >= 0.8:
            return confidence, matched_by
        for title in (yuc.title_jp, yuc.title_cn):
            if not title:
                continue
            try:
                raw = await self.client.search_subjects(title, SUBJECT_TYPE["anime"], limit=8)
            except Exception:  # noqa: BLE001
                continue
            for s in raw.get("data") or []:
                if s.get("id") == subject.id:
                    return 0.96, f"bangumi_search:{title}"
        return confidence, matched_by

    async def _collect_guide_comment_digests(
        self,
        guide_query: str,
        guide_links: list[GuideVideoLink],
        video_limit: int,
        comment_limit: int,
    ) -> list[GuideCommentDigest]:
        digests: list[GuideCommentDigest] = []
        videos = []
        seen: set[str] = set()
        for link in guide_links:
            for video in link.verified_hits:
                key = video.bvid or str(video.aid or video.url)
                if not video.aid or key in seen:
                    continue
                seen.add(key)
                videos.append(video)
        videos = videos[:video_limit]
        comment_results = await gather_limited(
            [
                self._bili_comments_tool.run(BiliVideoCommentsArgs(aid=video.aid, query=guide_query, limit=comment_limit))
                for video in videos
                if video.aid
            ],
            host="bilibili",
        )
        for video, comments in zip(videos, comment_results, strict=False):
            if isinstance(comments, Exception):
                continue
            if not comments.ok or not comments.data:
                continue
            digests.append(
                GuideCommentDigest(
                    video_title=video.title,
                    author=video.author,
                    url=video.url,
                    aid=video.aid,
                    count=comments.data.count,
                    opinion_summary=comments.data.opinion_summary,
                    caveats=comments.data.caveats,
                )
            )
            if len(digests) >= video_limit:
                break
        return digests

    @staticmethod
    def _guide_video_key(hit: GuideVideoHit) -> str:
        return str(hit.bvid or hit.aid or hit.url)

    @staticmethod
    def _content_type_allowed(content_type: str, mode: str) -> bool:
        if mode == "preseason":
            return content_type == "preseason_guide"
        if mode == "hot":
            return content_type in {"preseason_guide", "airing_review"}
        return content_type in {"preseason_guide", "airing_review", "season_recap"}

    @staticmethod
    def _publication_window_ok(
        pubdate: int | None,
        content_type: str,
        year: int,
        month: int,
    ) -> bool:
        if not pubdate:
            return False
        try:
            published = datetime.fromtimestamp(int(pubdate), timezone.utc)
        except (OSError, OverflowError, TypeError, ValueError):
            return False
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end_month = month + 3
        end_year = year
        if end_month > 12:
            end_month -= 12
            end_year += 1
        end = datetime(end_year, end_month, 1, tzinfo=timezone.utc)
        if content_type == "preseason_guide":
            return start - timedelta(days=180) <= published <= start + timedelta(days=21)
        if content_type == "airing_review":
            return start - timedelta(days=14) <= published <= end + timedelta(days=21)
        if content_type == "season_recap":
            return start + timedelta(days=30) <= published <= end + timedelta(days=120)
        return False

    def _normalize_whitelist_guides(
        self,
        links: list[GuideVideoLink],
        *,
        preferred_sources: list[str],
        mode: str,
        year: int,
        month: int,
    ) -> tuple[list[GuideVideoLink], list[GuideVideoLink]]:
        published: list[GuideVideoLink] = []
        pending: list[GuideVideoLink] = []
        preferred = set(preferred_sources)
        for link in links:
            source = "preferred" if link.up_name in preferred else "whitelist"
            link.discovery_source = source
            link.matched_whitelist = True
            link.trust_tier = "preferred" if source == "preferred" else "known"
            kept: list[GuideVideoHit] = []
            for hit in link.verified_hits:
                hit.discovery_source = source
                hit.matched_whitelist = True
                hit.trust_tier = (
                    "preferred" if source == "preferred"
                    else "content_verified" if hit.content_verified
                    else "known"
                )
                if not self._content_type_allowed(hit.content_type, mode):
                    continue
                if not self._publication_window_ok(hit.pubdate, hit.content_type, year, month):
                    continue
                kept.append(hit)
            link.verified_hits = kept
            link.verified = bool(kept)
            if kept:
                link.publication_status = "published"
                published.append(link)
            else:
                if link.publication_status == "published":
                    link.publication_status = "rejected"
                    link.verification_note = "视频已发布，但类型或发布时间不符合本轮导视阶段。"
                pending.append(link)
        return published, pending

    def _discovered_link(
        self,
        video: BiliVideoMeta,
        *,
        preferred_sources: list[str],
    ) -> GuideVideoLink:
        source = (
            "preferred" if video.author in set(preferred_sources)
            else "whitelist" if video.matched_whitelist
            else "discovered"
        )
        trust_tier = (
            "preferred" if source == "preferred"
            else "content_verified" if video.content_verified
            else "known" if video.matched_whitelist
            else "metadata_verified"
        )
        up_url = f"https://space.bilibili.com/{video.mid}" if video.mid else ""
        hit = GuideVideoHit(
            title=video.title,
            url=video.url,
            aid=video.aid,
            bvid=video.bvid,
            mid=video.mid,
            author=video.author,
            thumbnail_url=video.thumbnail_url,
            play=video.play,
            danmaku=video.danmaku,
            pubdate=video.pubdate,
            content_type=video.content_type,
            content_type_reason=video.content_type_reason,
            discovery_source=source,
            matched_whitelist=video.matched_whitelist,
            trust_tier=trust_tier,
            match_confidence=video.match_confidence,
            match_reason=video.match_reason,
            verification_status=video.verification_status,
            content_verified=video.content_verified,
            content_match_confidence=video.content_match_confidence,
            content_match_reason=video.content_match_reason,
            transcript_source=video.transcript_source,
            content_mentions=video.content_mentions,
        )
        return GuideVideoLink(
            label=f"{video.title} · {video.author}",
            url=video.url,
            up_name=video.author,
            up_url=up_url,
            positioning=(
                "你的首选导视来源" if source == "preferred"
                else "B站导视白名单来源" if source == "whitelist"
                else "全站严格筛选补充发现"
            ),
            match_reason=video.match_reason,
            confidence="high" if video.match_confidence >= 0.82 else "medium",
            discovery_source=source,
            matched_whitelist=video.matched_whitelist,
            trust_tier=trust_tier,
            verified=True,
            verified_hits=[hit],
            verification_query=video.url,
            verification_note=(
                "已通过字幕正文与视频详情核验。"
                if video.content_verified else
                "已通过标题、季度、发布时间与视频详情核验；暂无可用字幕正文。"
            ),
            publication_status="published",
        )

    def _merge_discovered_guides(
        self,
        preferred_guides: list[GuideVideoLink],
        discovered: list[BiliVideoMeta],
        *,
        preferred_sources: list[str],
        mode: str,
        year: int,
        month: int,
        limit: int,
    ) -> list[GuideVideoLink]:
        merged = list(preferred_guides)
        seen = {
            self._guide_video_key(hit)
            for link in merged
            for hit in link.verified_hits
        }
        for video in discovered:
            if not video.verified:
                continue
            if not self._content_type_allowed(video.content_type, mode):
                continue
            if not self._publication_window_ok(video.pubdate, video.content_type, year, month):
                continue
            if video.matched_whitelist:
                accepted = video.match_confidence >= 0.62
            else:
                hard_title_match = (
                    str(year) in video.title
                    and bool(re.search(rf"(?<!\d){month}\s*月", video.title))
                    and video.content_type != "general"
                )
                accepted = (
                    video.content_verified and video.match_confidence >= 0.62
                ) or (
                    video.verification_status == "view_verified"
                    and (
                        video.match_confidence >= 0.82
                        or (hard_title_match and video.match_confidence >= 0.58)
                    )
                )
            if not accepted:
                continue
            link = self._discovered_link(video, preferred_sources=preferred_sources)
            key = self._guide_video_key(link.verified_hits[0])
            if key in seen:
                continue
            seen.add(key)
            merged.append(link)

        source_rank = {"preferred": 0, "whitelist": 1, "discovered": 2}
        kind_rank = (
            {"preseason_guide": 0, "airing_review": 1, "season_recap": 2, "general": 3}
            if mode == "preseason" else
            {"airing_review": 0, "preseason_guide": 1, "season_recap": 2, "general": 3}
        )
        merged.sort(key=lambda link: (
            source_rank.get(link.discovery_source, 3),
            kind_rank.get(link.verified_hits[0].content_type if link.verified_hits else "general", 3),
            -int(bool(link.verified_hits and link.verified_hits[0].content_verified)),
            -(link.verified_hits[0].match_confidence if link.verified_hits else 0.0),
        ))
        return merged[:limit]

    async def run(self, args: SeasonGuideBriefArgs) -> ToolResult[SeasonGuideBriefResult]:
        phase = _season_phase(args.year, args.month)
        mode = _effective_mode(args.mode, phase)
        guide_preferences = self._guide_source_preferences(args)
        guide_query = f"{args.year}年{args.month}月 新番导视"
        discovery_task = (
            asyncio.create_task(self._bili_search_tool.run(BiliGuideSearchArgs(
                query=guide_query,
                tags=(args.focus_tags or [])[:2],
                whitelist_only=False,
                limit=min(8, max(args.guide_result_limit * 2, 6)),
            )))
            if args.verify_guide_videos and args.guide_verify_limit > 0 and args.discover_beyond_whitelist
            else None
        )
        core_coroutines = [
            self._season_tool._fetch_season(args.year, args.month, args.limit),
            self._yuc_tool.run(YucSeasonArgs(year=args.year, month=args.month, limit=80)),
            self._profile_tags(args.username),
        ]
        initial_results = await asyncio.gather(*core_coroutines)
        season, yuc_res = initial_results[:2]
        personalized, profile_tags, collection_rows = initial_results[2]
        user_collections = collection_map(collection_rows)
        yuc_items = yuc_res.data.anime if yuc_res.ok and yuc_res.data else []
        wanted = list(dict.fromkeys((args.focus_tags or []) + profile_tags))
        season_guide_links = _guide_links(
            guide_query,
            "season",
            8,
            wanted,
            preferred_sources=guide_preferences or None,
        )
        verify_guides_task = (
            asyncio.create_task(verify_guide_video_links(
                guide_query,
                season_guide_links,
                title_aliases=[guide_query, f"{args.year}年{args.month}月新番"],
                tags=wanted,
                max_links=min(3, args.guide_verify_limit),
                max_hits_per_link=1,
                min_confidence=0.5,
                verify_content=True,
                content_verify_limit=min(3, args.guide_verify_limit),
            ))
            if args.verify_guide_videos and args.guide_verify_limit > 0
            else None
        )
        hot_signals = (
            await self._hot_signal_maps(season.anime[: args.limit])
            if mode != "preseason" else
            {subject.id: HotSignals() for subject in season.anime[: args.limit]}
        )
        relation_memo = SeriesRelationMemo(self.client)

        async def build_item(subject: SubjectBrief) -> SeasonGuideItem:
            series_status = (
                await inspect_series_candidate(
                    self.client,
                    subject.id,
                    user_collections,
                    collection_available=True,
                    subject_name=subject.name_cn or subject.name,
                    max_hops=6,
                    relation_memo=relation_memo,
                )
                if personalized else None
            )
            yuc, match_confidence, matched_by = _match_yuc(subject, yuc_items)
            match_confidence, matched_by = await self._verify_yuc_match(subject, yuc, match_confidence, matched_by)
            bangumi_tags: list[str] = []
            if args.enrich_tags:
                try:
                    detail = await self.client.get_subject(subject.id)
                    bangumi_tags = [t.get("name", "") for t in (detail.get("tags") or []) if isinstance(t, dict)]
                except Exception:  # noqa: BLE001
                    bangumi_tags = []
            tags = _unique((yuc.tags if yuc else []) + bangumi_tags)
            fit, match_tags, reason, fit_score = _fit_item(tags, subject.score, wanted)
            item_routes = _guide_links(
                subject.name_cn or subject.name,
                "review",
                3,
                tags,
                preferred_sources=guide_preferences or None,
            )
            if (
                args.verify_item_videos
                and args.verify_guide_videos
                and args.guide_verify_limit > 0
                and mode != "preseason"
            ):
                item_routes = await verify_guide_video_links(
                    subject.name_cn or subject.name,
                    item_routes,
                    title_aliases=_unique([
                        subject.name_cn or "",
                        subject.name or "",
                        yuc.title_cn if yuc else "",
                        yuc.title_jp if yuc else "",
                    ]),
                    tags=tags,
                    max_links=args.guide_verify_limit,
                    max_hits_per_link=1,
                )
            vertical_map: dict[str, SubjectVertical] = {}
            for link in item_routes:
                for vertical in link.verticals:
                    old = vertical_map.get(vertical.name)
                    if old is None or vertical.confidence > old.confidence:
                        vertical_map[vertical.name] = vertical
            item_guides = [link for link in item_routes if link.publication_status == "published"]
            hot = hot_signals.get(subject.id) or HotSignals()
            return SeasonGuideItem(
                subject_id=subject.id,
                title=subject.name_cn or subject.name,
                title_jp=subject.name,
                yuc_title=yuc.title_cn if yuc else None,
                match_confidence=match_confidence,
                matched_by=matched_by,
                mapping_warning=(
                    "放送资料与 Bangumi 标题只做到了弱匹配，制作和播出信息请谨慎参考"
                    if yuc and match_confidence < 0.8
                    else None
                ),
                bangumi_score=subject.score,
                rank=subject.rank,
                air_date=subject.date,
                broadcast=yuc.broadcast if yuc else None,
                studio=yuc.studio if yuc else None,
                tags=tags,
                match_tags=match_tags,
                fit_score=fit_score,
                fit=fit,
                reason=reason,
                evidence=_evidence(subject, yuc, match_tags, fit, match_confidence) + hot.evidence[:3],
                hotness=hot.hotness,
                hotness_level=hot.level,
                doing=hot.doing,
                trending_rank=hot.trending_rank,
                trending_collects=hot.trending_collects,
                episode_comment_avg=hot.episode_comment_avg,
                episode_comment_peak=hot.episode_comment_peak,
                hotness_evidence=hot.evidence,
                verticals=sorted(vertical_map.values(), key=lambda x: -x.confidence),
                guide_videos=item_guides,
                official_url=yuc.official_url if yuc else None,
                pv_url=yuc.pv_url if yuc else None,
                bili_url=yuc.bili_url if yuc else None,
                stream_urls=[x.model_dump(mode="json") for x in (yuc.stream_urls if yuc else [])],
                image=subject.image or (yuc.image if yuc else None),
                series_status=series_status,
            )
        item_results = await gather_limited([build_item(subject) for subject in season.anime[: args.limit]], host="bangumi")
        items = [item for item in item_results if isinstance(item, SeasonGuideItem)]
        dropped = len(item_results) - len(items)
        def series_priority(item: SeasonGuideItem) -> int:
            status = item.series_status
            if status is None:
                return 0
            if not status.prerequisites_satisfied:
                return 3
            if status.collection_state == "watching":
                return -2
            if status.collection_state in {"wishlist", "uncollected"}:
                return -1
            if status.collection_state == "watched":
                return 2
            if status.collection_state in {"on_hold", "dropped"}:
                return 1
            return 0
        if mode == "hot":
            items.sort(key=lambda x: (series_priority(x), -(x.hotness * 0.7 + min(x.fit_score / 8.0, 1.0) * 0.3), -x.hotness, -(x.bangumi_score or 0)))
            await self._enrich_pre_air_hype(items[:12])
        elif mode == "preseason":
            await self._enrich_pre_air_hype(items)
            max_wish = max((item.pre_air_wish or 0 for item in items), default=0)

            def preseason_score(item: SeasonGuideItem) -> float:
                taste = min(item.fit_score / 8.0, 1.0)
                hype = math.log1p(item.pre_air_wish or 0) / math.log1p(max_wish) if max_wish else 0.0
                readiness = sum(bool(value) for value in (item.broadcast, item.studio, item.pv_url)) / 3.0
                return 0.5 * taste + 0.3 * hype + 0.2 * readiness

            items.sort(key=lambda x: (series_priority(x), -preseason_score(x), -_fit_rank(x.fit), -(x.pre_air_wish or 0)))
        else:
            items.sort(key=lambda x: (series_priority(x), -_fit_rank(x.fit), -x.fit_score, -x.hotness, -(x.bangumi_score or 0)))

        discovery_warnings: list[str] = []
        discovered_videos: list[BiliVideoMeta] = []
        if verify_guides_task is not None:
            season_guide_links = await verify_guides_task
        discovered_result = await discovery_task if discovery_task is not None else None
        if discovered_result is not None:
            if discovered_result.ok and discovered_result.data:
                discovered_videos = discovered_result.data.videos
                discovery_warnings = discovered_result.data.warnings
            elif discovered_result.error:
                discovery_warnings = [discovered_result.error]
        published_guides, pending_guides = self._normalize_whitelist_guides(
            season_guide_links,
            preferred_sources=guide_preferences,
            mode=mode,
            year=args.year,
            month=args.month,
        )
        published_guides = self._merge_discovered_guides(
            published_guides,
            discovered_videos,
            preferred_sources=guide_preferences,
            mode=mode,
            year=args.year,
            month=args.month,
            limit=args.guide_result_limit,
        )
        published_authors = {link.up_name for link in published_guides}
        pending_guides = [link for link in pending_guides if link.up_name not in published_authors]
        guide_comment_digests = (
            await self._collect_guide_comment_digests(
                guide_query,
                published_guides,
                args.comment_video_limit,
                args.comment_limit,
            )
            if args.include_video_comments
            else []
        )
        result = SeasonGuideBriefResult(
            season=season.season,
            mode=mode,
            requested_mode=args.mode,
            phase=phase,
            count=len(items),
            personalized=personalized,
            profile_tags=profile_tags,
            focus_tags=args.focus_tags or [],
            items=items,
            guide_videos=published_guides,
            pending_guide_sources=pending_guides,
            guide_source_preferences=guide_preferences,
            guide_discovery_warnings=discovery_warnings,
            guide_comment_digests=guide_comment_digests,
            notes=[
                "Bangumi 提供条目/评分/收藏锚点，yuc 提供放送表/官网/PV/制作阵容。",
                "本季分诊以 Bangumi 已收录且有播出日期的条目为骨架，yuc 仅补充放送/制作信息；Bangumi 未收录的番不会出现在分诊里，冷门或尚未收录的新番可能遗漏，可对照 yuc.wiki 原表。",
                "默认只核验整季导视，不逐部搜索漫评；只有明确要求逐部视频时才开启，以兼顾准确率与等待时间。",
                "登录后逐部合并系列前作状态；缺少必要前作的续作会降序并明确提示，不会因为看过任意一季就直接放行更后面的季度。",
                (
                    "B站导视评论已抽样读取；它们是话语源，不是事实源，且可能包含剧透/玩梗。"
                    if guide_comment_digests else
                    "季度视频采用偏好/白名单优先、全站严格补充发现；未发布、阶段不符、搜索不可用或正文不匹配的候选不参与排序。"
                ),
                "播前导视、热播漫评和季度复盘会分别标注；非白名单视频必须通过更严格的标题、季度、发布时间与视频详情门槛。",
                (
                    "hot 模式已融合 Bangumi doing / trending / 分集讨论量；热度是追番参考，不等于质量。"
                    if mode == "hot" else
                    "播前模式不使用尚不存在的分集热度，优先口味、播前期待和制作/PV资料完整度。"
                    if mode == "preseason" else
                    "guide 模式优先口味分诊；热度字段仍会附带给前端作为徽章。"
                ),
                *(
                    [f"有 {dropped} 部条目在并发补全时失败被跳过，本季清单可能不完整。"]
                    if dropped else []
                ),
            ],
        )
        sources = [
            Citation(title=i.title, url=f"https://bgm.tv/subject/{i.subject_id}", source="bangumi", image=i.image)
            for i in items[:5]
        ]
        if yuc_res.ok and yuc_res.data:
            sources.append(Citation(title=f"yuc.wiki — {season.season}", url=yuc_res.data.source_url, source="yuc"))
        sources.extend(
            Citation(
                title=f"Bilibili — {hit.title}",
                url=hit.url,
                source="bilibili",
                image=hit.thumbnail_url,
            )
            for link in published_guides[:4]
            for hit in link.verified_hits[:1]
        )
        return ToolResult(ok=True, data=result, sources=sources)


def build_season_tools(client: BangumiClient, ltm: LongTermMemory | None = None) -> list[Tool]:
    return [ListSeasonAnimeTool(client), ListYearAnimeTool(client), SeasonGuideBriefTool(client, ltm)]
