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
from ...profile import compute_taste_profile
from ..bangumi.client import SUBJECT_TYPE, BangumiClient
from ..bangumi.models import SubjectBrief
from ..videos.tool import (
    BiliGuideSearchArgs,
    BiliVideoCommentsArgs,
    GetBiliVideoCommentsTool,
    GuideVideoLink,
    SearchBiliGuideVideosTool,
    _guide_links,
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
    limit: int = Field(10, ge=1, le=20)
    username: str | None = Field(None, description="Bangumi 用户名；不传则尝试当前账号，失败就做非个性化导视")
    focus_tags: list[str] | None = Field(None, description="用户临时偏好，如 ['百合','日常','治愈']")
    enrich_tags: bool = Field(True, description="是否补 Bangumi 详情标签；默认开，能提升分诊质量")
    include_video_comments: bool = Field(
        False,
        description="是否抽样读取白名单 B站导视视频评论；用于观众期待/担心点，不作为事实源",
    )
    comment_video_limit: int = Field(2, ge=1, le=3, description="最多读取几个导视视频的评论")
    comment_limit: int = Field(20, ge=5, le=50, description="每个导视视频最多读取多少条评论")


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
    title_jp: str | None = None
    yuc_title: str | None = None
    match_confidence: float = 0.0
    matched_by: str = "bangumi_only"
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
    guide_videos: list[GuideVideoLink] = Field(default_factory=list)
    official_url: str | None = None
    pv_url: str | None = None
    image: str | None = None


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
    count: int
    personalized: bool = False
    profile_tags: list[str] = Field(default_factory=list)
    focus_tags: list[str] = Field(default_factory=list)
    items: list[SeasonGuideItem] = Field(default_factory=list)
    guide_videos: list[GuideVideoLink] = Field(default_factory=list)
    guide_comment_digests: list[GuideCommentDigest] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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


class SeasonGuideBriefTool(Tool):
    name = "season_guide_brief"
    description = (
        "聚合某季新番导视：Bangumi 条目/评分 + yuc 放送表/制作阵容 + B站白名单导视入口 + 用户标签分诊。"
        "用于『这季怎么追 / 新番导视 / 按我口味看 7 月番』。"
        "默认只返回导视入口；include_video_comments=true 时会抽样读取少量公开视频评论，作为话语源摘要。"
    )
    args_model = SeasonGuideBriefArgs
    result_model = SeasonGuideBriefResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client
        self._season_tool = ListSeasonAnimeTool(client)
        self._yuc_tool = ListYucSeasonTool()
        self._bili_search_tool = SearchBiliGuideVideosTool()
        self._bili_comments_tool = GetBiliVideoCommentsTool()

    async def _profile_tags(self, username: str | None) -> tuple[bool, list[str]]:
        try:
            user = username
            if not user:
                me = await self.client.get_me()
                user = me.get("username") or str(me.get("id"))
            items = await self.client.get_all_user_collections(
                user, SUBJECT_TYPE["anime"], collection_type=2, max_items=1000
            )
        except Exception:  # noqa: BLE001
            return False, []
        profile = compute_taste_profile(user, items)
        return True, [t["tag"] for t in profile.top_tags[:10]]

    async def _verify_yuc_match(
        self, subject: SubjectBrief, yuc: YucAnime | None, confidence: float, matched_by: str
    ) -> tuple[float, str]:
        if not yuc:
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
        self, guide_query: str, wanted: list[str], video_limit: int, comment_limit: int
    ) -> list[GuideCommentDigest]:
        search = await self._bili_search_tool.run(
            BiliGuideSearchArgs(query=guide_query, tags=wanted[:5], whitelist_only=True, limit=max(video_limit, 3))
        )
        if not search.ok or not search.data:
            return []
        digests: list[GuideCommentDigest] = []
        for video in search.data.videos:
            if not video.aid:
                continue
            comments = await self._bili_comments_tool.run(
                BiliVideoCommentsArgs(aid=video.aid, query=guide_query, limit=comment_limit)
            )
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

    async def run(self, args: SeasonGuideBriefArgs) -> ToolResult[SeasonGuideBriefResult]:
        season = await self._season_tool._fetch_season(args.year, args.month, args.limit)
        yuc_res = await self._yuc_tool.run(YucSeasonArgs(year=args.year, month=args.month, limit=80))
        yuc_items = yuc_res.data.anime if yuc_res.ok and yuc_res.data else []
        personalized, profile_tags = await self._profile_tags(args.username)
        wanted = list(dict.fromkeys((args.focus_tags or []) + profile_tags))

        items: list[SeasonGuideItem] = []
        for subject in season.anime[: args.limit]:
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
            item_guides = _guide_links(subject.name_cn or subject.name, "review", 3, tags)
            items.append(
                SeasonGuideItem(
                    subject_id=subject.id,
                    title=subject.name_cn or subject.name,
                    title_jp=subject.name,
                    yuc_title=yuc.title_cn if yuc else None,
                    match_confidence=match_confidence,
                    matched_by=matched_by,
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
                    evidence=_evidence(subject, yuc, match_tags, fit, match_confidence),
                    guide_videos=item_guides,
                    official_url=yuc.official_url if yuc else None,
                    pv_url=yuc.pv_url if yuc else None,
                    image=subject.image or (yuc.image if yuc else None),
                )
            )
        items.sort(key=lambda x: (-_fit_rank(x.fit), -x.fit_score, -(x.bangumi_score or 0)))

        guide_query = f"{args.year}年{args.month}月 新番导视"
        guide_comment_digests = (
            await self._collect_guide_comment_digests(
                guide_query, wanted, args.comment_video_limit, args.comment_limit
            )
            if args.include_video_comments
            else []
        )
        result = SeasonGuideBriefResult(
            season=season.season,
            count=len(items),
            personalized=personalized,
            profile_tags=profile_tags,
            focus_tags=args.focus_tags or [],
            items=items,
            guide_videos=_guide_links(guide_query, "season", 6, wanted),
            guide_comment_digests=guide_comment_digests,
            notes=[
                "Bangumi 提供条目/评分/收藏锚点，yuc 提供放送表/官网/PV/制作阵容。",
                (
                    "B站导视评论已抽样读取；它们是话语源，不是事实源，且可能包含剧透/玩梗。"
                    if guide_comment_digests else
                    "B站导视默认仅返回白名单 UP 搜索入口；需要观众期待/担心点时可启用 include_video_comments。"
                ),
            ],
        )
        sources = [
            Citation(title=i.title, url=f"https://bgm.tv/subject/{i.subject_id}", source="bangumi", image=i.image)
            for i in items[:5]
        ]
        if yuc_res.ok and yuc_res.data:
            sources.append(Citation(title=f"yuc.wiki — {season.season}", url=yuc_res.data.source_url, source="yuc"))
        return ToolResult(ok=True, data=result, sources=sources)


def build_season_tools(client: BangumiClient) -> list[Tool]:
    return [ListSeasonAnimeTool(client), ListYearAnimeTool(client), SeasonGuideBriefTool(client)]
