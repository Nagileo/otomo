"""Phase 19 product-loop aggregate tools.

These tools are intentionally higher-level than atomic API wrappers: they
package common product workflows into stable panel payloads while keeping the
underlying sources traceable.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...memory import LongTermMemory
from ...memory.models import MemorySummary, memory_summary
from .._concurrency import gather_limited
from ..bangumi.client import SUBJECT_TYPE, BangumiClient
from ..bangumi.models import RelatedSubject, SubjectBrief, SubjectDetail
from ..bangumi.tools import GetSubjectRelationsTool, SubjectRelationsArgs
from ..calendar.tool import AiringProgressArgs, AiringProgressTool, BroadcastCalendarArgs, BroadcastCalendarTool
from ..discovery.tool import EpisodeBuzzRadarTool, EpisodeRadarArgs
from ..animethemes.tool import AnimeThemesArgs, SearchAnimeThemesTool
from ..release.tool import AnimeReleaseFeedsArgs, GetAnimeReleaseFeedsTool
from ..review.tool import ReviewSubjectArgs, ReviewSubjectTool
from ..watch.tool import WhereToWatchArgs, WhereToWatchTool
from ..watchorder.tool import WatchCopilotArgs, WatchCopilotTool, WatchOrderArgs, WatchOrderTool, _resolve_username


class ProductSection(BaseModel):
    title: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class WatchCockpitArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则用当前账号")
    limit: int = Field(8, ge=3, le=20)
    include_on_hold: bool = True
    include_radar: bool = True


class WatchCockpitResult(BaseModel):
    username: str
    today: str
    sections: list[ProductSection] = Field(default_factory=list)
    subscription: dict[str, Any] = Field(default_factory=dict)
    next_actions: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    memory: MemorySummary | None = None


class SubjectDossierArgs(BaseModel):
    subject_id: int | None = Field(None, description="Bangumi subject_id；优先使用")
    title: str = Field("", description="subject_id 为空时按标题搜索")
    subject_type: Literal["anime", "book", "music", "game", "real"] | None = None
    spoiler_level: Literal["none", "mild", "full"] = "none"
    include_release: bool = Field(True, description="anime 条目是否补 release/RSS 入口")


class SubjectDossierResult(BaseModel):
    subject: dict[str, Any]
    sections: list[ProductSection] = Field(default_factory=list)
    quick_actions: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class FranchiseMapArgs(BaseModel):
    subject_id: int | None = Field(None, description="Bangumi subject_id；优先使用")
    title: str = Field("", description="subject_id 为空时按标题搜索")
    subject_type: Literal["anime", "book", "music", "game", "real"] | None = "anime"
    depth: int = Field(2, ge=1, le=3)
    limit: int = Field(60, ge=10, le=120)


class FranchiseNode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    type: int | None = None
    type_name: str = ""
    date: str = ""
    score: float | None = None
    rank: int | None = None
    image: str | None = None


class FranchiseEdge(BaseModel):
    source: int
    target: int
    relation: str
    type_name: str = ""


class FranchiseMapResult(BaseModel):
    seed: FranchiseNode
    nodes: list[FranchiseNode] = Field(default_factory=list)
    edges: list[FranchiseEdge] = Field(default_factory=list)
    groups: dict[str, list[int]] = Field(default_factory=dict)
    suggested_order: list[int] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class MonthlyWatchReportArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则用当前账号")
    period: Literal["month", "year"] = Field("month", description="月度报告或年度总结（Wrapped）")
    year: int | None = None
    month: int | None = Field(None, ge=1, le=12)
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    limit: int = Field(12, ge=3, le=30)


class MonthlyWatchReportResult(BaseModel):
    username: str
    period: str = "month"
    year: int
    month: int
    subject_type: str
    sections: list[ProductSection] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)


class AnimeMusicThemesArgs(BaseModel):
    subject_id: int | None = Field(None, description="动画 Bangumi subject_id；优先使用")
    title: str = Field("", description="subject_id 为空时按标题搜索动画")
    limit: int = Field(12, ge=1, le=24)


class BangumiMusicLink(BaseModel):
    id: int
    name: str
    relation: str = ""
    type_name: str = "music"
    score: float | None = None
    rank: int | None = None
    image: str | None = None
    url: str = ""


class AnimeMusicThemeResult(BaseModel):
    subject: dict[str, Any]
    bangumi_music: list[BangumiMusicLink] = Field(default_factory=list)
    animethemes_entries: list[dict[str, Any]] = Field(default_factory=list)
    fused: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


def _image(raw: dict[str, Any]) -> str | None:
    images = raw.get("images") or {}
    return images.get("common") or images.get("medium") or images.get("grid")


def _title(raw: dict[str, Any]) -> str:
    return str(raw.get("name_cn") or raw.get("name") or f"subject {raw.get('id')}")


def _subject_type_name(value: int | None) -> str:
    return {1: "book", 2: "anime", 3: "music", 4: "game", 6: "real"}.get(value or 0, "")


async def _resolve_subject(client: BangumiClient, args: SubjectDossierArgs | FranchiseMapArgs) -> dict[str, Any] | None:
    if args.subject_id:
        return await client.get_subject(args.subject_id)
    if not args.title.strip():
        return None
    stype = SUBJECT_TYPE.get(args.subject_type) if args.subject_type else None
    raw = await client.search_subjects(args.title, stype, limit=5)
    rows = raw.get("data") or []
    if not rows:
        return None
    exact = [x for x in rows if _title(x) == args.title or x.get("name") == args.title]
    return exact[0] if exact else rows[0]


def _subject_payload(raw: dict[str, Any]) -> dict[str, Any]:
    rating = raw.get("rating") or {}
    return {
        "id": raw.get("id"),
        "name": _title(raw),
        "name_jp": raw.get("name") or "",
        "type": raw.get("type"),
        "type_name": _subject_type_name(raw.get("type")),
        "date": raw.get("date") or "",
        "score": rating.get("score"),
        "rank": rating.get("rank"),
        "summary": (raw.get("summary") or "")[:600],
        "image": _image(raw),
        "tags": [t.get("name") for t in (raw.get("tags") or []) if isinstance(t, dict) and t.get("name")][:15],
    }


def _norm_music_title(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _norm_alias(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _alias_match(candidate: str, aliases: list[str]) -> bool:
    key = _norm_alias(candidate)
    if not key:
        return False
    for alias in aliases:
        ak = _norm_alias(alias)
        if len(ak) < 4:
            continue
        if key == ak or key in ak or ak in key:
            return True
    return False


def _find_music_title_match(song_title: str, music_links: list[BangumiMusicLink]) -> BangumiMusicLink | None:
    song_key = _norm_music_title(song_title)
    if not song_key:
        return None
    for music in music_links:
        mkey = _norm_music_title(music.name)
        if song_key and mkey and (song_key in mkey or mkey in song_key):
            return music
    return None


def _theme_kind(text: str) -> str:
    s = str(text or "").upper()
    if "OP" in s or "片头" in str(text) or "片頭" in str(text) or "オープニング" in str(text):
        return "OP"
    if "ED" in s or "片尾" in str(text) or "エンディング" in str(text):
        return "ED"
    if any(k in s for k in ("OST", "SOUNDTRACK", "原声", "サントラ")):
        return "OST"
    if any(k in s for k in ("CHARACTER", "角色歌", "キャラ")):
        return "角色歌"
    return "music"


_STATUS_NAME = {
    "1": "想看",
    "2": "看过",
    "3": "在看",
    "4": "搁置",
    "5": "抛弃",
    "unknown": "未知",
}


class AnimeMusicThemesTool(Tool):
    name = "anime_music_themes"
    description = (
        "融合动画音乐信息：先用 Bangumi relation 找 music 条目，再用 AnimeThemes 补 OP/ED 曲名、歌手和视频入口。"
        "用于『这番 OP/ED/theme song/谁唱的/相关音乐条目』。"
    )
    args_model = AnimeMusicThemesArgs
    result_model = AnimeMusicThemeResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client
        self.animethemes = SearchAnimeThemesTool()

    async def run(self, args: AnimeMusicThemesArgs) -> ToolResult[AnimeMusicThemeResult]:
        raw = await _resolve_subject(
            self.client,
            SubjectDossierArgs(subject_id=args.subject_id, title=args.title, subject_type="anime"),
        )
        if not raw:
            return ToolResult(ok=False, error="需要动画 subject_id 或可解析的 title")
        subject = _subject_payload(raw)
        sid = int(subject["id"])
        rel_rows = await self.client.get_subject_relations(sid)
        music_links: list[BangumiMusicLink] = []
        for rel in rel_rows or []:
            if rel.get("type") != SUBJECT_TYPE["music"] or not rel.get("id"):
                continue
            rating = rel.get("rating") or {}
            music_links.append(
                BangumiMusicLink(
                    id=int(rel["id"]),
                    name=str(rel.get("name_cn") or rel.get("name") or rel["id"]),
                    relation=str(rel.get("relation") or ""),
                    score=rating.get("score"),
                    rank=rating.get("rank"),
                    image=_image(rel),
                    url=f"https://bgm.tv/subject/{rel['id']}",
                )
            )
        at_queries: list[str] = []
        for q in (subject.get("name_jp"), subject.get("name"), args.title):
            qs = str(q or "").strip()
            if qs and qs not in at_queries:
                at_queries.append(qs)
        at_results = await gather_limited(
            [self.animethemes.run(AnimeThemesArgs(title=q, limit=args.limit)) for q in at_queries[:3]],
            host="animethemes",
        )
        at_entries: list[dict[str, Any]] = []
        seen_at: set[tuple[str, str, str]] = set()
        for res in at_results:
            if isinstance(res, Exception) or not res.ok or not res.data:
                continue
            for entry in res.data.entries:
                row = entry.model_dump(mode="json", exclude_none=True)
                key = (str(row.get("anime_title") or ""), str(row.get("theme_type") or ""), str(row.get("song_title") or ""))
                if key in seen_at:
                    continue
                seen_at.add(key)
                at_entries.append(row)
        fused: list[dict[str, Any]] = []
        visible_at_entries: list[dict[str, Any]] = []
        hidden_at_entries = 0
        subject_aliases = [
            str(subject.get("name") or ""),
            str(subject.get("name_jp") or ""),
            str(args.title or ""),
        ]
        for entry in at_entries:
            kind = _theme_kind(f"{entry.get('theme_type', '')}{entry.get('sequence', '')}")
            match = _find_music_title_match(str(entry.get("song_title") or ""), music_links)
            anime_match = _alias_match(str(entry.get("anime_title") or ""), subject_aliases) or _alias_match(str(entry.get("slug") or ""), subject_aliases)
            if not (match or anime_match):
                hidden_at_entries += 1
                continue
            visible_at_entries.append(entry)
            fused.append({
                "kind": kind,
                "theme_type": entry.get("theme_type"),
                "sequence": entry.get("sequence"),
                "song_title": entry.get("song_title"),
                "artists": entry.get("artists") or [],
                "animethemes_url": entry.get("page_url") or entry.get("video_url") or "",
                "video_url": entry.get("video_url") or "",
                "matched_bangumi_music_id": match.id if match else None,
                "matched_bangumi_music_name": match.name if match else "",
                "mapping_note": "Bangumi music 标题重叠" if match else "AnimeThemes 动画标题对齐",
            })
        # Bangumi 有 music relation 但 AnimeThemes 没匹配时，也保留为 fused entry，避免只看 OP/ED API 漏掉角色歌/OST。
        matched_ids = {x.get("matched_bangumi_music_id") for x in fused if x.get("matched_bangumi_music_id")}
        for music in music_links:
            if music.id in matched_ids:
                continue
            fused.append({
                "kind": _theme_kind(f"{music.relation} {music.name}"),
                "song_title": music.name,
                "artists": [],
                "bangumi_music_id": music.id,
                "bangumi_url": music.url,
                "relation": music.relation,
                "score": music.score,
                "rank": music.rank,
                "mapping_note": "Bangumi relation music 条目",
            })
        kind_order = {"OP": 0, "ED": 1, "OST": 2, "角色歌": 3, "music": 4}
        fused.sort(key=lambda x: (kind_order.get(str(x.get("kind") or "music"), 9), str(x.get("song_title") or "")))
        notes = [
            "Bangumi music relation 是社区锚点，适合关联专辑/角色歌/OST；AnimeThemes 适合 OP/ED 曲目与视频入口。",
            "AnimeThemes 条目只有在动画标题或曲名能对齐时才会进入融合列表，避免中文检索误配到其他动画。",
        ]
        caveats = []
        if not music_links:
            caveats.append("Bangumi 未返回 music relation，可能条目未维护或音乐条目未关联。")
        if not at_entries:
            caveats.append("AnimeThemes 未返回 OP/ED 条目，可能未收录或标题检索失败。")
        if hidden_at_entries:
            caveats.append(f"AnimeThemes 返回的 {hidden_at_entries} 条结果未能与 Bangumi 条目/音乐条目对齐，已隐藏以避免误配。")
        return ToolResult(
            ok=True,
            data=AnimeMusicThemeResult(
                subject=subject,
                bangumi_music=music_links[: args.limit],
                animethemes_entries=visible_at_entries[: args.limit],
                fused=fused[: args.limit],
                notes=notes,
                caveats=caveats,
            ),
            sources=[
                Citation(title=subject["name"], url=f"https://bgm.tv/subject/{sid}", source="bangumi", image=subject.get("image")),
                *[Citation(title=m.name, url=m.url, source="bangumi", image=m.image) for m in music_links[:4]],
                *[Citation(title=e.get("song_title") or e.get("anime_title") or "AnimeThemes", url=e.get("page_url") or e.get("video_url") or "", source="animethemes") for e in visible_at_entries[:4]],
            ][:10],
        )


class WatchCockpitTool(Tool):
    name = "watch_cockpit"
    description = "追番驾驶舱：聚合今日/本周放送、追番副驾、分集热度、订阅状态和下一步动作。"
    args_model = WatchCockpitArgs
    result_model = WatchCockpitResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm
        self.airing = AiringProgressTool(client)
        self.calendar = BroadcastCalendarTool(client)
        self.copilot = WatchCopilotTool(client)
        self.radar = EpisodeBuzzRadarTool(client)

    async def run(self, args: WatchCockpitArgs) -> ToolResult[WatchCockpitResult]:
        username = await _resolve_username(self.client, args.username)
        jobs = [
            self.airing.run(AiringProgressArgs(username=username, include_wishlist=True, limit=args.limit)),
            self.calendar.run(BroadcastCalendarArgs(day="week", only_mine=True, username=username, include_wishlist=True)),
            self.copilot.run(WatchCopilotArgs(username=username, limit=args.limit, include_on_hold=args.include_on_hold)),
        ]
        airing_res, calendar_res, copilot_res = await gather_limited(jobs, host="bangumi", return_exceptions=False)
        mem = self.ltm.load_user(username)
        sections: list[ProductSection] = []
        sources: list[Citation] = []
        airing_items = airing_res.data.items if airing_res.ok and airing_res.data else []
        sections.append(ProductSection(
            title="追番进度",
            items=[x.model_dump(mode="json", exclude_none=True) for x in airing_items[: args.limit]],
            notes=["落后集数来自 Bangumi ep_status 与正片 airdate；国内上架可能有时差。"],
        ))
        if airing_res.sources:
            sources.extend(airing_res.sources)
        if calendar_res.ok and calendar_res.data:
            today_rows = [
                x.model_dump(mode="json", exclude_none=True)
                for d in calendar_res.data.days
                for x in d.items
                if d.is_today
            ]
            sections.append(ProductSection(title="今日更新", items=today_rows[: args.limit], notes=calendar_res.data.notes[:2]))
            sources.extend(calendar_res.sources)
        if copilot_res.ok and copilot_res.data:
            data = copilot_res.data
            sections.extend([
                ProductSection(title="继续追", items=[x.model_dump(mode="json", exclude_none=True) for x in data.continue_watching[:5]], notes=["减少追番断点，优先处理在看。"]),
                ProductSection(title="想看开坑", items=[x.model_dump(mode="json", exclude_none=True) for x in data.start_from_wishlist[:5]], notes=["从想看列表挑低启动成本候选。"]),
                ProductSection(title="搁置盘活", items=[x.model_dump(mode="json", exclude_none=True) for x in data.revive_on_hold[:5]], notes=["只建议低压力试一集，不断言搁置原因。"]),
            ])
            sources.extend(copilot_res.sources)
        if args.include_radar and airing_items:
            radar_jobs = [
                self.radar.run(EpisodeRadarArgs(subject_id=x.id, progress_episode=x.my_ep or None, top=3, with_summary=False))
                for x in airing_items[:4]
            ]
            radar_results = await gather_limited(radar_jobs, host="bangumi")
            radar_rows: list[dict[str, Any]] = []
            for item, res in zip(airing_items[:4], radar_results, strict=False):
                if isinstance(res, Exception) or not res.ok or not res.data:
                    continue
                peaks = [p.model_dump(mode="json", exclude_none=True) for p in res.data.peaks[:3] if p.comments > 0]
                if peaks:
                    radar_rows.append({"subject_id": item.id, "name": item.name, "my_ep": item.my_ep, "peaks": peaks})
                    sources.extend(res.sources)
            if radar_rows:
                sections.append(ProductSection(title="分集热度雷达", items=radar_rows, notes=["讨论数是话题度，不等于质量；已按进度过滤后续集。"]))
        result = WatchCockpitResult(
            username=username,
            today=date.today().isoformat(),
            sections=sections,
            subscription=mem.weekly_digest_subscription.model_dump(mode="json", exclude={"webhook_url", "email", "web_push_endpoint", "web_push_p256dh", "web_push_auth"}),
            memory=memory_summary(mem),
            next_actions=[
                "确认今天继续追的条目后，可写回 Bangumi ep_status。",
                "对确定追的新番，配置 release RSS 后每日提醒会检查资源更新。",
                "如果队列不准，用“这个别再推/多来这种”记录反馈再重排。",
            ],
            caveats=["驾驶舱只读 Bangumi/本地记忆，不会自动写回收藏。"],
        )
        return ToolResult(ok=True, data=result, sources=sources[:10])


class SubjectDossierTool(Tool):
    name = "subject_dossier"
    description = "作品档案页：聚合条目详情、无剧透评价、观看/购买入口、资源入口、分集热度与系列路线。"
    args_model = SubjectDossierArgs
    result_model = SubjectDossierResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client
        self.reviewer = ReviewSubjectTool(client)
        self.watch = WhereToWatchTool(client)
        self.release = GetAnimeReleaseFeedsTool(client)
        self.order = WatchOrderTool(client)
        self.relations = GetSubjectRelationsTool(client)
        self.radar = EpisodeBuzzRadarTool(client)
        self.music = AnimeMusicThemesTool(client)

    async def run(self, args: SubjectDossierArgs) -> ToolResult[SubjectDossierResult]:
        raw = await _resolve_subject(self.client, args)
        if not raw:
            return ToolResult(ok=False, error="需要 subject_id 或可解析的 title")
        subject = _subject_payload(raw)
        sid = int(subject["id"])
        stype_name = subject["type_name"] or args.subject_type or "anime"
        jobs = [
            self.reviewer.run(ReviewSubjectArgs(subject_id=sid, title_hint=subject["name"], include_comments=True, spoiler_level=args.spoiler_level)),
            self.watch.run(WhereToWatchArgs(subject_id=sid, title=subject["name"])),
            self.relations.run(SubjectRelationsArgs(subject_id=sid, limit=30)),
        ]
        if stype_name == "anime":
            jobs.extend([
                self.release.run(AnimeReleaseFeedsArgs(subject_id=sid, title=subject["name"], prefer="auto", limit=8)),
                self.radar.run(EpisodeRadarArgs(subject_id=sid, top=5, with_summary=False)),
                self.order.run(WatchOrderArgs(title=subject["name"], subject_type="anime")),
                self.music.run(AnimeMusicThemesArgs(subject_id=sid, title=subject["name"], limit=12)),
            ])
        results = await gather_limited(jobs, host="bangumi")
        sections: list[ProductSection] = []
        sources = [Citation(title=subject["name"], url=f"https://bgm.tv/subject/{sid}", source="bangumi", image=subject.get("image"))]
        for res in results:
            if isinstance(res, Exception) or not res.ok or not res.data:
                continue
            sources.extend(res.sources)
            name = res.data.__class__.__name__
            payload = res.data.model_dump(mode="json", exclude_none=True)
            if name == "ReviewSubjectResult":
                sections.append(ProductSection(title="评价矩阵", items=[payload], notes=["默认无剧透；短评原文会按 spoiler_level 控制。"]))
            elif name == "WhereToWatchResult":
                sections.append(ProductSection(title="观看/购买入口", items=[payload], notes=payload.get("caveats", [])[:2]))
            elif name == "AnimeReleaseFeedsResult":
                sections.append(ProductSection(title="Release/RSS", items=[payload], notes=payload.get("caveats", [])[:2]))
            elif name == "EpisodeRadarResult":
                sections.append(ProductSection(title="分集热度雷达", items=payload.get("peaks", []), notes=payload.get("notes", [])[:2]))
            elif name == "WatchOrderResult":
                sections.append(ProductSection(title="补番路线", items=[payload], notes=payload.get("notes", [])[:2]))
            elif name == "RelatedSubjectsResult":
                sections.append(ProductSection(title="跨媒体关系", items=payload.get("relations", []), notes=["用于原作/改编/续作/音乐等追溯。"]))
            elif name == "AnimeMusicThemeResult":
                sections.append(ProductSection(title="OP/ED/音乐", items=payload.get("fused", []), notes=payload.get("notes", [])[:2]))
        return ToolResult(
            ok=True,
            data=SubjectDossierResult(
                subject=subject,
                sections=sections,
                quick_actions=[
                    "无剧透评价",
                    "在哪看/在哪买",
                    "加入计划板",
                    "查系列观看顺序",
                    "查 RSS/BD 入口" if stype_name == "anime" else "查相关作品",
                ],
                caveats=["档案页是多源聚合；外部入口的可用性和版权地区以源站为准。"],
            ),
            sources=sources[:12],
        )


class FranchiseMapTool(Tool):
    name = "franchise_map"
    description = "IP 图谱：从一个 Bangumi 条目出发，按关系边收集前传/续作/原作/改编/音乐/旁支，并按媒介分组。"
    args_model = FranchiseMapArgs
    result_model = FranchiseMapResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: FranchiseMapArgs) -> ToolResult[FranchiseMapResult]:
        raw = await _resolve_subject(self.client, args)
        if not raw:
            return ToolResult(ok=False, error="需要 subject_id 或可解析的 title")
        seed_payload = _subject_payload(raw)
        seed = FranchiseNode(
            id=int(seed_payload["id"]),
            name=seed_payload["name"],
            type=seed_payload.get("type"),
            type_name=seed_payload.get("type_name") or "",
            date=seed_payload.get("date") or "",
            score=seed_payload.get("score"),
            rank=seed_payload.get("rank"),
            image=seed_payload.get("image"),
        )
        nodes: dict[int, FranchiseNode] = {seed.id: seed}
        edges: list[FranchiseEdge] = []
        queue = [(seed.id, 0)]
        visited = {seed.id}
        while queue and len(nodes) < args.limit:
            sid, depth = queue.pop(0)
            if depth >= args.depth:
                continue
            try:
                rels = await self.client.get_subject_relations(sid)
            except Exception:  # noqa: BLE001
                continue
            for rel in rels or []:
                rid = rel.get("id")
                if not rid:
                    continue
                relation = str(rel.get("relation") or "")
                type_name = _subject_type_name(rel.get("type"))
                edges.append(FranchiseEdge(source=sid, target=int(rid), relation=relation, type_name=type_name))
                if int(rid) not in nodes:
                    rating = rel.get("rating") or {}
                    nodes[int(rid)] = FranchiseNode(
                        id=int(rid),
                        name=str(rel.get("name_cn") or rel.get("name") or rid),
                        type=rel.get("type"),
                        type_name=type_name,
                        date=rel.get("date") or "",
                        score=rating.get("score"),
                        rank=rating.get("rank"),
                        image=_image(rel),
                    )
                if int(rid) not in visited and len(nodes) < args.limit:
                    visited.add(int(rid))
                    queue.append((int(rid), depth + 1))
        grouped: dict[str, list[int]] = defaultdict(list)
        for node in nodes.values():
            grouped[node.type_name or "unknown"].append(node.id)
        main_relations = {"前传", "续集", "不同演绎"}
        suggested = [
            n.id for n in sorted(nodes.values(), key=lambda n: (n.date or "9999", n.id))
            if n.id == seed.id or any(e.target == n.id and e.relation in main_relations for e in edges)
        ]
        return ToolResult(
            ok=True,
            data=FranchiseMapResult(
                seed=seed,
                nodes=list(nodes.values()),
                edges=edges[: args.limit * 2],
                groups={k: v for k, v in grouped.items()},
                suggested_order=suggested[:30],
                notes=[
                    "图谱来自 Bangumi relation 边；关系名由社区维护，可能存在遗漏或口径差异。",
                    "suggested_order 只按日期和主线关系粗排，严肃补番顺序请调用 plan_watch_order。",
                ],
            ),
            sources=[Citation(title=n.name, url=f"https://bgm.tv/subject/{n.id}", source="bangumi", image=n.image) for n in list(nodes.values())[:8]],
        )


class MonthlyWatchReportTool(Tool):
    name = "monthly_watch_report"
    description = "月度/年度收藏报告：按用户 Bangumi 收藏生成评分、标签、完成/搁置分布与 staff 高频。period=year 即年度总结（Wrapped），适合分享。"
    args_model = MonthlyWatchReportArgs
    result_model = MonthlyWatchReportResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: MonthlyWatchReportArgs) -> ToolResult[MonthlyWatchReportResult]:
        username = await _resolve_username(self.client, args.username)
        today = date.today()
        year = args.year or today.year
        month = args.month or today.month
        stype = SUBJECT_TYPE[args.subject_type]
        rows = await self.client.get_all_user_collections(username, stype, None, max_items=1000)
        by_status = Counter(str(row.get("type") or "unknown") for row in rows)
        rated = [row for row in rows if row.get("rate")]
        rating_hist = Counter(str(row.get("rate")) for row in rated)
        tag_counter: Counter[str] = Counter()
        month_tag_counter: Counter[str] = Counter()
        staff_counter: Counter[str] = Counter()
        completed_this_month: list[dict[str, Any]] = []
        updated_this_month: list[dict[str, Any]] = []
        on_hold_or_dropped: list[dict[str, Any]] = []
        for row in rows:
            subj = row.get("subject") or {}
            row_tags = [
                str(t["name"])
                for t in subj.get("tags") or []
                if isinstance(t, dict) and t.get("name")
            ]
            for t in subj.get("tags") or []:
                if isinstance(t, dict) and t.get("name"):
                    tag_counter[str(t["name"])] += 1
            updated = str(row.get("updated_at") or row.get("updatedAt") or "")[:7]
            base_payload = {
                "id": subj.get("id"),
                "name": _title(subj),
                "rate": row.get("rate"),
                "score": (subj.get("rating") or {}).get("score"),
                "status": _STATUS_NAME.get(str(row.get("type") or "unknown"), str(row.get("type") or "")),
                "ep_status": row.get("ep_status"),
                "comment": (row.get("comment") or "")[:180],
                "image": _image(subj),
                "updated_at": row.get("updated_at") or row.get("updatedAt") or "",
            }
            in_window = updated.startswith(str(year)) if args.period == "year" else updated == f"{year}-{month:02d}"
            if in_window:
                updated_this_month.append(base_payload)
                month_tag_counter.update(row_tags)
            if row.get("type") == 2 and in_window:
                completed_this_month.append(base_payload)
            if row.get("type") in {4, 5}:
                on_hold_or_dropped.append(base_payload)
        top_completed = sorted(completed_this_month, key=lambda x: -(x.get("rate") or 0))[: args.limit]
        recent_updates = sorted(updated_this_month, key=lambda x: str(x.get("updated_at") or ""), reverse=True)[: args.limit]
        month_tag_lift = []
        total_rows = max(len(rows), 1)
        month_rows = max(len(updated_this_month), 1)
        for tag, count in month_tag_counter.most_common(20):
            base_rate = tag_counter[tag] / total_rows
            month_rate = count / month_rows
            month_tag_lift.append({
                "tag": tag,
                "month_count": count,
                "total_count": tag_counter[tag],
                "lift": round(month_rate / base_rate, 2) if base_rate else None,
            })
        staff_jobs = [
            self.client.get_subject_persons(int(x["id"]))
            for x in (top_completed or recent_updates)[:24]
            if x.get("id") and args.subject_type in {"anime", "game", "music"}
        ]
        if staff_jobs:
            for persons in await gather_limited(staff_jobs, host="bangumi"):
                if isinstance(persons, Exception):
                    continue
                for person in persons or []:
                    rel = str(person.get("relation") or "")
                    name = str(person.get("name") or "")
                    if not name:
                        continue
                    if any(k in rel for k in ("动画制作", "制作", "导演", "监督", "脚本", "系列构成", "原作", "音乐", "声优", "配音")):
                        staff_counter[f"{rel}:{name}" if rel else name] += 1
        label = "本年度" if args.period == "year" else "本月"
        sections = [
            ProductSection(title=f"{label}完成", items=top_completed, notes=[f"按收藏更新时间近似“{label}完成”；Bangumi 没有独立观看完成日期。"]),
            ProductSection(title=f"{label}更新", items=recent_updates, notes=[f"包含评分、状态、进度或短评在{label}有更新的条目。"]),
            ProductSection(title="状态分布", items=[{"status": _STATUS_NAME.get(k, k), "count": v} for k, v in by_status.items()], notes=["来自 Bangumi collection type。"]),
            ProductSection(title="评分分布", items=[{"rating": k, "count": rating_hist[k]} for k in sorted(rating_hist, key=lambda x: int(x), reverse=True)], notes=["只统计你有打分的收藏。"]),
            ProductSection(title="高频标签", items=[{"tag": k, "count": v} for k, v in tag_counter.most_common(12)], notes=["来自条目标签，不等同于用户主动打标。"]),
            ProductSection(title=f"{label}标签漂移", items=month_tag_lift[:12], notes=["lift>1 表示本月更新样本里该标签相对全量更集中。"]),
            ProductSection(title="搁置/抛弃观察", items=sorted(on_hold_or_dropped, key=lambda x: str(x.get("updated_at") or ""), reverse=True)[: args.limit], notes=["只展示状态与短评样本；不能断言搁置/弃坑原因。"]),
            ProductSection(title="Staff/CV/Studio", items=[{"name": k, "count": v} for k, v in staff_counter.most_common(16)], notes=["对本月完成/更新样本拉 staff，控制 API 负载。"]),
        ]
        avg_rate = round(sum(row.get("rate") or 0 for row in rated) / len(rated), 2) if rated else None
        month_rates = [x.get("rate") for x in updated_this_month if x.get("rate")]
        return ToolResult(
            ok=True,
            data=MonthlyWatchReportResult(
                username=username,
                period=args.period,
                year=year,
                month=month,
                subject_type=args.subject_type,
                sections=sections,
                summary={
                    "collection_count": len(rows),
                    "rated_count": len(rated),
                    "avg_user_rate": avg_rate,
                    "month_updated_count": len(updated_this_month),
                    "completed_this_month": len(completed_this_month),
                    "month_avg_rate": round(mean(month_rates), 2) if month_rates else None,
                    "top_tags": tag_counter.most_common(8),
                    "month_top_tags": month_tag_counter.most_common(8),
                    "top_staff": staff_counter.most_common(8),
                },
                caveats=[
                    "Bangumi API 没有独立“真实观看完成日期”，月度口径以 collection updated_at 近似。",
                    "staff/CV/studio 对本月样本抽样拉取，适合看趋势，不作为完整履历表。",
                ],
            ),
            sources=[Citation(title=x.get("name") or "subject", url=f"https://bgm.tv/subject/{x.get('id')}", source="bangumi", image=x.get("image")) for x in top_completed[:6] if x.get("id")],
        )


def build_product_loop_tools(client: BangumiClient, ltm: LongTermMemory) -> list[Tool]:
    return [
        WatchCockpitTool(client, ltm),
        AnimeMusicThemesTool(client),
        SubjectDossierTool(client),
        FranchiseMapTool(client),
        MonthlyWatchReportTool(client),
    ]
