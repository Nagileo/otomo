"""Phase 19 product-loop aggregate tools.

These tools are intentionally higher-level than atomic API wrappers: they
package common product workflows into stable panel payloads while keeping the
underlying sources traceable.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import date
import re
from statistics import mean, median
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...memory import LongTermMemory
from ...memory.consolidate import now_iso
from ...memory.models import MemorySummary, memory_summary
from ...recommendation_cache import RecommendationArtifactCache
from ...subscription_read import public_subscription_summary
from .._concurrency import gather_limited
from ..bangumi.client import SUBJECT_TYPE, BangumiClient
from ..bangumi.tools import GetSubjectRelationsTool, SubjectRelationsArgs
from ..calendar.tool import AiringProgressArgs, AiringProgressTool, BroadcastCalendarArgs, BroadcastCalendarTool
from ..discovery.tool import EpisodeBuzzRadarTool, EpisodeRadarArgs
from ..animethemes.tool import AnimeThemesArgs, SearchAnimeThemesTool
from ..release.tool import AnimeReleaseFeedsArgs, GetAnimeReleaseFeedsTool
from ..media_identity import MediaIdentity, assess_media_scope, media_identity_from_subject, normalize_media_title
from ..review.tool import ReviewSubjectArgs, ReviewSubjectTool
from ..series_progress import SeriesProgressArgs, SeriesProgressResult, SeriesProgressService
from ..videos.tool import (
    BiliSubjectVideosArgs,
    BiliSubjectVideosResult,
    SearchBiliSubjectVideosTool,
)
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
    include_watch: bool = Field(True, description="是否聚合观看入口；产品页可改由 anime_watch_hub 分段加载")
    include_release: bool = Field(True, description="anime 条目是否补 release/RSS 入口")


class SubjectDossierResult(BaseModel):
    subject: dict[str, Any]
    sections: list[ProductSection] = Field(default_factory=list)
    quick_actions: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class AnimeLifecycle(BaseModel):
    state: Literal["upcoming", "airing", "recent", "archive", "unknown"] = "unknown"
    phase: Literal[
        "upcoming_tv", "airing_tv", "completed_tv", "archive_tv",
        "upcoming_movie", "theatrical", "awaiting_streaming", "awaiting_bd", "archive_movie",
        "upcoming_ova", "releasing_ova", "completed_ova", "unknown",
    ] = "unknown"
    media_kind: Literal["tv", "web", "movie", "ova", "unknown"] = "unknown"
    label: str = "状态待确认"
    air_date: str = ""
    end_date: str = ""
    strategy: str = "按作品身份查询观看与内容入口"
    confidence: float = 0.5
    resource_mode: Literal["episodic", "one_shot", "archive", "unknown"] = "unknown"


class AnimeResolutionCandidate(BaseModel):
    subject_id: int
    title: str
    title_jp: str = ""
    date: str = ""
    platform: str = ""


class AnimeResolution(BaseModel):
    status: Literal["resolved", "ambiguous", "not_found"] = "resolved"
    matched_by: Literal["subject_id", "exact_title", "normalized_title", "none"] = "subject_id"
    query: str = ""
    reason: str = ""
    candidates: list[AnimeResolutionCandidate] = Field(default_factory=list)


class HubModuleState(BaseModel):
    status: Literal["idle", "loading", "ready", "empty", "degraded", "failed"] = "idle"
    duration_ms: int = 0
    error: str = ""
    cache_hit: bool | None = None
    retryable: bool = True
    updated_at: str = ""


class AnimeWatchHubArgs(BaseModel):
    subject_id: int | None = Field(None, description="Bangumi 动画 subject_id；优先使用")
    title: str = Field("", description="subject_id 为空时按标题搜索")
    include_release: bool = True
    include_videos: bool = True
    video_limit: int = Field(5, ge=1, le=10)
    stage: Literal["all", "identity", "overview", "core", "videos", "releases", "music", "follow"] = "all"
    username: str | None = Field(None, description="可选 Bangumi 用户名；用于合并逐季系列进度")
    include_viewer_state: bool = True
    spoiler_level: Literal["none", "mild", "full"] = "none"


class AnimeWatchHubResult(BaseModel):
    subject: dict[str, Any] = Field(default_factory=dict)
    identity: MediaIdentity | None = None
    resolution: AnimeResolution = Field(default_factory=AnimeResolution)
    lifecycle: AnimeLifecycle = Field(default_factory=AnimeLifecycle)
    viewer_state: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)
    overview: dict[str, Any] = Field(default_factory=dict)
    reputation: dict[str, Any] = Field(default_factory=dict)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    episode_radar: dict[str, Any] = Field(default_factory=dict)
    trend: dict[str, Any] = Field(default_factory=dict)
    music: dict[str, Any] = Field(default_factory=dict)
    online: dict[str, Any] = Field(default_factory=dict)
    releases: dict[str, Any] = Field(default_factory=dict)
    bilibili: BiliSubjectVideosResult | None = None
    series_progress: SeriesProgressResult | None = None
    staff_signals: list[str] = Field(default_factory=list)
    status_summary: list[str] = Field(default_factory=list)
    quick_actions: list[str] = Field(default_factory=list)
    modules: dict[str, HubModuleState] = Field(default_factory=dict)
    generated_at: str = ""
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


async def _resolve_subject(
    client: BangumiClient,
    args: SubjectDossierArgs | FranchiseMapArgs | AnimeWatchHubArgs,
) -> dict[str, Any] | None:
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


def _resolution_candidates(rows: list[dict[str, Any]]) -> list[AnimeResolutionCandidate]:
    return [
        AnimeResolutionCandidate(
            subject_id=int(row["id"]),
            title=_title(row),
            title_jp=str(row.get("name") or ""),
            date=str(row.get("date") or ""),
            platform=str(row.get("platform") or ""),
        )
        for row in rows[:6]
        if row.get("id")
    ]


async def _resolve_anime_subject(
    client: BangumiClient, args: AnimeWatchHubArgs,
) -> tuple[dict[str, Any] | None, AnimeResolution]:
    """Resolve titles conservatively; never silently take a fuzzy first hit."""
    if args.subject_id:
        raw = await client.get_subject(args.subject_id)
        return raw, AnimeResolution(
            status="resolved", matched_by="subject_id", query=str(args.subject_id),
            reason="已使用明确的 Bangumi subject_id。",
        )
    query = args.title.strip()
    if not query:
        return None, AnimeResolution(status="not_found", matched_by="none", reason="需要 subject_id 或动画标题。")
    payload = await client.search_subjects(query, SUBJECT_TYPE["anime"], limit=8)
    rows = [row for row in (payload.get("data") or []) if isinstance(row, dict) and row.get("id")]
    if not rows:
        return None, AnimeResolution(
            status="not_found", matched_by="none", query=query, reason="Bangumi 没有返回动画候选。",
        )
    exact = [row for row in rows if query in {str(row.get("name_cn") or "").strip(), str(row.get("name") or "").strip()}]
    if len(exact) == 1:
        return exact[0], AnimeResolution(
            status="resolved", matched_by="exact_title", query=query, reason="标题与唯一 Bangumi 条目完全一致。",
        )
    normalized_query = normalize_media_title(query)
    normalized = [
        row for row in rows
        if normalized_query and normalized_query in {
            normalize_media_title(str(row.get("name_cn") or "")),
            normalize_media_title(str(row.get("name") or "")),
        }
    ]
    if len(normalized) == 1:
        return normalized[0], AnimeResolution(
            status="resolved", matched_by="normalized_title", query=query,
            reason="忽略空格与标点后只对应一个 Bangumi 条目。",
        )
    candidates = exact or normalized or rows
    return None, AnimeResolution(
        status="ambiguous", matched_by="none", query=query,
        reason="标题可能对应多个版本、季度或重制条目；请选择具体 Bangumi 条目后再继续。",
        candidates=_resolution_candidates(candidates),
    )


def _subject_payload(raw: dict[str, Any]) -> dict[str, Any]:
    rating = raw.get("rating") or {}
    return {
        "id": raw.get("id"),
        "name": _title(raw),
        "name_jp": raw.get("name") or "",
        "type": raw.get("type"),
        "type_name": _subject_type_name(raw.get("type")),
        "date": raw.get("date") or "",
        "eps": raw.get("eps") or raw.get("total_episodes"),
        "platform": raw.get("platform") or "",
        "score": rating.get("score"),
        "rank": rating.get("rank"),
        "summary": (raw.get("summary") or "")[:600],
        "image": _image(raw),
        "tags": [t.get("name") for t in (raw.get("tags") or []) if isinstance(t, dict) and t.get("name")][:15],
    }


def _infobox_text(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    for row in raw.get("infobox") or []:
        if not isinstance(row, dict) or str(row.get("key") or "").strip() not in keys:
            continue
        value = row.get("value")
        if isinstance(value, list):
            values = [str(item.get("v") if isinstance(item, dict) else item).strip() for item in value]
            return " / ".join(item for item in values if item)
        return str(value or "").strip()
    return ""


def _date_prefix(value: str) -> date | None:
    match = re.search(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})", value or "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _duration_minutes(value: str) -> float | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    clock = re.search(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})", text)
    if clock:
        hours = int(clock.group(1) or 0)
        return hours * 60 + int(clock.group(2)) + int(clock.group(3)) / 60
    hours_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:小时|小時|hours?|hrs?|h)", text)
    minutes_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:分钟|分鐘|分|min(?:ute)?s?|m)", text)
    if hours_match or minutes_match:
        return (float(hours_match.group(1)) * 60 if hours_match else 0) + (float(minutes_match.group(1)) if minutes_match else 0)
    return None


def _anime_lifecycle(raw: dict[str, Any]) -> AnimeLifecycle:
    today = date.today()
    identity = media_identity_from_subject(raw)
    media_kind = identity.media_kind
    air_text = str(raw.get("date") or "")
    end_text = _infobox_text(raw, ("播放结束", "放送结束", "上映结束", "发售日", "発売日"))
    start = _date_prefix(air_text)
    end = _date_prefix(end_text)
    if media_kind == "movie":
        if start and start > today:
            return AnimeLifecycle(
                state="upcoming", phase="upcoming_movie", media_kind=media_kind,
                label="尚未上映", air_date=air_text, end_date=end_text,
                strategy="优先查正式PV、上映日期和正版预约；不按周更动画搜索分集或RSS。",
                confidence=0.95, resource_mode="one_shot",
            )
        age = (today - start).days if start else None
        if age is not None and age <= 120:
            return AnimeLifecycle(
                state="airing", phase="theatrical", media_kind=media_kind,
                label="院线/上映阶段", air_date=air_text, end_date=end_text,
                strategy="优先查上映信息、无剧透评价和官方物料；流媒体与BD未核验前不写成可观看。",
                confidence=0.82, resource_mode="one_shot",
            )
        if age is not None and age <= 365:
            return AnimeLifecycle(
                state="recent", phase="awaiting_streaming", media_kind=media_kind,
                label="等待流媒体上线", air_date=air_text, end_date=end_text,
                strategy="优先核验正版流媒体和发行公告；不使用TV番组周更RSS。",
                confidence=0.72, resource_mode="one_shot",
            )
        if age is not None and age <= 730:
            return AnimeLifecycle(
                state="recent", phase="awaiting_bd", media_kind=media_kind,
                label="流媒体/BD发行阶段", air_date=air_text, end_date=end_text,
                strategy="优先查正版存量、BD发行和完整影评；离线入口只显示明确电影版本。",
                confidence=0.7, resource_mode="one_shot",
            )
        return AnimeLifecycle(
            state="archive" if start else "unknown", phase="archive_movie" if start else "unknown",
            media_kind=media_kind, label="已发行剧场版" if start else "上映状态待确认",
            air_date=air_text, end_date=end_text,
            strategy="优先查正版存量、BD/合集和系列位置，不按分集周更处理。",
            confidence=0.78 if start else 0.4, resource_mode="archive" if start else "unknown",
        )
    if media_kind == "ova":
        if start and start > today:
            return AnimeLifecycle(
                state="upcoming", phase="upcoming_ova", media_kind=media_kind,
                label="OVA尚未发售", air_date=air_text, end_date=end_text,
                strategy="优先查发售日、系列位置和官方PV；不把预售期资源写成可观看。",
                confidence=0.93, resource_mode="one_shot",
            )
        recent_ova = bool(start and (today - start).days <= 365 and not end)
        return AnimeLifecycle(
            state="airing" if recent_ova else "recent" if start and (today - start).days <= 730 else "archive" if start else "unknown",
            phase="releasing_ova" if recent_ova else "completed_ova" if start else "unknown",
            media_kind=media_kind, label="OVA发行中" if recent_ova else "OVA已发行" if start else "OVA状态待确认",
            air_date=air_text, end_date=end_text,
            strategy="按单次/分卷发行处理，优先系列位置、正版/BD状态和明确OVA资源，不套用TV周更逻辑。",
            confidence=0.76 if start else 0.4, resource_mode="one_shot" if recent_ova else "archive" if start else "unknown",
        )
    if start and start > today:
        return AnimeLifecycle(
            state="upcoming", phase="upcoming_tv", media_kind=media_kind,
            label="尚未开播", air_date=air_text, end_date=end_text,
            strategy="优先查正版预约、官方/Staff PV 与播前内容；不把未发布资源写成可观看",
            confidence=0.94, resource_mode="episodic",
        )
    if end and end < today:
        days = (today - end).days
        state = "recent" if days <= 365 else "archive"
        return AnimeLifecycle(
            state=state, phase="completed_tv" if state == "recent" else "archive_tv", media_kind=media_kind,
            label="近期完结" if state == "recent" else "已完结老番",
            air_date=air_text,
            end_date=end_text,
            strategy=(
                "优先查完结评价、全集/番组 RSS 与系列下一部"
                if state == "recent"
                else "优先查正版存量、全集/BD/VCB、补番回顾与系列顺序"
            ),
            confidence=0.92, resource_mode="episodic" if state == "recent" else "archive",
        )
    if start:
        age = (today - start).days
        if age <= 210:
            return AnimeLifecycle(
                state="airing", phase="airing_tv", media_kind=media_kind,
                label="正在播出或近期上线", air_date=air_text, end_date=end_text,
                strategy="优先查正版更新、最新集 RSS、B站普通投稿正片候选与首集/阶段漫评",
                confidence=0.72 if not end else 0.88, resource_mode="episodic",
            )
        if age <= 730:
            return AnimeLifecycle(
                state="recent", phase="completed_tv", media_kind=media_kind,
                label="近期作品", air_date=air_text, end_date=end_text,
                strategy="兼顾正版存量、番组 RSS、完结评价和系列路线",
                confidence=0.68, resource_mode="episodic",
            )
        return AnimeLifecycle(
            state="archive", phase="archive_tv", media_kind=media_kind,
            label="已完结老番", air_date=air_text, end_date=end_text,
            strategy="优先查正版存量、全集/BD/VCB、补番回顾与系列顺序",
            confidence=0.78, resource_mode="archive",
        )
    return AnimeLifecycle(
        state="unknown",
        phase="unknown", media_kind=media_kind,
        label="播出状态待确认",
        air_date=air_text,
        end_date=end_text,
        strategy="先按作品条目查询；对资源与视频结果保留较强版本警告",
        confidence=0.4, resource_mode="unknown",
    )


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
            subscription=public_subscription_summary(username),
            memory=memory_summary(mem),
            next_actions=[
                "确认今天继续追的条目后，可写回 Bangumi ep_status。",
                "对确定追的新番，配置 release RSS 后每日提醒会检查资源更新。",
                "如果队列不准，用“这个别再推/多来这种”记录反馈再重排。",
            ],
            caveats=["驾驶舱只读 Bangumi/本地记忆，不会自动写回收藏。"],
        )
        return ToolResult(ok=True, data=result, sources=sources[:10])


class AnimeWatchHubTool(Tool):
    name = "anime_watch_hub"
    description = (
        "动画作品的一站式观看枢纽：按 Bangumi 条目聚合正版观看、B站普通投稿中的可看正片候选、"
        "PV/漫评/回顾、Mikan/字幕组 RSS、BT/BD 入口。适用于新番和老番；只读聚合，下载器仍需单独确认。"
    )
    args_model = AnimeWatchHubArgs
    result_model = AnimeWatchHubResult

    def __init__(
        self,
        client: BangumiClient,
        ltm: LongTermMemory | None = None,
        friend_usernames: list[str] | None = None,
        artifact_cache: RecommendationArtifactCache | None = None,
    ) -> None:
        self.client = client
        self.ltm = ltm
        self.friend_usernames = list(dict.fromkeys(friend_usernames or []))[:12]
        self.artifact_cache = artifact_cache
        self.watch = WhereToWatchTool(client)
        self.release = GetAnimeReleaseFeedsTool(client)
        self.videos = SearchBiliSubjectVideosTool()
        self.series = SeriesProgressService(client)
        self.reviewer = ReviewSubjectTool(client)
        self.relations_tool = GetSubjectRelationsTool(client)
        self.radar = EpisodeBuzzRadarTool(client)
        self.music_tool = AnimeMusicThemesTool(client)

    async def _staff_signals(self, subject_id: int) -> list[str]:
        try:
            rows = await self.client.get_subject_persons(subject_id)
        except Exception:  # noqa: BLE001 - staff 缺失不应拖垮观看入口
            return []
        relevant: list[str] = []
        relation_words = ("制作", "导演", "監督", "原作", "企画", "制片", "系列构成", "脚本")
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            relation = str(row.get("relation") or row.get("staff") or "")
            if relation and not any(word in relation for word in relation_words):
                continue
            name = str(row.get("name_cn") or row.get("name") or "").strip()
            if name and name not in relevant:
                relevant.append(name)
        return relevant[:24]

    async def _episode_runtime_minutes(self, subject_id: int) -> float | None:
        """Read actual Bangumi episode durations when the subject infobox omits runtime."""
        try:
            payload = await self.client.get_episodes(subject_id, episode_type=0, limit=12, offset=0)
        except Exception:  # noqa: BLE001 - runtime enrichment must not block the hub
            return None
        minutes: list[float] = []
        for row in (payload or {}).get("data") or []:
            if not isinstance(row, dict):
                continue
            seconds = float(row.get("duration_seconds") or 0)
            value = seconds / 60 if seconds > 0 else _duration_minutes(str(row.get("duration") or ""))
            if value is not None and 0.5 <= value <= 300:
                minutes.append(value)
        return round(float(median(minutes)), 2) if minutes else None

    def _preferences(self, subject_id: int, username: str | None) -> tuple[dict[str, Any], Any | None]:
        if not self.ltm or not username:
            return {}, None
        mem = self.ltm.load_user(username)
        prefs = mem.anime_hub_preferences.get(str(subject_id))
        return (prefs.model_dump(mode="json", exclude_none=True) if prefs else {}), mem

    async def _viewer_state(self, subject_id: int, username: str | None) -> dict[str, Any]:
        if not username:
            return {"authenticated": False, "collection_state": "unknown", "collection_label": "登录后显示进度"}
        labels = {1: "想看", 2: "看过", 3: "在看", 4: "搁置", 5: "抛弃"}
        try:
            row = await asyncio.wait_for(self.client.get_user_collection(username, subject_id), timeout=3)
        except Exception as exc:  # 404 means uncollected; other failures remain explicit
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 404:
                return {
                    "authenticated": True, "username": username, "collection_type": 0,
                    "collection_state": "uncollected", "collection_label": "未收藏", "ep_status": 0,
                }
            return {
                "authenticated": True, "username": username, "collection_state": "unknown",
                "collection_label": "进度读取失败", "error": type(exc).__name__,
            }
        ctype = int(row.get("type") or 0)
        return {
            "authenticated": True,
            "username": username,
            "collection_type": ctype,
            "collection_state": {1: "wishlist", 2: "watched", 3: "watching", 4: "on_hold", 5: "dropped"}.get(ctype, "unknown"),
            "collection_label": labels.get(ctype, "状态未知"),
            "ep_status": int(row.get("ep_status") or 0),
            "rate": int(row.get("rate") or 0),
            "comment": str(row.get("comment") or ""),
            "private": bool(row.get("private", False)),
        }

    async def _friend_feedback(self, subject_id: int) -> list[dict[str, Any]]:
        if not self.friend_usernames:
            return []

        async def one(username: str) -> dict[str, Any] | None:
            try:
                row = await asyncio.wait_for(self.client.get_user_collection(username, subject_id), timeout=4)
            except Exception:
                return None
            ctype = int(row.get("type") or 0)
            return {
                "username": username,
                "collection_type": ctype,
                "collection_label": {1: "想看", 2: "看过", 3: "在看", 4: "搁置", 5: "抛弃"}.get(ctype, "更新过"),
                "rate": int(row.get("rate") or 0),
                "ep_status": int(row.get("ep_status") or 0),
            }

        rows = await asyncio.gather(*(one(username) for username in self.friend_usernames[:8]))
        return sorted((row for row in rows if row), key=lambda row: (-int(row.get("rate") or 0), row["username"]))[:6]

    async def _subject_relations(self, subject_id: int) -> list[dict[str, Any]]:
        cache_key = f"anime-hub:relations:{subject_id}:v1"
        if self.artifact_cache:
            cached = self.artifact_cache.get(cache_key)
            if isinstance(cached, dict) and isinstance(cached.get("rows"), list):
                return [row for row in cached["rows"] if isinstance(row, dict)]
        getter = getattr(self.client, "get_subject_relations", None)
        if not callable(getter):
            return []
        rows = await getter(subject_id)
        normalized = [row for row in rows or [] if isinstance(row, dict)]
        if self.artifact_cache:
            self.artifact_cache.set(cache_key, {"rows": normalized}, kind="anime_hub_relations")
        return normalized

    def _overview_payload(
        self,
        subject: dict[str, Any],
        review: dict[str, Any],
        viewer_state: dict[str, Any],
        friend_feedback: list[dict[str, Any]],
        mem: Any | None,
    ) -> dict[str, Any]:
        tags = [str(tag) for tag in subject.get("tags") or []]
        searchable = " ".join([str(subject.get("name") or ""), str(subject.get("summary") or ""), *tags]).lower()
        likes = [item.value for item in (mem.likes if mem else []) if item.value.lower() in searchable]
        dislikes = [item.value for item in (mem.dislikes if mem else []) if item.value.lower() in searchable]
        feedback = [item for item in (mem.feedback if mem else []) if item.subject_id == subject.get("id")]
        if any(item.signal in {"dislike", "less"} for item in feedback):
            dislikes.append("你曾明确减少或拒绝这部作品")
        if any(item.signal in {"like", "more"} for item in feedback):
            likes.append("你曾明确喜欢或希望多来这类作品")
        aspect_rows = {str(row.get("aspect")): row for row in review.get("aspect_summary") or []}
        profile = (mem.aspect_profiles.get("anime") if mem else None)
        if profile:
            for pref in profile.likes:
                row = aspect_rows.get(pref.aspect)
                if row and row.get("dominant_sentiment") == "positive":
                    likes.append(f"你偏好的{pref.label}口碑较好")
                elif row and row.get("dominant_sentiment") == "negative":
                    dislikes.append(f"你重视{pref.label}，但这方面存在负面口碑")
            for pref in profile.dislikes:
                row = aspect_rows.get(pref.aspect)
                if row and row.get("dominant_sentiment") == "negative":
                    dislikes.append(f"触及你的{pref.label}雷区")
        friend_rates = [int(row.get("rate") or 0) for row in friend_feedback if row.get("rate")]
        if friend_rates and sum(friend_rates) / len(friend_rates) >= 8:
            likes.append(f"{len(friend_rates)} 位关注好友的平均评分较高")
        likes = list(dict.fromkeys(likes))[:5]
        dislikes = list(dict.fromkeys(dislikes))[:5]
        if dislikes and not likes:
            verdict = "谨慎考虑"
            fit = "可能触及你的明确雷区，建议先看无剧透评价或试播一集。"
        elif likes:
            verdict = "值得优先了解"
            fit = "与你的显式偏好或好友反馈存在可靠交集。"
        else:
            verdict = "需要你自己判断"
            fit = "目前只有通用口碑，缺少足够个性化证据，不会强行断言适合你。"
        return {
            "verdict": verdict,
            "fit_summary": fit,
            "why_for_me": likes,
            "risk_for_me": dislikes,
            "general_consensus": str(review.get("consensus") or "暂无稳定的无剧透共识。"),
            "review_confidence": str(review.get("confidence") or "low"),
            "friend_feedback": friend_feedback,
            "viewer_state": viewer_state,
            "spoiler_level": str(review.get("spoiler_level") or "none"),
        }


    async def run(self, args: AnimeWatchHubArgs) -> ToolResult[AnimeWatchHubResult]:
        identity_started = time.monotonic()
        identity_cache_hit = False
        raw: dict[str, Any] | None = None
        resolution: AnimeResolution
        identity_cache_key = f"anime-hub:identity:{args.subject_id}:v2" if args.subject_id else ""
        cached_identity = self.artifact_cache.get(identity_cache_key) if self.artifact_cache and identity_cache_key else None
        if isinstance(cached_identity, dict) and isinstance(cached_identity.get("subject"), dict):
            raw = cached_identity["subject"]
            identity_cache_hit = True
            resolution = AnimeResolution(
                status="resolved",
                matched_by="subject_id",
                query=str(args.subject_id),
                reason="命中跨请求作品身份缓存",
            )
        else:
            raw, resolution = await _resolve_anime_subject(self.client, args)
            if raw is not None and self.artifact_cache and identity_cache_key:
                self.artifact_cache.set(identity_cache_key, {"subject": raw}, kind="anime_hub_identity")
        generated_at = now_iso()
        if raw is None:
            status = "empty" if resolution.status == "not_found" else "degraded"
            return ToolResult(
                ok=True,
                data=AnimeWatchHubResult(
                    resolution=resolution,
                    modules={
                        "identity": HubModuleState(
                            status=status,
                            duration_ms=round((time.monotonic() - identity_started) * 1000),
                            error=resolution.reason,
                            updated_at=generated_at,
                        )
                    },
                    generated_at=generated_at,
                    caveats=[resolution.reason],
                ),
            )
        subject = _subject_payload(raw)
        if subject.get("type_name") != "anime":
            if args.stage == "identity":
                return ToolResult(
                    ok=True,
                    data=AnimeWatchHubResult(
                        subject=subject,
                        resolution=resolution,
                        modules={
                            "identity": HubModuleState(
                                status="ready",
                                duration_ms=round((time.monotonic() - identity_started) * 1000),
                                cache_hit=identity_cache_hit,
                                updated_at=generated_at,
                            )
                        },
                        generated_at=generated_at,
                        caveats=["该条目不是动画，前端应继续使用通用作品档案。"],
                    ),
                    sources=[Citation(
                        title=str(subject.get("name") or subject.get("id") or "作品"),
                        url=f"https://bgm.tv/subject/{subject.get('id')}",
                        source="bangumi",
                        image=subject.get("image"),
                    )],
                )
            return ToolResult(ok=False, error="动画观看枢纽只处理 anime 条目")
        sid = int(subject["id"])
        identity = media_identity_from_subject(raw, fallback_title=str(subject.get("name") or ""))
        lifecycle = _anime_lifecycle(raw)
        preferences, mem = self._preferences(sid, args.username)
        viewer_state = await self._viewer_state(sid, args.username) if args.include_viewer_state else {}
        modules: dict[str, HubModuleState] = {
            "identity": HubModuleState(
                status="degraded" if viewer_state.get("error") else "ready",
                duration_ms=round((time.monotonic() - identity_started) * 1000),
                error=str(viewer_state.get("error") or ""),
                cache_hit=identity_cache_hit,
                updated_at=generated_at,
            )
        }
        base_result = {
            "subject": subject,
            "identity": identity,
            "resolution": resolution,
            "lifecycle": lifecycle,
            "viewer_state": viewer_state,
            "preferences": preferences,
            "generated_at": generated_at,
        }
        base_sources = [Citation(
            title=str(subject.get("name") or sid),
            url=f"https://bgm.tv/subject/{sid}",
            source="bangumi",
            image=subject.get("image"),
        )]
        if args.stage == "identity":
            return ToolResult(
                ok=True,
                data=AnimeWatchHubResult(**base_result, modules=modules),
                sources=base_sources,
            )

        aliases = identity.aliases or [str(subject.get("name") or ""), str(subject.get("name_jp") or "")]
        production_text = _infobox_text(raw, ("动画制作", "動畫製作", "アニメーション制作", "制作"))
        production_names = [
            name.strip() for name in re.split(r"\s*[/、,，]\s*", production_text) if name.strip()
        ][:12]
        runtime_text = _infobox_text(raw, ("片长", "片長", "单集片长", "單集片長", "每话时长", "每話時長", "时长", "時長"))
        expected_episode_minutes = _duration_minutes(runtime_text)
        task_modules: dict[str, HubModuleState] = {}

        async def timed(name: str, awaitable: Any, timeout: float) -> Any:
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(awaitable, timeout=timeout)
                ok = bool(getattr(result, "ok", True))
                error = str(getattr(result, "error", "") or "")
                cache_hit = getattr(getattr(result, "data", None), "cache_hit", None)
                task_modules[name] = HubModuleState(
                    status="ready" if ok else "failed",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    error=error,
                    cache_hit=cache_hit if isinstance(cache_hit, bool) else None,
                    updated_at=now_iso(),
                )
                return result
            except Exception as exc:  # noqa: BLE001 - each hub module degrades independently
                task_modules[name] = HubModuleState(
                    status="failed",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    error=f"{type(exc).__name__}: {str(exc)[:180]}",
                    updated_at=now_iso(),
                )
                return exc

        want_overview = args.stage in {"all", "overview"}
        want_core = args.stage in {"all", "core", "follow"}
        want_videos = args.include_videos and args.stage in {"all", "videos", "follow"}
        want_releases = args.include_release and args.stage in {"all", "releases", "follow"}
        want_music = args.stage in {"all", "music"}
        tasks: dict[str, asyncio.Task[Any]] = {}

        if want_core:
            tasks["online"] = asyncio.create_task(timed(
                "online",
                self.watch.run(WhereToWatchArgs(subject_id=sid, title=str(subject.get("name") or ""))),
                20,
            ))
            tasks["series"] = asyncio.create_task(timed(
                "series",
                self.series.build(SeriesProgressArgs(subject_id=sid, username=args.username, max_members=24)),
                25,
            ))
            tasks["staff"] = asyncio.create_task(timed("staff", self._staff_signals(sid), 8))
        if want_overview:
            tasks["review"] = asyncio.create_task(timed(
                "review",
                self.reviewer.run(ReviewSubjectArgs(
                    subject_id=sid,
                    title_hint=str(subject.get("name") or ""),
                    include_comments=True,
                    spoiler_level=args.spoiler_level,
                )),
                30,
            ))
            tasks["radar"] = asyncio.create_task(timed(
                "radar", self.radar.run(EpisodeRadarArgs(subject_id=sid, top=6, with_summary=False)), 15,
            ))
            tasks["friends"] = asyncio.create_task(timed("friends", self._friend_feedback(sid), 12))
            from ..netabare.tool import SubjectTrendArgs, SubjectTrendTool

            tasks["trend"] = asyncio.create_task(timed(
                "trend", SubjectTrendTool(self.client).run(SubjectTrendArgs(subject_id=sid, days=365)), 18,
            ))
        if want_music:
            tasks["music"] = asyncio.create_task(timed(
                "music",
                self.music_tool.run(AnimeMusicThemesArgs(subject_id=sid, title=str(subject.get("name") or ""), limit=12)),
                25,
            ))
        if want_releases:
            release_prefer: Literal["auto", "mikan", "bt", "bd", "archive"] = (
                "bd" if identity.media_kind in {"movie", "ova"}
                else "archive" if lifecycle.state == "archive"
                else "auto"
            )
            preferred_subgroups = preferences.get("preferred_subgroups") or []
            tasks["releases"] = asyncio.create_task(timed(
                "releases",
                self.release.run(AnimeReleaseFeedsArgs(
                    subject_id=sid,
                    title=str(subject.get("name") or ""),
                    prefer=release_prefer,
                    preferred_subgroups=list(preferred_subgroups),
                    quality_filter=str(preferences.get("preferred_quality") or ""),
                    subtitle_filter=str(preferences.get("preferred_subtitle") or ""),
                    disabled_sources=list(preferences.get("disabled_sources") or []),
                    limit=12,
                )),
                35,
            ))
        if want_videos:
            if expected_episode_minutes is None:
                try:
                    expected_episode_minutes = await asyncio.wait_for(self._episode_runtime_minutes(sid), timeout=4)
                except Exception:  # noqa: BLE001 - metadata enrichment is optional
                    expected_episode_minutes = None
            tasks["videos"] = asyncio.create_task(timed(
                "videos",
                self.videos.run(BiliSubjectVideosArgs(
                    query=str(subject.get("name") or ""),
                    aliases=aliases,
                    staff_names=production_names,
                    expected_episode_minutes=expected_episode_minutes,
                    subject_platform=str(subject.get("platform") or ""),
                    lifecycle=lifecycle.state,
                    lifecycle_phase=lifecycle.phase,
                    media_kind=identity.media_kind,
                    preferred_uploaders=list(preferences.get("liked_uploaders") or []),
                    muted_uploaders=list(preferences.get("muted_uploaders") or []),
                    hidden_video_ids=list(preferences.get("hidden_video_ids") or []),
                    limit=args.video_limit,
                )),
                32,
            ))
        if want_overview or want_videos:
            tasks["relations"] = asyncio.create_task(timed("relations", self._subject_relations(sid), 10))

        values = await asyncio.gather(*tasks.values()) if tasks else []
        resolved = dict(zip(tasks.keys(), values, strict=True))
        modules.update(task_modules)

        def tool_payload(name: str) -> tuple[dict[str, Any], list[Citation]]:
            result = resolved.get(name)
            if isinstance(result, BaseException) or not getattr(result, "ok", False) or not result.data:
                return {}, []
            return result.data.model_dump(mode="json", exclude_none=True), list(result.sources)

        sources = list(base_sources)
        online, online_sources = tool_payload("online")
        releases, release_sources = tool_payload("releases")
        review, review_sources = tool_payload("review")
        radar, radar_sources = tool_payload("radar")
        trend, trend_sources = tool_payload("trend")
        music, music_sources = tool_payload("music")
        sources.extend([*online_sources, *release_sources, *review_sources, *radar_sources, *trend_sources, *music_sources])
        series_res = resolved.get("series")
        series_progress = series_res if isinstance(series_res, SeriesProgressResult) else None
        video_res = resolved.get("videos")
        bilibili = (
            video_res.data
            if not isinstance(video_res, BaseException) and getattr(video_res, "ok", False) and video_res.data
            else None
        )
        if bilibili and not isinstance(video_res, BaseException):
            sources.extend(video_res.sources)
        relation_res = resolved.get("relations")
        relations = [row for row in relation_res if isinstance(row, dict)] if isinstance(relation_res, list) else []
        friend_feedback = resolved.get("friends") if isinstance(resolved.get("friends"), list) else []
        overview = self._overview_payload(subject, review, viewer_state, friend_feedback, mem) if want_overview else {}
        staff_signals = resolved.get("staff") if isinstance(resolved.get("staff"), list) else []

        if bilibili and bilibili.version_conflicts and relations:
            related_anime = [row for row in relations if row.get("id") and row.get("type") == SUBJECT_TYPE["anime"]]
            for conflict in bilibili.version_conflicts:
                matches: list[tuple[int, dict[str, Any]]] = []
                for row in related_anime:
                    scope = assess_media_scope(media_identity_from_subject(row), conflict.title)
                    score = 2 if scope.status == "exact" else 1 if scope.status == "compatible" else 0
                    if score:
                        matches.append((score, row))
                matches.sort(key=lambda pair: (-pair[0], int(pair[1].get("id") or 0)))
                related = matches[0][1] if matches and (len(matches) == 1 or matches[0][0] > matches[1][0]) else None
                if related is None and "季" in conflict.reason:
                    sequel_rows = [
                        row for row in related_anime
                        if any(token in str(row.get("relation") or "").lower() for token in ("续集", "续作", "続編", "sequel"))
                    ]
                    related = sequel_rows[0] if len(sequel_rows) == 1 else None
                if related is not None:
                    conflict.suggested_subject_id = int(related["id"])
                    conflict.suggested_subject_title = str(related.get("name_cn") or related.get("name") or related["id"])
                    conflict.suggested_relation = str(related.get("relation") or "关联篇章")
                    if series_progress:
                        progress_item = next((
                            item for item in series_progress.mainline + series_progress.optional + series_progress.alternates
                            if item.id == conflict.suggested_subject_id
                        ), None)
                        if progress_item:
                            conflict.suggested_collection_state = progress_item.collection_state
                            conflict.suggested_collection_label = progress_item.collection_label
                            conflict.suggested_completed = progress_item.completed

        def aggregate(name: str, keys: list[str]) -> None:
            states = [modules[key] for key in keys if key in modules]
            if not states:
                return
            failed = [state for state in states if state.status == "failed"]
            cache_marks = [state.cache_hit for state in states if state.cache_hit is not None]
            modules[name] = HubModuleState(
                status="failed" if len(failed) == len(states) else "degraded" if failed else "ready",
                duration_ms=max(state.duration_ms for state in states),
                error="；".join(state.error for state in failed if state.error)[:500],
                cache_hit=all(cache_marks) if cache_marks else None,
                updated_at=max((state.updated_at for state in states), default=generated_at),
            )

        aggregate("core", ["online", "series"])
        aggregate("overview", ["review", "radar", "trend", "friends", "relations"])
        status_summary: list[str] = []
        if series_progress:
            status_summary.append(series_progress.summary)
        if want_core:
            official_count = len(online.get("official_sources") or [])
            fallback_count = len(online.get("search_fallbacks") or [])
            status_summary.append(
                f"正版/官方平台：{official_count} 个候选"
                if official_count else f"正版平台暂不可核验；保留 {fallback_count} 个搜索入口"
                if fallback_count else "暂未找到可靠正版平台入口"
            )
        if want_videos:
            public_uploads = len(bilibili.watch_candidates) if bilibili else 0
            status_summary.append(
                f"B站普通投稿可看正片候选：{public_uploads} 个（非正版入口）"
                if public_uploads else "未发现通过身份、时长与内容核验的B站普通投稿正片"
            )
        if want_releases:
            status_summary.append(
                f"离线入口：{len(releases.get('groups') or [])} 个 RSS/收藏组 · {len(releases.get('fallback_items') or [])} 条兜底"
                if releases else "本轮未返回离线入口"
            )
        caveats = [
            "B站番剧库页是平台正版入口；普通投稿即使包含完整动画，也只作为公开可看候选，版权与上传授权未核验。",
            "RSS、BT 与 BD 只聚合公开元数据和外链；Otomo 不代理、不托管、不自动下载。",
            "Bangumi 与下载器写操作都需要用户明确发起；Bangumi写回仍会进入二次确认。",
        ]
        for name, state in modules.items():
            if state.status == "failed" and name not in {"identity", "core", "overview"}:
                caveats.append(f"{name} 模块加载失败，可单独重试：{state.error}")
        if bilibili:
            caveats.extend(bilibili.warnings[:3])
        return ToolResult(
            ok=True,
            data=AnimeWatchHubResult(
                **base_result,
                overview=overview,
                reputation=review,
                relations=relations,
                episode_radar=radar,
                trend=trend,
                music=music,
                online=online,
                releases=releases,
                bilibili=bilibili,
                series_progress=series_progress,
                staff_signals=staff_signals[:8],
                status_summary=status_summary,
                quick_actions=["更新分集进度", "加入本地计划", "打开正版入口", "选择字幕组", "关注作品更新"],
                modules=modules,
                caveats=list(dict.fromkeys(caveats)),
            ),
            sources=list({source.url: source for source in sources if source.url}.values())[:20],
        )


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
            self.relations.run(SubjectRelationsArgs(subject_id=sid, limit=30)),
        ]
        if args.include_watch:
            jobs.append(self.watch.run(WhereToWatchArgs(subject_id=sid, title=subject["name"])))
        if stype_name == "anime":
            if args.include_release:
                jobs.append(self.release.run(AnimeReleaseFeedsArgs(subject_id=sid, title=subject["name"], prefer="auto", limit=8)))
            jobs.extend([
                self.radar.run(EpisodeRadarArgs(subject_id=sid, top=5, with_summary=False)),
                self.order.run(WatchOrderArgs(title=subject["name"], subject_type="anime")),
                self.music.run(AnimeMusicThemesArgs(subject_id=sid, title=subject["name"], limit=12)),
            ])
        results = await gather_limited(jobs, host="bangumi")
        # 口碑走势（netaba.re）：独立 host 槽避免与 bangumi 批次嵌套；失败静默不拖垮档案
        from ..netabare.tool import SubjectTrendArgs, SubjectTrendTool

        trend_res = await gather_limited(
            [SubjectTrendTool(self.client).run(SubjectTrendArgs(subject_id=sid, days=365))],
            host="netabare",
            return_exceptions=True,
        )
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
        trend = trend_res[0] if trend_res else None
        if not isinstance(trend, Exception) and getattr(trend, "ok", False) and trend.data is not None:
            tp = trend.data
            sections.append(ProductSection(
                title="口碑走势",
                items=[{
                    "summary": tp.summary,
                    "current_score": tp.current_score,
                    "score_change_30d": tp.score_change_30d,
                    "score_change_90d": tp.score_change_90d,
                    "pre_air_wish": tp.pre_air_wish,
                    "rating_std": tp.rating_std,
                    "controversy": tp.controversy,
                    "netabare_url": tp.netabare_url,
                }],
                notes=["走势数据来自 netaba.re 每日快照（近一年窗口）；分布统计取 Bangumi 官方实时。"],
            ))
            sources.append(Citation(title=f"netaba.re · {tp.title}", url=tp.netabare_url, source="netabare"))
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
        AnimeWatchHubTool(client),
        SubjectDossierTool(client),
        FranchiseMapTool(client),
        MonthlyWatchReportTool(client),
    ]
