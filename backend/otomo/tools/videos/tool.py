"""相关视频外链工具（外部知识增强档之一）。

给作品/角色/话题构造 B站搜索外链（综合 / 解析考据 / 二创MAD），作为"延伸观看"。
**仅 link-out**：不调 B站 API、不抓取、不嵌入视频（避免反爬与版权）。
"""
from __future__ import annotations

import asyncio
from http.cookiejar import LoadError, MozillaCookieJar
from datetime import datetime, timezone
import urllib.parse
import html
import re
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import Literal
import xml.etree.ElementTree as ET

import httpx
from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from .._cache import acached, scached
from .._concurrency import gather_limited
from ..media_identity import assess_media_scope, build_media_identity
from .._persistent_cache import PersistentJsonCache
from ..review.tool import (
    AspectOpinion,
    AspectSummary,
    CommentEvidence,
    _build_aspect_summary,
    _extract_aspect_opinions,
    _format_aspect_summary,
)

_BILI_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
_BILI_REPLY_API = "https://api.bilibili.com/x/v2/reply"
_BILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
_BILI_PAGELIST_API = "https://api.bilibili.com/x/player/pagelist"
_BILI_PLAYER_API = "https://api.bilibili.com/x/player/v2"
_BILI_NAV_API = "https://api.bilibili.com/x/web-interface/nav"
_BILI_DANMAKU_API = "https://comment.bilibili.com/{cid}.xml"
_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


class VideosArgs(BaseModel):
    query: str = Field(..., description="作品 / 角色 / 话题名，如『孤独摇滚 后藤一里』")


class VideoLink(BaseModel):
    label: str
    url: str


class GuideVideosArgs(BaseModel):
    query: str = Field(..., description="作品名、季度或话题，如『2026年7月新番』/『摇曳露营』")
    intent: Literal["season", "review", "yuri", "kirara", "data", "all"] = Field(
        "all", description="导视意图：season 季度导视 / review 漫评 / yuri 百合 / kirara 芳文社 / data 数据向"
    )
    tags: list[str] | None = Field(None, description="可选题材标签，如 ['百合','芳文社','日常']")
    limit: int = Field(6, ge=1, le=10)


class BiliGuideSearchArgs(BaseModel):
    query: str = Field(..., description="导视/漫评搜索词，如『2026年7月 新番导视』")
    tags: list[str] | None = Field(None, description="可选题材标签，如 百合/芳文社/数据向")
    whitelist_only: bool = Field(False, description="是否强制只保留白名单 UP；默认 false，白名单仅作信任加分")
    limit: int = Field(8, ge=1, le=20)


class BiliSubjectVideosArgs(BaseModel):
    query: str = Field(..., description="动画主标题")
    aliases: list[str] = Field(default_factory=list, description="中日英别名；用于排除同名、续作和重制版误配")
    staff_names: list[str] = Field(default_factory=list, description="Bangumi 制作方/Staff 名称；只作为作者身份信号")
    expected_episode_minutes: float | None = Field(None, gt=0, le=300, description="Bangumi 条目给出的单集/影片时长，用于识别短篇与排除残缺正片")
    subject_platform: str = Field("", description="Bangumi 条目的媒介形态，如 TV/Web/剧场版；用于排除跨篇章误配")
    lifecycle: Literal["upcoming", "airing", "recent", "archive", "unknown"] = "unknown"
    lifecycle_phase: str = Field("", description="更细的媒介生命周期，如 theatrical/awaiting_bd")
    media_kind: Literal["tv", "web", "movie", "ova", "unknown"] = "unknown"
    preferred_uploaders: list[str] = Field(default_factory=list, description="用户明确偏好的 UP")
    muted_uploaders: list[str] = Field(default_factory=list, description="用户明确减少推荐的 UP")
    hidden_video_ids: list[str] = Field(default_factory=list, description="用户标记不相关的视频")
    limit: int = Field(5, ge=1, le=10)


class BiliVideoCommentsArgs(BaseModel):
    aid: int = Field(..., description="B站 av/aid；可先用 search_bilibili_guide_videos 获得")
    query: str | None = Field(None, description="可选语义关键词；当前只做轻量词法优先，不做全文 RAG")
    limit: int = Field(20, ge=1, le=50)


class BiliVideoSubtitleArgs(BaseModel):
    aid: int | None = Field(None, description="B站 av/aid；aid 或 bvid 至少传一个")
    bvid: str | None = Field(None, description="B站 BV 号；aid 或 bvid 至少传一个")
    max_segments: int = Field(60, ge=10, le=160, description="最多返回多少条字幕片段")
    allow_asr: bool = Field(True, description="公开字幕不存在时是否允许使用已配置的 ASR 兜底")
    sample_across_video: bool = Field(False, description="长字幕是否从全片均匀抽样；内容匹配核验时启用")


class BiliVideoDanmakuArgs(BaseModel):
    aid: int | None = Field(None, description="B站 av/aid；aid 或 bvid 至少传一个")
    bvid: str | None = Field(None, description="B站 BV 号；aid 或 bvid 至少传一个")
    limit: int = Field(80, ge=10, le=200)
    query: str | None = Field(None, description="可选关键词，优先返回相关弹幕")


class BiliVideoContentArgs(BaseModel):
    url: str | None = Field(None, description="B站视频 URL；可直接传 https://www.bilibili.com/video/BV... 或 av...")
    aid: int | None = Field(None, description="B站 av/aid；aid 或 bvid 至少传一个")
    bvid: str | None = Field(None, description="B站 BV 号；aid 或 bvid 至少传一个")
    query: str | None = Field(None, description="关注点，如『新番导视提到哪些作品』")
    limit: int = Field(80, ge=10, le=200)


class SubjectVertical(BaseModel):
    name: str
    label: str
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)


class GuideVideoHit(BaseModel):
    title: str
    url: str
    aid: int | None = None
    bvid: str | None = None
    mid: int | None = None
    author: str
    thumbnail_url: str | None = None
    play: int | None = None
    danmaku: int | None = None
    pubdate: int | None = None
    content_type: Literal["preseason_guide", "airing_review", "season_recap", "general"] = "general"
    content_type_reason: str = ""
    discovery_source: Literal["preferred", "whitelist", "discovered"] = "whitelist"
    matched_whitelist: bool = True
    trust_tier: Literal["preferred", "known", "content_verified", "metadata_verified"] = "known"
    match_confidence: float = 0.0
    match_reason: str = ""
    verification_status: Literal["content_verified", "view_verified", "search_metadata"] = "search_metadata"
    content_verified: bool = False
    content_match_confidence: float = 0.0
    content_match_reason: str = ""
    transcript_source: Literal["subtitle", "asr", "none"] = "none"
    content_mentions: int = 0
    content_required: bool = False


class GuideVideoLink(BaseModel):
    label: str
    url: str
    up_name: str
    up_url: str
    positioning: str
    match_reason: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    route_score: int = 0
    discovery_source: Literal["preferred", "whitelist", "discovered"] = "whitelist"
    matched_whitelist: bool = True
    trust_tier: Literal["preferred", "known", "content_verified", "metadata_verified"] = "known"
    verticals: list[SubjectVertical] = Field(default_factory=list)
    verified: bool = False
    verified_hits: list[GuideVideoHit] = Field(default_factory=list)
    verification_query: str = ""
    verification_note: str = ""
    publication_status: Literal["navigation", "published", "not_found", "unavailable", "rejected"] = "navigation"


class BiliVideoMeta(BaseModel):
    title: str
    url: str
    aid: int | None = None
    bvid: str | None = None
    author: str
    mid: int | None = None
    thumbnail_url: str | None = None
    play: int | None = None
    danmaku: int | None = None
    pubdate: int | None = None
    content_type: Literal["preseason_guide", "airing_review", "season_recap", "general"] = "general"
    content_type_reason: str = ""
    matched_whitelist: bool = False
    match_confidence: float = 0.0
    match_reason: str = ""
    verified: bool = False
    verification_status: Literal["content_verified", "view_verified", "search_metadata"] = "search_metadata"
    content_verified: bool = False
    content_match_confidence: float = 0.0
    content_match_reason: str = ""
    transcript_source: Literal["subtitle", "asr", "none"] = "none"
    content_mentions: int = 0


class BiliRejectedCandidate(BaseModel):
    title: str
    author: str = ""
    match_confidence: float = 0.0
    reason: str = ""


class BiliGuideSearchResult(BaseModel):
    query: str
    count: int
    videos: list[BiliVideoMeta] = Field(default_factory=list)
    navigation_url: str = ""
    rejected: list[BiliRejectedCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BiliSubjectVideoMeta(BiliVideoMeta):
    role: Literal[
        "public_full_episode",
        "episode_candidate",
        "official_pv",
        "review",
        "retrospective",
        "fan_creation",
        "related",
    ] = "related"
    uploader_class: Literal[
        "platform_account",
        "staff_or_production",
        "self_claimed_official",
        "creator",
        "unknown",
    ] = "unknown"
    watch_candidate: bool = False
    identity_evidence: list[str] = Field(default_factory=list)
    content_evidence: list[str] = Field(default_factory=list)
    duration_seconds: int | None = None
    page_count: int = 1
    page_titles: list[str] = Field(default_factory=list)
    page_links: list[dict[str, object]] = Field(default_factory=list)
    episode_coverage: str = ""
    copyright_declaration: Literal["original", "repost", "unknown"] = "unknown"
    rights_status: Literal["uploader_rights_unknown"] = "uploader_rights_unknown"
    editorial_role: Literal[
        "watch", "official", "no_spoiler_review", "review", "deep_analysis", "recap", "fan", "related"
    ] = "related"
    spoiler_risk: Literal["none", "low", "medium", "high", "unknown"] = "unknown"
    caution: str = ""


class BiliVersionConflict(BaseModel):
    title: str
    url: str = ""
    aid: int | None = None
    bvid: str | None = None
    author: str = ""
    reason: str = ""
    suggested_subject_id: int | None = None
    suggested_subject_title: str = ""
    suggested_relation: str = ""
    suggested_collection_state: str = ""
    suggested_collection_label: str = ""
    suggested_completed: bool | None = None


class BiliSubjectVideosResult(BaseModel):
    query: str
    count: int
    watch_candidates: list[BiliSubjectVideoMeta] = Field(default_factory=list)
    videos: list[BiliSubjectVideoMeta] = Field(default_factory=list)
    version_conflicts: list[BiliVersionConflict] = Field(default_factory=list)
    navigation_url: str = ""
    cache_hit: bool = False
    search_partial: bool = False
    rate_limited: bool = False
    last_verified: str = ""
    account_mode: Literal["public", "cookie"] = "public"
    warnings: list[str] = Field(default_factory=list)


class BiliVideoCommentsResult(BaseModel):
    aid: int
    count: int
    comments: list[str] = Field(default_factory=list)
    aspect_opinions: list[AspectOpinion] = Field(default_factory=list)
    aspect_summary: list[AspectSummary] = Field(default_factory=list)
    opinion_summary: list[str] = Field(default_factory=list)
    source_url: str
    caveats: list[str] = Field(default_factory=list)


class BiliSubtitleSegment(BaseModel):
    start: float | None = None
    end: float | None = None
    text: str


class BiliTranscriptMatch(BaseModel):
    verified: bool = False
    confidence: float = 0.0
    reason: str = ""
    source: Literal["subtitle", "asr", "none"] = "none"
    mentions: int = 0


class BiliVideoSubtitleResult(BaseModel):
    aid: int | None = None
    bvid: str | None = None
    cid: int | None = None
    subtitle_url: str = ""
    source: Literal["bili_public_subtitle", "bili_asr"] = "bili_public_subtitle"
    count: int = 0
    segments: list[BiliSubtitleSegment] = Field(default_factory=list)
    rough_summary: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class BiliDanmakuItem(BaseModel):
    time: float | None = None
    text: str


class BiliVideoDanmakuResult(BaseModel):
    aid: int | None = None
    bvid: str | None = None
    cid: int | None = None
    count: int = 0
    danmaku: list[BiliDanmakuItem] = Field(default_factory=list)
    aspect_opinions: list[AspectOpinion] = Field(default_factory=list)
    aspect_summary: list[AspectSummary] = Field(default_factory=list)
    opinion_summary: list[str] = Field(default_factory=list)
    source_url: str = ""
    caveats: list[str] = Field(default_factory=list)


class BiliVideoContentResult(BaseModel):
    aid: int | None = None
    bvid: str | None = None
    cid: int | None = None
    title: str = ""
    source_url: str = ""
    access_level: Literal["multi", "subtitle", "asr", "danmaku", "comments", "metadata", "unavailable"] = "unavailable"
    read_layers: list[str] = Field(default_factory=list)
    content_summary: list[str] = Field(default_factory=list)
    audience_summary: list[str] = Field(default_factory=list)
    subtitle_summary: list[str] = Field(default_factory=list)
    danmaku_summary: list[str] = Field(default_factory=list)
    comment_summary: list[str] = Field(default_factory=list)
    metadata_summary: list[str] = Field(default_factory=list)
    subtitle_segments: list[BiliSubtitleSegment] = Field(default_factory=list)
    danmaku_samples: list[BiliDanmakuItem] = Field(default_factory=list)
    comment_samples: list[str] = Field(default_factory=list)
    analysis_plan: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class VideosResult(BaseModel):
    query: str
    links: list[VideoLink] = Field(default_factory=list)


class GuideVideosResult(BaseModel):
    query: str
    intent: str
    links: list[GuideVideoLink] = Field(default_factory=list)


def _clean_bili_title(value: str) -> str:
    value = re.sub(r"</?em[^>]*>", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


def _clean_bili_image(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("//"):
        return "https:" + value
    return value.replace("http://", "https://")


def _bili(keyword: str) -> str:
    return f"https://search.bilibili.com/all?keyword={urllib.parse.quote(keyword)}"


def _space(uid: str) -> str:
    return f"https://space.bilibili.com/{uid}"


_GUIDE_UPS: list[dict] = [
    {
        "name": "名作之壁吧",
        "uid": "2859372",
        "positioning": "数据向新番导视/季度盘点",
        "intents": {"season", "data", "all"},
        "keywords": ["新番导视", "新番推荐", "季度新番"],
        "tags": {"新番", "季度", "数据"},
        "domains": {"data_interest", "season_general"},
    },
    {
        "name": "泛式",
        "uid": "63231",
        "positioning": "评价向导视/综合漫评",
        "intents": {"season", "review", "all"},
        "keywords": ["新番导视", "评价", "推荐"],
        "tags": {"新番", "季度", "漫评", "评价"},
        "domains": {"mainstream_review", "season_general", "controversial"},
    },
    {
        "name": "瓶子君152",
        "uid": "730732",
        "positioning": "评价向漫评/动画杂谈",
        "intents": {"season", "review", "all"},
        "keywords": ["新番导视", "评价", "杂谈"],
        "tags": {"新番", "季度", "漫评", "评价"},
        "domains": {"mainstream_review", "season_general", "controversial"},
    },
    {
        "name": "台长",
        "uid": "213741",
        "positioning": "综合漫评/动画杂谈",
        "intents": {"season", "review", "all"},
        "keywords": ["新番导视", "评价", "杂谈"],
        "tags": {"新番", "季度", "漫评", "评价"},
        "domains": {"mainstream_review", "season_general", "controversial"},
    },
    {
        "name": "FlowerMX-花梦",
        "uid": "13181306",
        "positioning": "百合向新番导视/推荐",
        "intents": {"season", "review", "yuri", "all"},
        "keywords": ["百合", "新番导视", "推荐"],
        "tags": {"百合", "GL", "新番", "季度"},
        "domains": {"yuri_core", "yuri_adjacent", "season_general"},
    },
    {
        "name": "峻岸上的喀秋莎_Channel",
        "uid": "228172909",
        "positioning": "百合作品翻译/介绍",
        "intents": {"yuri", "review", "all"},
        "keywords": ["百合", "介绍", "推荐"],
        "tags": {"百合", "GL", "翻译"},
        "domains": {"yuri_core", "yuri_adjacent"},
    },
    {
        "name": "芳文观星台",
        "uid": "1585955812",
        "positioning": "芳文社/Kirara 系盘点",
        "intents": {"kirara", "review", "all"},
        "keywords": ["芳文社", "Kirara", "きらら"],
        "tags": {"芳文社", "Kirara", "きらら", "日常"},
        "domains": {"kirara", "cute_girls_daily"},
    },
    {
        "name": "大猫猫组",
        "uid": "526330959",
        "positioning": "芳文社/Kirara 系内容",
        "intents": {"kirara", "review", "all"},
        "keywords": ["芳文社", "Kirara", "きらら"],
        "tags": {"芳文社", "Kirara", "きらら", "日常"},
        "domains": {"kirara", "cute_girls_daily"},
    },
]


def _tag_intents(tags: list[str]) -> set[str]:
    text = " ".join(tags)
    intents: set[str] = set()
    if any(k in text for k in ("百合", "GL")):
        intents.add("yuri")
    if any(k in text for k in ("芳文", "Kirara", "きらら")):
        intents.add("kirara")
    if any(k in text for k in ("数据", "榜", "评分", "导视")):
        intents.add("data")
    return intents


def _norm_video_text(value: str | None) -> str:
    return "".join(ch.lower() for ch in (value or "") if ch.isalnum())


def _contains_any(text: str, keys: tuple[str, ...]) -> list[str]:
    return [k for k in keys if k and k in text]


def classify_subject_verticals(
    tags: list[str] | None = None,
    *,
    title: str = "",
    studio: str = "",
    extra_text: str = "",
) -> list[SubjectVertical]:
    """把作品标签/标题映射到可解释圈层，供导视源路由使用。

    这里只做保守启发式，不把"轻百合/女性主角群像"等价成强百合，也不把动画制作公司误判成芳文社。
    真正能不能引用某 UP，后续还要经过 B站视频命中验证。
    """
    tags = tags or []
    text = " ".join([title, studio, extra_text, *tags])
    lower = text.lower()
    out: list[SubjectVertical] = []

    yuri_core = _contains_any(lower, ("百合", "gl", "girls love", "ガールズラブ", "百合姫"))
    if yuri_core:
        out.append(SubjectVertical(
            name="yuri_core",
            label="明确百合",
            confidence=0.86,
            evidence=[f"命中百合关键词：{', '.join(yuri_core[:3])}"],
        ))
    else:
        yuri_adjacent = _contains_any(lower, ("轻百合", "輕百合", "女性主角", "女孩子", "女子", "girls band", "少女乐队"))
        cute_daily = _contains_any(lower, ("日常", "治愈", "校园", "空气系", "萌系", "cute girls"))
        if yuri_adjacent and cute_daily:
            out.append(SubjectVertical(
                name="yuri_adjacent",
                label="百合邻近",
                confidence=0.58,
                evidence=[f"女性主角群像/轻百合信号：{', '.join((yuri_adjacent + cute_daily)[:4])}"],
            ))

    kirara = _contains_any(lower, ("芳文", "kirara", "きらら", "まんがタイム"))
    if kirara:
        out.append(SubjectVertical(
            name="kirara",
            label="芳文社/Kirara",
            confidence=0.88,
            evidence=[f"命中芳文/Kirara 关键词：{', '.join(kirara[:3])}"],
        ))

    cute = _contains_any(lower, ("日常", "治愈", "萌系", "空气系", "女子高生", "轻百合"))
    if cute and not any(v.name == "kirara" for v in out):
        out.append(SubjectVertical(
            name="cute_girls_daily",
            label="萌系日常",
            confidence=0.55,
            evidence=[f"命中日常/治愈/萌系标签：{', '.join(cute[:4])}"],
        ))

    data = _contains_any(lower, ("数据", "榜", "评分", "导视", "年度", "季度"))
    if data:
        out.append(SubjectVertical(
            name="data_interest",
            label="数据向导视",
            confidence=0.62,
            evidence=[f"查询/标签偏数据向：{', '.join(data[:3])}"],
        ))

    out.append(SubjectVertical(
        name="mainstream_review",
        label="泛用漫评",
        confidence=0.45,
        evidence=["默认保留泛用漫评源作为兜底，不代表该 UP 已覆盖具体作品。"],
    ))
    dedup: dict[str, SubjectVertical] = {}
    for item in out:
        old = dedup.get(item.name)
        if old is None or item.confidence > old.confidence:
            dedup[item.name] = item
    return sorted(dedup.values(), key=lambda x: -x.confidence)


def _guide_score(up: dict, intent: str, tags: list[str], verticals: list[SubjectVertical] | None = None) -> tuple[int, str, str]:
    score = 0
    reasons: list[str] = []
    if intent in up["intents"]:
        score += 3
        reasons.append(f"匹配 {intent} 场景")
    tag_hits = [t for t in tags if any(t in str(ut) or str(ut) in t for ut in up.get("tags", set()))]
    if tag_hits:
        score += 2 + min(len(tag_hits), 2)
        reasons.append("标签命中：" + "、".join(tag_hits[:3]))
    for inferred in _tag_intents(tags):
        if inferred in up["intents"]:
            score += 2
            reasons.append(f"由标签推断适合 {inferred}")
    for vertical in verticals or []:
        if vertical.name in up.get("domains", set()):
            add = max(1, round(vertical.confidence * 4))
            score += add
            reasons.append(f"圈层 {vertical.label}({vertical.confidence:.2f}) → {up['name']}")
    confidence = "high" if score >= 7 else ("medium" if score >= 3 else "low")
    return score, "；".join(dict.fromkeys(reasons)) or "通用导视入口", confidence


def guide_source_catalog() -> list[dict[str, str]]:
    return [
        {
            "name": str(up["name"]),
            "positioning": str(up["positioning"]),
            "up_url": _space(str(up["uid"])),
        }
        for up in _GUIDE_UPS
    ]


def _guide_links(
    query: str,
    intent: str,
    limit: int,
    tags: list[str] | None = None,
    preferred_sources: list[str] | None = None,
) -> list[GuideVideoLink]:
    q = query.strip()
    tags = tags or []
    preference_order = [name.strip() for name in (preferred_sources or []) if name.strip()]
    preference_rank = {name: index for index, name in enumerate(preference_order)}
    verticals = classify_subject_verticals(tags, title=q)
    ranked: list[tuple[int, int, GuideVideoLink]] = []
    for up in _GUIDE_UPS:
        if preference_order and up["name"] not in preference_rank:
            continue
        score, reason, confidence = _guide_score(up, intent, tags, verticals)
        if up["name"] in preference_rank:
            preference_bonus = max(1, 8 - preference_rank[up["name"]])
            score += preference_bonus
            reason = "；".join(x for x in [f"你的来源偏好 +{preference_bonus}", reason] if x)
            confidence = "high" if score >= 7 else confidence
        if intent != "all" and score <= 0:
            continue
        keyword_tag = next((t for t in tags if t in up.get("tags", set())), "")
        keyword = " ".join([q, up["name"], keyword_tag or up["keywords"][0]]).strip()
        route_verticals = [v for v in verticals if v.name in up.get("domains", set())]
        ranked.append((
            score,
            len(ranked),
            GuideVideoLink(
                label=f"{q} · {up['name']}",
                url=_bili(keyword),
                up_name=up["name"],
                up_url=_space(up["uid"]),
                positioning=up["positioning"],
                match_reason=reason,
                confidence=confidence,
                route_score=score,
                verticals=route_verticals or verticals[:1],
                verification_query=keyword,
                verification_note="尚未验证具体视频命中，仅作为白名单导航入口。",
            ),
        ))
    if preference_order:
        ranked.sort(key=lambda x: (preference_rank.get(x[2].up_name, len(preference_rank)), -x[0], x[1]))
    else:
        ranked.sort(key=lambda x: (-x[0], x[1]))
    return [x[2] for x in ranked[:limit]]


def _season_markers(value: str) -> dict[str, str]:
    lower = value.lower()
    year = re.search(r"(?<!\d)(20\d{2})(?!\d)", lower)
    month = re.search(r"(?<!\d)(1[0-2]|[1-9])\s*月", lower)
    numbered = re.search(r"第\s*([0-9一二三四五六七八九十]+)\s*[季期部]", lower)
    if numbered is None:
        numbered = re.search(r"(?:season|s)\s*([0-9]+)", lower)
    edition = next((token for token in ("剧场版", "重制版", "重制", "remake") if token in lower), "")
    return {
        "year": year.group(1) if year else "",
        "month": month.group(1) if month else "",
        "numbered": numbered.group(1) if numbered else "",
        "edition": edition,
    }


def classify_season_video(
    title: str,
    pubdate: int | None,
    season_query: str,
) -> tuple[Literal["preseason_guide", "airing_review", "season_recap", "general"], str]:
    """Classify a seasonal video by editorial intent, not merely by month tokens."""
    clean = _clean_bili_title(title).lower()
    if any(word in clean for word in ("完结", "季末", "季度总结", "季度复盘", "回顾", "年终", "年度总结")):
        return "season_recap", "标题表明它是季度回顾或完结复盘"
    if any(word in clean for word in ("看完", "开播", "首集", "第一集", "初印象", "追番", "中期", "热播", "观望")):
        return "airing_review", "标题表明它是开播后的追番观察或漫评"
    if any(word in clean for word in ("导视", "前瞻", "前导", "新番推荐", "新番介绍", "季度新番", "preview")):
        return "preseason_guide", "标题明确表明它是播前导视或季度前瞻"

    markers = _season_markers(season_query)
    if pubdate and markers["year"] and markers["month"] and "新番" in clean:
        target = datetime(int(markers["year"]), int(markers["month"]), 1, tzinfo=timezone.utc)
        published = datetime.fromtimestamp(int(pubdate), timezone.utc)
        if published < target:
            return "preseason_guide", "发布时间早于季度开播，且标题明确讨论新番"
        if published < _season_end(target):
            return "airing_review", "发布时间位于季度播出期，且标题明确讨论新番"
    return "general", "标题未能明确区分播前导视、热播漫评或季度复盘"


_SUBJECT_EPISODE_RE = re.compile(
    r"(?:第\s*[0-9一二三四五六七八九十百]+\s*[话話集]|(?:episode|ep)\s*[0-9]+)",
    re.IGNORECASE,
)

_SUBJECT_EPISODE_NUMBER_RE = re.compile(
    r"(?:第\s*([0-9一二三四五六七八九十百]+)\s*[话話集]|(?:episode|ep)\s*0*([0-9]+))",
    re.IGNORECASE,
)


def _chinese_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "百" in value:
        left, right = value.split("百", 1)
        hundreds = digits.get(left, 1) * 100
        tail = _chinese_number(right) if right else 0
        return hundreds + (tail or 0)
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) * 10
        return tens + digits.get(right, 0)
    return digits.get(value)


def _episode_coverage(title: str, page_titles: list[str]) -> str:
    numbers: set[int] = set()
    for value in [title, *page_titles]:
        for match in _SUBJECT_EPISODE_NUMBER_RE.finditer(value or ""):
            token = next((item for item in match.groups() if item), "")
            number = _chinese_number(token)
            if number is not None and 0 < number <= 10000:
                numbers.add(number)
    ordered = sorted(numbers)
    if len(ordered) >= 2:
        if ordered == list(range(ordered[0], ordered[-1] + 1)):
            return f"第 {ordered[0]}–{ordered[-1]} 话"
        preview = "、".join(str(item) for item in ordered[:6])
        return f"可识别分集：{preview}{'…' if len(ordered) > 6 else ''}"
    if ordered:
        return f"第 {ordered[0]} 话"
    return ""


def _subject_version_compatibility(
    aliases: list[str],
    title: str,
    page_titles: list[str] | None = None,
    subject_platform: str = "",
) -> tuple[bool, str]:
    """Compatibility wrapper backed by the shared media identity module."""
    identity = build_media_identity(
        title=aliases[0] if aliases else title,
        aliases=aliases,
        platform=subject_platform,
    )
    scope = assess_media_scope(identity, title, page_titles)
    if scope.status in {"conflict", "bundle"}:
        return False, scope.reason
    return True, scope.reason if scope.status == "exact" else ""


def classify_subject_video(
    title: str,
    author: str,
    description: str = "",
    *,
    staff_names: list[str] | None = None,
    duration_seconds: int | None = None,
    page_titles: list[str] | None = None,
    copyright_code: int | None = None,
    match_confidence: float = 0.0,
    expected_duration_seconds: int | None = None,
) -> tuple[
    Literal[
        "public_full_episode", "episode_candidate", "official_pv", "review",
        "retrospective", "fan_creation", "related",
    ],
    Literal[
        "platform_account", "staff_or_production", "self_claimed_official",
        "creator", "unknown",
    ],
    bool,
    list[str],
    list[str],
    str,
]:
    """Classify a public Bilibili upload by content shape, separately from licensed pages.

    Ordinary uploads can contain a complete episode or film even when the uploader cannot
    be mapped to a Bangumi staff name.  Playability therefore depends on work relevance,
    long-form metadata and episode/full-content signals; uploader identity is supplemental
    evidence only.  No ordinary upload is promoted to a licensed platform source.
    """
    clean_title = _clean_bili_title(title)
    lower = f"{clean_title} {author} {description}".lower()
    content_lower = f"{clean_title} {description}".lower()
    author_key = _norm_video_text(author)
    staff_hits = [
        name for name in (staff_names or [])
        if author_key
        and len(_norm_video_text(name)) >= 3
        and (
            _norm_video_text(name) == author_key
            or _norm_video_text(name) in author_key
            or author_key in _norm_video_text(name)
        )
    ]
    identity_evidence: list[str] = []
    content_evidence: list[str] = []
    known_platform = any(token in author_key for token in ("哔哩哔哩番剧", "bilibili番剧", "哔哩哔哩动画"))
    self_claimed = any(token in lower for token in ("官方账号", "官方频道", "official channel")) or any(
        token in author.lower() for token in ("官方", "official")
    )
    if known_platform:
        uploader_class = "platform_account"
        identity_evidence.append("作者名命中 Bilibili 动画/番剧平台账号信号")
    elif staff_hits:
        uploader_class = "staff_or_production"
        identity_evidence.append("作者名与 Bangumi 制作方/Staff 信号匹配：" + "、".join(staff_hits[:2]))
    elif self_claimed:
        uploader_class = "self_claimed_official"
        identity_evidence.append("作者或简介含官方身份自述，尚未由平台授权页交叉确认")
    else:
        uploader_class = "unknown"

    pages = [str(item or "").strip() for item in (page_titles or []) if str(item or "").strip()]
    duration = max(int(duration_seconds or 0), 0)
    expected_duration = max(int(expected_duration_seconds or 0), 0)
    page_episode_hits = sum(bool(_SUBJECT_EPISODE_RE.search(item)) for item in pages)
    copyright_declaration = "转载" if copyright_code == 2 else "自制" if copyright_code == 1 else "未知"
    if duration:
        content_evidence.append(f"稿件总时长约 {max(1, round(duration / 60))} 分钟")
    if expected_duration:
        content_evidence.append(f"条目参考时长约 {max(1, round(expected_duration / 60))} 分钟")
    if len(pages) > 1:
        content_evidence.append(f"稿件含 {len(pages)} 个分P，其中 {page_episode_hits} 个具有分集标题特征")
    if copyright_code in {1, 2}:
        content_evidence.append(f"B站投稿声明为“{copyright_declaration}”；该字段不等于版权授权证明")

    auxiliary_media = any(
        token in content_lower
        for token in (
            "op/ed", "op／ed", "剧中歌", "角色歌", "专辑", "演唱会", "演奏会",
            "live event", "concert", "ライブ", "特典", "素材", "剪辑",
        )
    )
    reaction = "reaction" in content_lower
    promo = "pv" in content_lower or bool(re.search(r"(?:^|\W)(?:teaser|trailer)(?:\W|$)", content_lower, re.IGNORECASE)) or any(
        token in content_lower for token in ("预告", "預告", "先导", "先導", "宣传片")
    )
    condensed_story = any(
        token in content_lower
        for token in (
            "一口气看完", "一口氣看完", "一口气看懂", "一口氣看懂",
            "剧情解说", "劇情解說", "动漫解说", "動漫解說", "动画解说", "動畫解說",
            "电影解说", "電影解說", "影视解说", "影視解說", "速看", "全剧情", "全劇情",
            "剧情梳理", "劇情梳理", "故事梳理", "剧情回顾", "劇情回顧", "浓缩", "濃縮",
        )
    ) or bool(re.search(r"\d+\s*(?:分钟|分鐘|分|min(?:ute)?s?)\s*(?:带你|帶你)?\s*看完", content_lower, re.IGNORECASE))
    retrospective = condensed_story or any(
        token in content_lower for token in ("完结评价", "完結", "回顾", "回顧", "复盘", "補番", "补番", "多年后")
    )
    review = any(token in content_lower for token in ("漫评", "评价", "解析", "解读", "吐槽", "初印象", "值不值得看"))
    fan = any(token in content_lower for token in ("mad", "amv", "手书", "混剪", "二创", "mmd"))
    excluded_context = auxiliary_media or reaction or promo or retrospective or review or fan
    explicit_episode = bool(_SUBJECT_EPISODE_RE.search(clean_title))
    explicit_full = any(
        token in content_lower for token in ("正片", "全片", "全集", "全话", "全話", "完整版", "本篇", "免费放送", "限时放送")
    )
    release_format = any(
        token in content_lower for token in ("中字", "字幕", "国语", "國語", "粤语", "粵語", "日语", "日語", "1080p", "720p", "bdrip", "web-dl", "无删减", "無刪減")
    )
    short_format = any(token in content_lower for token in ("泡面番", "短篇动画", "短篇動畫", "短片动画", "短片動畫"))
    multipart_episode = len(pages) > 1 and page_episode_hits > 0
    long_form = duration >= 12 * 60
    expected_length_ok = expected_duration > 0 and duration >= max(150, round(expected_duration * 0.75))
    compatible_length = expected_length_ok if expected_duration > 0 else long_form
    candidate_signal = not excluded_context and (
        explicit_episode or explicit_full or multipart_episode or (long_form and release_format)
    )
    strong_content = (
        candidate_signal
        and match_confidence >= 0.68
        and duration > 0
        and (
            (compatible_length and (explicit_episode or explicit_full or release_format))
            or (duration >= 8 * 60 and multipart_episode)
            or (duration >= 3 * 60 and short_format and (explicit_episode or explicit_full))
        )
    )
    if explicit_episode:
        content_evidence.append("标题具有明确分集特征")
    if explicit_full:
        content_evidence.append("标题或简介具有正片/完整内容特征")
    if release_format:
        content_evidence.append("标题或简介具有字幕、语言或发行格式特征")

    if strong_content:
        return (
            "public_full_episode", uploader_class, True, identity_evidence, content_evidence,
            "这是B站普通投稿中的完整动画内容候选，可打开观看，但不是番剧库正版入口；版权与上传授权未核验。",
        )
    if candidate_signal:
        return (
            "episode_candidate", uploader_class, False, identity_evidence, content_evidence,
            "标题像正片，但时长、分P或作品一致性证据不足；折叠展示，不作为默认观看入口。",
        )
    if promo:
        if uploader_class in {"platform_account", "staff_or_production", "self_claimed_official"}:
            content_evidence.append("标题具有 PV/预告特征")
            return "official_pv", uploader_class, False, identity_evidence, content_evidence, "宣传内容不是完整正片。"
        return "related", uploader_class, False, identity_evidence, content_evidence, "PV 作者身份未确认。"
    # OP/ED/特典合集和纯 reaction 并非作品观看链路的核心结果。它们仍可从
    # B站搜索导航抵达，但不占用有限的编辑卡片名额。
    if auxiliary_media or reaction:
        return "related", uploader_class, False, identity_evidence, content_evidence, "音乐/特典合集或 reaction 不进入默认作品视频卡片。"
    if retrospective:
        return "retrospective", "creator" if uploader_class == "unknown" else uploader_class, False, identity_evidence, content_evidence, "回顾/复盘属于观点内容。"
    if review:
        return "review", "creator" if uploader_class == "unknown" else uploader_class, False, identity_evidence, content_evidence, "漫评属于观点内容，不是正版播放入口。"
    if fan:
        return "fan_creation", "creator" if uploader_class == "unknown" else uploader_class, False, identity_evidence, content_evidence, "二创内容不是正片播放入口。"
    return "related", uploader_class, False, identity_evidence, content_evidence, "相关性依据标题和稿件详情，打开后仍应核对内容。"


def _editorial_role(
    title: str, role: str,
) -> tuple[
    Literal["watch", "official", "no_spoiler_review", "review", "deep_analysis", "recap", "fan", "related"],
    Literal["none", "low", "medium", "high", "unknown"],
]:
    lower = title.lower()
    if role in {"public_full_episode", "episode_candidate"}:
        return "watch", "high"
    if role == "official_pv":
        return "official", "none"
    if role == "fan_creation":
        return "fan", "unknown"
    if role == "retrospective":
        return "recap", "high"
    if role == "review":
        if any(token in lower for token in ("无剧透", "無劇透", "不剧透", "不劇透", "初印象", "值不值得", "是否值得", "追不追")):
            return "no_spoiler_review", "low"
        if any(token in lower for token in ("作画", "作畫", "制作", "製作", "演出", "分镜", "分鏡", "脚本", "劇本", "深度", "考据", "考據", "解析")):
            return "deep_analysis", "medium"
        return "review", "medium"
    return "related", "unknown"


def _season_end(start: datetime) -> datetime:
    month = start.month + 3
    year = start.year
    if month > 12:
        month -= 12
        year += 1
    return datetime(year, month, 1, tzinfo=start.tzinfo)


def _content_terms(value: str) -> list[str]:
    cleaned = re.sub(
        r"(20\d{2}\s*年?|[0-9]{1,2}\s*月|新番导视|新番推荐|导视|漫评|评价|杂谈|推荐|解析|盘点|视频)",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    return [
        term for term in re.split(r"[\s,，。:：/|·\-]+", cleaned)
        if len(_norm_video_text(term)) >= 2
    ][:8]


def _match_video_transcript(
    query: str,
    title: str,
    segments: list[BiliSubtitleSegment],
    *,
    source: Literal["subtitle", "asr"],
) -> BiliTranscriptMatch:
    """Conservatively decide whether the transcript actually covers the requested work."""
    text_rows = [segment.text.strip() for segment in segments if segment.text.strip()]
    if not text_rows:
        return BiliTranscriptMatch(reason="未读到可核验的字幕正文")
    full_text = " ".join(text_rows)
    full_key = _norm_video_text(full_text)
    title_key = _norm_video_text(title)
    terms = list(dict.fromkeys(_content_terms(query)))
    term_keys = [
        _norm_video_text(term) for term in terms
        if len(_norm_video_text(term)) >= 2
    ]
    # Longer title tokens carry more identity than generic words such as “动画”或“推荐”。
    identity_terms = sorted(term_keys, key=len, reverse=True)[:3]
    row_keys = [_norm_video_text(row) for row in text_rows]
    mentions = sum(
        1 for row in row_keys
        if any(term in row for term in identity_terms)
    ) if identity_terms else 0
    matched_terms = [term for term in identity_terms if term in full_key]
    query_markers = _season_markers(query)
    transcript_markers = _season_markers(full_text)
    reasons: list[str] = []

    for key, label in (("year", "年份"), ("month", "月份"), ("numbered", "季度/续作编号"), ("edition", "版本")):
        expected = query_markers[key]
        actual = transcript_markers[key]
        if expected and actual and expected != actual:
            return BiliTranscriptMatch(
                confidence=0.12,
                reason=f"字幕中的{label}是 {actual}，与目标 {expected} 冲突",
                source=source,
                mentions=mentions,
            )
        if expected and actual == expected:
            reasons.append(f"字幕确认{label} {expected}")

    is_broad_guide = not identity_terms and any(
        marker in query for marker in ("新番", "导视", "季度", "盘点")
    )
    if is_broad_guide:
        guide_hits = [
            marker for marker in ("新番", "导视", "动画", "作品", "推荐")
            if marker in full_text
        ]
        marker_hits = sum(
            1 for key in ("year", "month")
            if query_markers[key] and transcript_markers[key] == query_markers[key]
        )
        confidence = min(0.92, 0.58 + 0.08 * len(guide_hits) + 0.1 * marker_hits)
        return BiliTranscriptMatch(
            verified=confidence >= 0.62,
            confidence=round(confidence, 3),
            reason="；".join(reasons + (["字幕正文持续讨论新番/作品"] if guide_hits else ["字幕缺少导视正文信号"])),
            source=source,
            mentions=len(guide_hits),
        )

    if not matched_terms:
        return BiliTranscriptMatch(
            confidence=0.18,
            reason="标题看似相关，但抽取到的字幕正文没有提及目标作品",
            source=source,
            mentions=0,
        )

    compilation = any(word in title for word in ("合集", "盘点", "十部", "十大", "汇总", "导视"))
    if compilation and mentions <= 1:
        return BiliTranscriptMatch(
            confidence=0.46,
            reason="字幕只顺带提到目标作品，视频主体更像合集/盘点",
            source=source,
            mentions=mentions,
        )

    coverage = mentions / max(len(text_rows), 1)
    confidence = 0.72 + min(0.16, mentions * 0.04) + min(0.08, coverage * 0.8)
    if any(term in title_key for term in matched_terms):
        confidence += 0.04
    missing_markers = [
        label for key, label in (("numbered", "季度/续作编号"), ("edition", "版本"))
        if query_markers[key] and not transcript_markers[key]
    ]
    if missing_markers:
        confidence = min(confidence, 0.64)
        reasons.append("字幕未确认" + "、".join(missing_markers))
    reasons.insert(0, f"字幕正文命中目标作品 {mentions} 个片段")
    return BiliTranscriptMatch(
        verified=confidence >= 0.62,
        confidence=round(min(confidence, 0.96), 3),
        reason="；".join(reasons),
        source=source,
        mentions=mentions,
    )


def _hit_relevance(raw: dict, *, up_name: str, aliases: list[str], tags: list[str], season_query: str = "") -> tuple[float, str]:
    title = _clean_bili_title(raw.get("title") or "")
    author = raw.get("author") or ""
    title_key = _norm_video_text(title)
    alias_keys = [_norm_video_text(x) for x in aliases if _norm_video_text(x)]
    score = 0.0
    reasons: list[str] = []
    if author == up_name:
        score += 0.08
        reasons.append("白名单 UP 信任加分")
    exact_aliases = [
        key for key in alias_keys
        if key and key in title_key and len(key) >= 3
    ]
    if exact_aliases:
        score += 0.58
        reasons.append("标题明确命中作品/查询别名")
    terms = _content_terms(" ".join([*aliases, season_query]))
    term_hits = [term for term in terms if _norm_video_text(term) in title_key]
    if term_hits:
        coverage = len(term_hits) / max(len(terms), 1)
        score += min(0.48, 0.18 + 0.3 * coverage)
        reasons.append("标题关键词覆盖：" + "、".join(term_hits[:3]))
    quoted_work_keys = [
        _norm_video_text(value)
        for value in re.findall(r"[《「『]([^》」』]{2,60})[》」』]", title)
        if _norm_video_text(value)
    ]
    alias_is_quoted_subject = any(
        alias in quoted or quoted in alias
        for alias in alias_keys
        for quoted in quoted_work_keys
    )
    comparison_alias = bool(exact_aliases) and (
        (bool(quoted_work_keys) and not alias_is_quoted_subject)
        or any(
            f"{marker}{alias}" in title_key
            for marker in ("军队版", "軍隊版", "酷似", "类似", "類似", "堪比", "媲美", "对标", "對標", "号称", "號稱", "被称为", "被稱為")
            for alias in exact_aliases
        )
    )
    if comparison_alias:
        reasons.append("目标作品名只出现在类比/副标题位置，标题主体是其他作品")
    query_markers = _season_markers(" ".join([season_query, *aliases]))
    title_markers = _season_markers(title)
    marker_conflict = False
    for key, label, bonus, penalty in (
        ("year", "年份", 0.22, 0.5),
        ("month", "月份", 0.16, 0.36),
        ("numbered", "季度/续作编号", 0.14, 0.5),
        ("edition", "版本", 0.1, 0.35),
    ):
        expected = query_markers[key]
        actual = title_markers[key]
        if expected and actual == expected:
            score += bonus
            reasons.append(f"{label}精确匹配")
        elif expected and actual and actual != expected:
            score -= penalty
            marker_conflict = True
            reasons.append(f"{label}冲突：需要 {expected}，候选是 {actual}")
    guide_hits = [k for k in ("新番", "导视", "推荐", "评价", "杂谈", "百合", "芳文", "kirara", "きらら") if k.lower() in title.lower()]
    if guide_hits:
        score += min(0.18, 0.06 * len(guide_hits))
        reasons.append("标题命中导视/圈层词：" + "、".join(guide_hits[:3]))
    tag_hits = [t for t in tags if t and t.lower() in title.lower()]
    if tag_hits:
        score += min(0.12, 0.04 * len(tag_hits))
        reasons.append("标题命中标签：" + "、".join(tag_hits[:3]))
    pubdate = int(raw.get("pubdate") or 0)
    if query_markers["year"] and pubdate:
        published_year = datetime.fromtimestamp(pubdate, timezone.utc).year
        target_year = int(query_markers["year"])
        if published_year < target_year - 1:
            score -= 0.28
            reasons.append(f"发布时间偏旧（{published_year}）")
    broad_season_query = bool(query_markers["year"] or query_markers["month"]) and any(
        marker in season_query for marker in ("新番", "导视", "季度", "盘点")
    )
    explicit_guide_title = any(
        marker in title.lower()
        for marker in ("新番", "导视", "推荐", "盘点", "季度", "春番", "夏番", "秋番", "冬番")
    ) or bool(re.search(r"\d{1,2}\s*月番", title))
    if broad_season_query and not explicit_guide_title:
        score = min(score, 0.49)
        reasons.append("标题只有月份/圈层信号，未明确表明是新番导视")
    has_content_match = bool(exact_aliases or term_hits or any(query_markers.values()))
    if not has_content_match:
        score = min(score, 0.28)
        reasons.append("只有作者/泛导视词命中，不能证明是目标视频")
    if marker_conflict:
        score = min(score, 0.34)
    if comparison_alias:
        score = min(score, 0.34)
    return max(0.0, min(score, 1.0)), "；".join(reasons) or "弱相关搜索结果"


async def verify_guide_video_links(
    query: str,
    links: list[GuideVideoLink],
    *,
    title_aliases: list[str] | None = None,
    tags: list[str] | None = None,
    max_links: int = 2,
    max_hits_per_link: int = 1,
    min_confidence: float = 0.55,
    verify_content: bool = False,
    content_verify_limit: int = 3,
) -> list[GuideVideoLink]:
    """对路由出的白名单 UP 做真实 B站搜索验证。

    命中失败不删除导航入口，只把 verified=false 和 verification_note 暴露给前端，避免把"适合这个圈层"
    误说成"这个 UP 已经讲过这部作品"。
    """
    tags = tags or []
    aliases = [x for x in (title_aliases or []) if x]
    verified_links = [link.model_copy(deep=True) for link in links]

    async def verify_link(link: GuideVideoLink) -> None:
        vertical_terms = [v.label for v in link.verticals[:2]]
        search_query = " ".join(dict.fromkeys([*(aliases[:1] or [query]), link.up_name, *(vertical_terms or tags[:1])])).strip()
        link.verification_query = search_query
        try:
            data = await _bili_search_async(search_query)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 412:
                link.publication_status = "unavailable"
                link.verification_note = f"B站搜索验证失败：HTTP {e.response.status_code}"
                return
            try:
                data = await asyncio.to_thread(_sync_bili_search, search_query)
            except (httpx.HTTPError, httpx.TransportError, ValueError) as fallback_e:
                link.publication_status = "unavailable"
                link.verification_note = f"B站搜索验证失败：HTTP 412 / fallback {type(fallback_e).__name__}"
                return
        except (httpx.HTTPError, httpx.TransportError, ValueError) as e:
            link.publication_status = "unavailable"
            link.verification_note = f"B站搜索验证失败：{type(e).__name__}"
            return

        candidates: list[tuple[float, GuideVideoHit]] = []
        for raw in ((data.get("data") or {}).get("result") or []):
            author = raw.get("author") or ""
            if author != link.up_name:
                continue
            url = raw.get("arcurl") or (f"https://www.bilibili.com/video/{raw.get('bvid')}" if raw.get("bvid") else "")
            if not url:
                continue
            conf, reason = _hit_relevance(raw, up_name=link.up_name, aliases=aliases, tags=tags, season_query=query)
            content_required = conf < min_confidence
            if content_required and not (verify_content and conf >= max(0.4, min_confidence - 0.1)):
                continue
            content_type, content_type_reason = classify_season_video(
                raw.get("title") or "", raw.get("pubdate"), query,
            )
            candidates.append((
                conf,
                GuideVideoHit(
                    title=_clean_bili_title(raw.get("title") or ""),
                    url=url.replace("http://", "https://"),
                    aid=raw.get("aid") or raw.get("id"),
                    bvid=raw.get("bvid"),
                    mid=raw.get("mid"),
                    author=author,
                    thumbnail_url=_clean_bili_image(raw.get("pic")),
                    play=raw.get("play"),
                    danmaku=raw.get("video_review"),
                    pubdate=raw.get("pubdate"),
                    content_type=content_type,
                    content_type_reason=content_type_reason,
                    match_confidence=round(conf, 3),
                    match_reason=reason,
                    content_required=content_required,
                ),
            ))
        candidates.sort(key=lambda x: -x[0])

        async def verify_view(row: tuple[float, GuideVideoHit]) -> tuple[float, GuideVideoHit] | None:
            _score, hit = row
            try:
                payload = await asyncio.to_thread(_sync_bili_view, hit.aid, hit.bvid)
            except (httpx.HTTPError, httpx.TransportError, ValueError):
                return None
            detail = payload.get("data") or {}
            if not detail:
                return None
            author = str((detail.get("owner") or {}).get("name") or hit.author)
            if author != link.up_name:
                return None
            raw = {
                "title": detail.get("title") or hit.title,
                "author": author,
                "pubdate": detail.get("pubdate") or hit.pubdate,
            }
            score, reason = _hit_relevance(
                raw, up_name=link.up_name, aliases=aliases, tags=tags, season_query=query,
            )
            if score < min_confidence and not (verify_content and score >= max(0.4, min_confidence - 0.1)):
                return None
            hit.title = _clean_bili_title(str(raw["title"]))
            hit.author = author
            hit.aid = detail.get("aid") or hit.aid
            hit.bvid = detail.get("bvid") or hit.bvid
            hit.mid = (detail.get("owner") or {}).get("mid") or hit.mid
            hit.thumbnail_url = _clean_bili_image(detail.get("pic")) or hit.thumbnail_url
            hit.pubdate = raw["pubdate"]
            hit.play = (detail.get("stat") or {}).get("view") or hit.play
            hit.danmaku = (detail.get("stat") or {}).get("danmaku") or hit.danmaku
            hit.match_confidence = round(score, 3)
            hit.match_reason = reason
            hit.content_required = score < min_confidence
            hit.verification_status = "view_verified"
            hit.content_type, hit.content_type_reason = classify_season_video(hit.title, hit.pubdate, query)
            return score, hit

        finalist_rows = candidates[: max(3, max_hits_per_link * 2)]
        verified_views = await asyncio.gather(*(verify_view(row) for row in finalist_rows)) if finalist_rows else []
        view_candidates = [row for row in verified_views if row is not None]
        view_candidates.sort(key=lambda x: (-x[0], -(x[1].pubdate or 0)))
        link.verified_hits = [x[1] for x in view_candidates[:max_hits_per_link]]
        link.verified = bool(link.verified_hits)
        link.publication_status = "published" if link.verified else ("unavailable" if candidates else "not_found")
        link.verification_note = (
            f"已通过视频详情核验 {len(link.verified_hits)} 个白名单相关视频。"
            if link.verified else
            "发现搜索候选，但视频详情本轮不可核验。"
            if candidates else
            "尚未发现该 UP 已发布本次查询对应的视频。"
        )

    await gather_limited(
        (verify_link(link) for link in verified_links[:max_links]),
        host="bilibili",
    )

    if verify_content and content_verify_limit > 0:
        targets = [
            (link, hit)
            for link in verified_links[:max_links]
            for hit in link.verified_hits[:max_hits_per_link]
        ][:content_verify_limit]

        async def check_content(hit: GuideVideoHit, *, allow_asr: bool) -> BiliTranscriptMatch | None:
            result = await GetBiliVideoSubtitlesTool().run(BiliVideoSubtitleArgs(
                aid=hit.aid,
                bvid=hit.bvid,
                max_segments=160,
                allow_asr=allow_asr,
                sample_across_video=True,
            ))
            if not result.ok or result.data is None or not result.data.segments:
                return None
            source: Literal["subtitle", "asr"] = (
                "asr" if result.data.source == "bili_asr" else "subtitle"
            )
            return _match_video_transcript(query, hit.title, result.data.segments, source=source)

        public_matches = await asyncio.gather(*(
            check_content(hit, allow_asr=False) for _link, hit in targets
        )) if targets else []
        resolved: dict[str, BiliTranscriptMatch] = {}
        for (_link, hit), match in zip(targets, public_matches, strict=False):
            if match is not None:
                resolved[hit.bvid or str(hit.aid or hit.url)] = match
        if settings.asr_provider != "off":
            asr_target = next((
                (link, hit) for link, hit in targets
                if (hit.bvid or str(hit.aid or hit.url)) not in resolved
                and 0.58 <= hit.match_confidence <= 0.82
            ), None)
            if asr_target is not None:
                _link, hit = asr_target
                match = await check_content(hit, allow_asr=True)
                if match is not None:
                    resolved[hit.bvid or str(hit.aid or hit.url)] = match

        processed_links: set[int] = set()
        for link, _hit in targets:
            link_key = id(link)
            if link_key in processed_links:
                continue
            processed_links.add(link_key)
            kept: list[GuideVideoHit] = []
            rejected = False
            for hit in link.verified_hits:
                match = resolved.get(hit.bvid or str(hit.aid or hit.url))
                if match is None:
                    if hit.content_required:
                        rejected = True
                        continue
                    hit.verification_status = "view_verified"
                    hit.content_match_reason = "未获得公开字幕；仅确认视频已发布且元数据匹配"
                    kept.append(hit)
                    continue
                hit.content_verified = match.verified
                hit.content_match_confidence = match.confidence
                hit.content_match_reason = match.reason
                hit.transcript_source = match.source
                hit.content_mentions = match.mentions
                hit.verification_status = "content_verified" if match.verified else "view_verified"
                hit.match_confidence = round(0.45 * hit.match_confidence + 0.55 * match.confidence, 3)
                hit.match_reason = f"{hit.match_reason}；内容核验：{match.reason}"
                if match.confidence < 0.52 or (hit.content_required and not match.verified):
                    rejected = True
                    continue
                kept.append(hit)
            link.verified_hits = kept
            link.verified = bool(kept)
            if kept:
                link.publication_status = "published"
                content_count = sum(1 for hit in kept if hit.content_verified)
                link.verification_note = (
                    f"已发布；其中 {content_count} 个视频通过字幕正文核验。"
                    if content_count else
                    "已确认发布，暂未获得可用字幕正文，结论仅依据视频元数据。"
                )
            elif rejected:
                link.publication_status = "rejected"
                link.verification_note = "发现了边界候选，但标题证据不足，且字幕正文未能通过本季内容核验。"
    return verified_links


def _whitelist_by_name() -> dict[str, dict]:
    return {u["name"]: u for u in _GUIDE_UPS}


def _bili_cookie_header() -> str:
    """Load only bilibili.com cookies from the server-side Netscape jar.

    The raw value must never be returned by a tool or logged.  Reading per
    request makes admin replacement/clear take effect without a process restart.
    """
    path = Path(settings.bilibili_cookies_file)
    if not path.is_file():
        return ""
    try:
        jar = MozillaCookieJar(str(path))
        jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, ValueError, LoadError):
        return ""
    values = [
        f"{cookie.name}={cookie.value}"
        for cookie in jar
        if cookie.value and str(cookie.domain or "").lower().endswith("bilibili.com")
    ]
    return "; ".join(values)


def bilibili_account_mode() -> Literal["public", "cookie"]:
    return "cookie" if _bili_cookie_header() else "public"


def _bili_headers() -> dict[str, str]:
    headers = {"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"}
    if cookie := _bili_cookie_header():
        headers["Cookie"] = cookie
    return headers


def verify_bilibili_account() -> dict[str, object]:
    """Verify imported login state without exposing cookie material."""
    configured = bool(_bili_cookie_header())
    if not configured:
        return {"configured": False, "authenticated": False, "username": "", "user_id": 0}
    try:
        response = httpx.get(_BILI_NAV_API, headers=_bili_headers(), timeout=settings.http_timeout)
        response.raise_for_status()
        payload = _bili_json(response.json())
        data = payload.get("data") or {}
        authenticated = bool(data.get("isLogin"))
        return {
            "configured": True,
            "authenticated": authenticated,
            "username": str(data.get("uname") or "") if authenticated else "",
            "user_id": int(data.get("mid") or 0) if authenticated else 0,
        }
    except Exception as exc:  # noqa: BLE001 - status endpoint should explain, not crash admin
        return {
            "configured": True,
            "authenticated": False,
            "username": "",
            "user_id": 0,
            "error": f"{type(exc).__name__}: 登录态不可用或已过期",
        }


def _bili_json(data: dict) -> dict:
    """B站把风控/错误放在 200 响应体的 code 字段（-412 风控 / -404 等），HTTP 状态仍是 200。

    code!=0 时抛 ValueError，让上层的 except 统一按"抓取失败"降级，而不是静默返回空列表
    （否则 agent 会误以为"没有导视视频/没有评论"）。
    """
    code = data.get("code", 0)
    if code not in (0, None):
        raise ValueError(f"bilibili code={code}: {data.get('message') or ''}")
    return data


_BILI_PERSISTENT_CACHES: dict[tuple[str, str], PersistentJsonCache] = {}


def _bili_cache(namespace: str) -> PersistentJsonCache:
    key = (settings.bilibili_cache_path, namespace)
    cache = _BILI_PERSISTENT_CACHES.get(key)
    if cache is None:
        cache = PersistentJsonCache(settings.bilibili_cache_path, namespace)
        _BILI_PERSISTENT_CACHES[key] = cache
    return cache


def _cache_payload(payload: dict, created_at: float, *, hit: bool, stale: bool = False, rate_limited: bool = False) -> dict:
    return {
        **payload,
        "_otomo_cache": {
            "hit": hit,
            "stale": stale,
            "rate_limited": rate_limited,
            "verified_at": datetime.fromtimestamp(created_at, timezone.utc).isoformat(),
        },
    }


def _is_rate_limited(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("-412", "429", "rate limit", "too many requests"))


def _bili_cache_key(value: str) -> str:
    return f"{bilibili_account_mode()}:{value}"


def _sync_bili_search(query: str) -> dict:
    cache = _bili_cache("search")
    key = _bili_cache_key(query)
    if hit := cache.get(key, ttl=settings.bilibili_search_cache_ttl):
        return _cache_payload(hit[0], hit[1], hit=True)
    try:
        r = httpx.get(
            _BILI_SEARCH_API,
            params={"search_type": "video", "keyword": query, "page": 1},
            headers=_bili_headers(),
            timeout=settings.http_timeout,
        )
        r.raise_for_status()
        payload = _bili_json(r.json())
    except (httpx.HTTPError, httpx.TransportError, ValueError) as exc:
        stale = cache.get(key, ttl=settings.bilibili_stale_cache_ttl)
        if stale:
            return _cache_payload(stale[0], stale[1], hit=True, stale=True, rate_limited=_is_rate_limited(exc))
        raise
    created_at = cache.set(key, payload)
    return _cache_payload(payload, created_at, hit=False)


@scached()
def _sync_bili_replies(aid: int, limit: int) -> dict:
    r = httpx.get(
        _BILI_REPLY_API,
        params={"type": 1, "oid": aid, "sort": 1, "pn": 1, "ps": min(limit, 50)},
        headers=_bili_headers(),
        timeout=settings.http_timeout,
    )
    r.raise_for_status()
    return _bili_json(r.json())


async def _bili_search_async(q: str) -> dict:
    cache = _bili_cache("search")
    key = _bili_cache_key(q)
    if hit := cache.get(key, ttl=settings.bilibili_search_cache_ttl):
        return _cache_payload(hit[0], hit[1], hit=True)
    try:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout,
            headers=_bili_headers(),
        ) as c:
            r = await c.get(_BILI_SEARCH_API, params={"search_type": "video", "keyword": q, "page": 1})
            r.raise_for_status()
            payload = _bili_json(r.json())
    except (httpx.HTTPError, httpx.TransportError, ValueError) as exc:
        stale = cache.get(key, ttl=settings.bilibili_stale_cache_ttl)
        if stale:
            return _cache_payload(stale[0], stale[1], hit=True, stale=True, rate_limited=_is_rate_limited(exc))
        raise
    created_at = cache.set(key, payload)
    return _cache_payload(payload, created_at, hit=False)


def _summarize_aspect_opinions(opinions: list[AspectOpinion]) -> list[str]:
    return _format_aspect_summary(_build_aspect_summary(opinions))


def _sync_bili_view(aid: int | None, bvid: str | None) -> dict:
    key = _bili_cache_key(str(bvid or f"av{aid or 0}"))
    cache = _bili_cache("view")
    if hit := cache.get(key, ttl=settings.bilibili_view_cache_ttl):
        return _cache_payload(hit[0], hit[1], hit=True)
    params = {"aid": aid} if aid else {"bvid": bvid}
    try:
        r = httpx.get(
            _BILI_VIEW_API,
            params=params,
            headers=_bili_headers(),
            timeout=settings.http_timeout,
        )
        r.raise_for_status()
        payload = _bili_json(r.json())
    except (httpx.HTTPError, httpx.TransportError, ValueError) as exc:
        stale = cache.get(key, ttl=settings.bilibili_stale_cache_ttl)
        if stale:
            return _cache_payload(stale[0], stale[1], hit=True, stale=True, rate_limited=_is_rate_limited(exc))
        raise
    created_at = cache.set(key, payload)
    return _cache_payload(payload, created_at, hit=False)


@scached()
def _sync_bili_pagelist(aid: int | None, bvid: str | None) -> dict:
    params = {"aid": aid} if aid else {"bvid": bvid}
    r = httpx.get(
        _BILI_PAGELIST_API,
        params=params,
        headers=_bili_headers(),
        timeout=settings.http_timeout,
    )
    r.raise_for_status()
    return _bili_json(r.json())


@scached()
def _sync_bili_player(aid: int | None, bvid: str | None, cid: int) -> dict:
    params = {"cid": cid}
    if aid:
        params["aid"] = aid
    if bvid:
        params["bvid"] = bvid
    r = httpx.get(
        _BILI_PLAYER_API,
        params=params,
        headers=_bili_headers(),
        timeout=settings.http_timeout,
    )
    r.raise_for_status()
    return _bili_json(r.json())


@scached()
def _sync_subtitle_json(url: str) -> dict:
    full = "https:" + url if url.startswith("//") else url
    r = httpx.get(
        full,
        headers=_bili_headers(),
        timeout=settings.http_timeout,
    )
    r.raise_for_status()
    return r.json()


@scached()
def _sync_bili_danmaku_xml(cid: int) -> str:
    r = httpx.get(
        _BILI_DANMAKU_API.format(cid=cid),
        headers=_bili_headers(),
        timeout=settings.http_timeout,
    )
    r.raise_for_status()
    return r.text


def _rough_subtitle_summary(segments: list[BiliSubtitleSegment]) -> list[str]:
    texts = [s.text for s in segments if s.text.strip()]
    if not texts:
        return []
    total = len(texts)
    picks = [0, total // 3, (total * 2) // 3]
    out = []
    for idx in picks:
        window = " ".join(texts[idx : min(idx + 4, total)])
        window = re.sub(r"\s+", " ", window).strip()
        if window and window not in out:
            out.append(window[:180])
    return out


def _parse_danmaku(xml_text: str, limit: int = 120) -> list[BiliDanmakuItem]:
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return []
    items: list[BiliDanmakuItem] = []
    for elem in root.findall("d"):
        text_value = (elem.text or "").strip()
        if not text_value:
            continue
        p = elem.attrib.get("p") or ""
        start = None
        if p:
            try:
                start = float(p.split(",", 1)[0])
            except ValueError:
                start = None
        items.append(BiliDanmakuItem(time=start, text=text_value[:160]))
        if len(items) >= limit:
            break
    return items


def _rough_danmaku_summary(items: list[BiliDanmakuItem]) -> list[str]:
    texts = [x.text for x in items if x.text.strip()]
    if not texts:
        return []
    # 高频短语通常能反映弹幕氛围；保留去重后的代表句。
    uniq: list[str] = []
    for text_value in texts:
        norm = re.sub(r"\s+", "", text_value)
        if len(norm) < 2:
            continue
        if norm not in {re.sub(r"\s+", "", x) for x in uniq}:
            uniq.append(text_value)
        if len(uniq) >= 8:
            break
    return uniq[:6]


def _video_url(aid: int | None, bvid: str | None) -> str:
    if bvid:
        return f"https://www.bilibili.com/video/{bvid}"
    if aid:
        return f"https://www.bilibili.com/video/av{aid}"
    return "https://www.bilibili.com/"


def _parse_bili_video_ref(value: str | None) -> tuple[int | None, str | None]:
    text = str(value or "").strip()
    if not text:
        return None, None
    bvid_match = re.search(r"\b(BV[0-9A-Za-z]{10,})\b", text)
    if bvid_match:
        return None, bvid_match.group(1)
    av_match = re.search(r"(?:/video/)?av(\d+)\b", text, re.I)
    if av_match:
        return int(av_match.group(1)), None
    aid_match = re.search(r"[?&]aid=(\d+)\b", text)
    if aid_match:
        return int(aid_match.group(1)), None
    return None, None


@scached()
def _sync_resolve_bili_url(url: str) -> str:
    """Resolve b23.tv/share links without downloading video content."""
    r = httpx.get(
        url,
        headers=_bili_headers(),
        timeout=settings.http_timeout,
        follow_redirects=True,
    )
    r.raise_for_status()
    return str(r.url)


async def _resolve_video_ref(url: str | None, aid: int | None, bvid: str | None) -> tuple[int | None, str | None, list[str]]:
    notes: list[str] = []
    if url:
        parsed_aid, parsed_bvid = _parse_bili_video_ref(url)
        aid = aid or parsed_aid
        bvid = bvid or parsed_bvid
        if aid is None and not bvid and "b23.tv" in url:
            try:
                resolved = await asyncio.to_thread(_sync_resolve_bili_url, url)
                parsed_aid, parsed_bvid = _parse_bili_video_ref(resolved)
                aid = aid or parsed_aid
                bvid = bvid or parsed_bvid
            except Exception as e:  # noqa: BLE001
                notes.append(f"B站短链解析失败：{type(e).__name__}")
    return aid, bvid, notes


@lru_cache(maxsize=2)
def _whisper_model(model_name: str, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:  # pragma: no cover - optional dependency
        raise RuntimeError("未安装 faster-whisper；请执行 pip install -e \".[asr]\"") from e
    # 项目惯例（同 _rag._resolve_model）：模型走 modelscope 下到 otomo/models/ 本地优先，
    # HF 直连在国内网络常失败。例：
    #   modelscope download --model gpustack/faster-whisper-small --local_dir otomo/models/faster-whisper-small
    from .._rag import _LOCAL_MODELS  # 单一事实源，避免再算错目录层级

    local = _LOCAL_MODELS / f"faster-whisper-{model_name}"
    return WhisperModel(str(local) if local.is_dir() else model_name, device=device, compute_type=compute_type)


# 单发闸门：whisper CPU 推理单跑就吃满核，并发只会互相拖慢并把内存翻倍。
# threading 信号量在 to_thread 的 worker 线程里阻塞等待，不占 event loop，也无跨 loop 问题。
_ASR_GATE = threading.BoundedSemaphore(1)


def _sync_local_bili_asr(source_url: str, max_segments: int) -> list[BiliSubtitleSegment]:
    """Download public Bilibili audio to a temp dir and transcribe it locally."""
    try:
        import yt_dlp
    except ImportError as e:  # pragma: no cover - optional dependency
        raise RuntimeError("未安装 yt-dlp；请执行 pip install -e \".[asr]\"") from e

    with _ASR_GATE, tempfile.TemporaryDirectory(prefix="otomo_bili_asr_") as tmp:
        tmp_path = Path(tmp)
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(tmp_path / "audio.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": settings.http_timeout,
            "http_headers": {
                "User-Agent": _BROWSER_UA,
                "Referer": "https://www.bilibili.com/",
            },
        }
        if settings.asr_cookies_from_browser:
            ydl_opts["cookiesfrombrowser"] = (settings.asr_cookies_from_browser.strip().lower(),)
        elif settings.asr_cookies_file:
            ydl_opts["cookiefile"] = settings.asr_cookies_file
        elif _bili_cookie_header():
            ydl_opts["cookiefile"] = settings.bilibili_cookies_file
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(source_url, download=False)
            duration = float(info.get("duration") or 0)
            if duration and duration > settings.asr_max_video_seconds:
                raise RuntimeError(
                    f"视频时长 {duration:.0f}s 超过 ASR_MAX_VIDEO_SECONDS={settings.asr_max_video_seconds}"
                )
            info = ydl.extract_info(source_url, download=True)
            filename = Path(ydl.prepare_filename(info))
        if not filename.exists():
            files = [p for p in tmp_path.glob("audio.*") if p.is_file()]
            if not files:
                raise RuntimeError("yt-dlp 未产出音频文件")
            filename = files[0]
        model = _whisper_model(settings.asr_model, settings.asr_device, settings.asr_compute_type)
        segments_iter, _info = model.transcribe(
            str(filename),
            language=settings.asr_language or None,
            vad_filter=True,
        )
        segments: list[BiliSubtitleSegment] = []
        for seg in segments_iter:
            text_value = str(getattr(seg, "text", "") or "").strip()
            if text_value:
                segments.append(
                    BiliSubtitleSegment(
                        start=float(getattr(seg, "start", 0.0) or 0.0),
                        end=float(getattr(seg, "end", 0.0) or 0.0),
                        text=text_value[:220],
                    )
                )
            if len(segments) >= max_segments:
                break
        return segments


@acached(ttl=settings.asr_cache_ttl)
async def _local_bili_asr(source_url: str, max_segments: int) -> list[BiliSubtitleSegment]:
    return await asyncio.to_thread(_sync_local_bili_asr, source_url, max_segments)


@acached(ttl=settings.asr_cache_ttl)
async def _worker_bili_asr(source_url: str, max_segments: int) -> list[BiliSubtitleSegment]:
    headers = {
        "Authorization": f"Bearer {settings.asr_worker_token}"
    } if settings.asr_worker_token else {}
    async with httpx.AsyncClient(timeout=settings.asr_worker_timeout) as client:
        response = await client.post(
            f"{settings.asr_worker_url.rstrip('/')}/transcribe",
            json={"url": source_url, "max_segments": max_segments},
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "ASR worker 返回失败"))
    return [BiliSubtitleSegment.model_validate(item) for item in payload.get("segments") or []]


async def _maybe_asr_segments(source_url: str, max_segments: int) -> tuple[list[BiliSubtitleSegment], list[str], str | None]:
    provider = (settings.asr_provider or "off").strip().lower()
    if provider in {"", "off", "none", "false"}:
        return [], ["ASR_PROVIDER=off，未启用本地转写。"], None
    if provider not in {"local", "worker"}:
        return [], [f"ASR_PROVIDER={settings.asr_provider} 暂未接入；当前支持 local/worker。"], None
    try:
        segments = (
            await _worker_bili_asr(source_url, max_segments)
            if provider == "worker" else
            await _local_bili_asr(source_url, max_segments)
        )
    except Exception as e:  # noqa: BLE001
        hint = "（B站 412 风控：导出浏览器 cookies.txt 并配置 ASR_COOKIES_FILE 可解除）" if "412" in str(e) else ""
        return [], [f"本地 ASR 转写失败：{type(e).__name__}: {e}{hint}"], str(e)
    caveats = [
        f"{'独立 ASR 服务' if provider == 'worker' else '本地 ASR'}由 faster-whisper 识别公开视频音频，可能漏字、错字或错分段。",
        "B站 ASR 是视频话语源，不是 canonical 事实源；事实需回到 Bangumi/yuc 等源核验。",
    ]
    return segments, caveats, None


class FindVideosTool(Tool):
    name = "find_related_videos"
    description = (
        "给一个作品/角色/话题，返回 B站搜索外链（综合 / 解析考据 / 二创MAD），作为'延伸观看'推荐。"
        "仅外链不抓取。用户想看视频/解析/二创时用。"
    )
    args_model = VideosArgs
    result_model = VideosResult

    async def run(self, args: VideosArgs) -> ToolResult[VideosResult]:
        q = args.query.strip()
        links = [
            VideoLink(label=f"{q} · 综合", url=_bili(q)),
            VideoLink(label=f"{q} · 解析/考据", url=_bili(f"{q} 解析 考据")),
            VideoLink(label=f"{q} · 台长", url=_bili(f"{q} 台长")),
            VideoLink(label=f"{q} · 二创/MAD", url=_bili(f"{q} MAD")),
        ]
        return ToolResult(
            ok=True,
            data=VideosResult(query=q, links=links),
            sources=[Citation(title=link.label, url=link.url, source="bilibili") for link in links],
        )


class FindGuideVideosTool(Tool):
    name = "find_guide_videos"
    description = (
        "按白名单 UP 返回 B站导视/漫评搜索入口。用于新番导视、季度推荐、某作品评价延伸。"
        "仅 link-out，不抓取视频内容或评论。intent 可选 season/review/yuri/kirara/data/all。"
    )
    args_model = GuideVideosArgs
    result_model = GuideVideosResult

    async def run(self, args: GuideVideosArgs) -> ToolResult[GuideVideosResult]:
        q = args.query.strip()
        links = _guide_links(q, args.intent, args.limit, args.tags)
        return ToolResult(
            ok=True,
            data=GuideVideosResult(query=q, intent=args.intent, links=links),
            sources=[Citation(title=link.label, url=link.url, source="bilibili") for link in links],
        )


class SearchBiliGuideVideosTool(Tool):
    name = "search_bilibili_guide_videos"
    description = (
        "搜索 B站导视/漫评视频元数据，校验作品名、季度/续作和视频详情后返回高置信 BV 链接。"
        "白名单 UP 只提供小幅信任加分；标题精确的非白名单漫评也可进入。"
    )
    args_model = BiliGuideSearchArgs
    result_model = BiliGuideSearchResult

    async def run(self, args: BiliGuideSearchArgs) -> ToolResult[BiliGuideSearchResult]:
        base_query = args.query.strip()
        q = " ".join([base_query] + (args.tags or [])).strip()
        whitelist = _whitelist_by_name()
        candidates: list[tuple[float, str, dict, bool]] = []
        seen: set[str] = set()
        warnings: list[str] = []

        async def search_variant(query: str) -> dict | None:
            try:
                return await _bili_search_async(query)
            except (httpx.HTTPError, httpx.TransportError, ValueError):
                try:
                    return await asyncio.to_thread(_sync_bili_search, query)
                except (httpx.HTTPError, httpx.TransportError, ValueError) as exc:
                    warnings.append(f"搜索变体《{query}》不可用：{type(exc).__name__}")
                    return None

        variants = list(dict.fromkeys([
            q,
            f"{base_query} 漫评",
            f"{base_query} 新番导视" if any(key in base_query for key in ("年", "月", "季度", "新番")) else f"{base_query} 评价",
        ]))[:3]
        payloads = await asyncio.gather(*(search_variant(query) for query in variants))

        def add_from(data_obj: dict) -> None:
            for raw in ((data_obj.get("data") or {}).get("result") or []):
                author = raw.get("author") or ""
                matched = author in whitelist
                if args.whitelist_only and not matched:
                    continue
                url = raw.get("arcurl") or (f"https://www.bilibili.com/video/{raw.get('bvid')}" if raw.get("bvid") else "")
                if not url:
                    continue
                url = url.replace("http://", "https://")
                key = raw.get("bvid") or url
                if key in seen:
                    continue
                seen.add(key)
                confidence, reason = _hit_relevance(
                    raw,
                    up_name=author if matched else "",
                    aliases=[base_query],
                    tags=args.tags or [],
                    season_query=base_query,
                )
                candidates.append((confidence, reason, raw, matched))

        for payload in payloads:
            if payload:
                add_from(payload)
        candidates.sort(key=lambda item: (-item[0], -int(item[2].get("pubdate") or 0)))

        async def verify(candidate: tuple[float, str, dict, bool]):
            _confidence, _reason, raw, _matched = candidate
            try:
                payload = await asyncio.to_thread(
                    _sync_bili_view, raw.get("aid") or raw.get("id"), raw.get("bvid"),
                )
                detail = payload.get("data") or {}
                if not detail:
                    return candidate, None
                normalized = {
                    **raw,
                    "title": detail.get("title") or raw.get("title"),
                    "author": (detail.get("owner") or {}).get("name") or raw.get("author"),
                    "mid": (detail.get("owner") or {}).get("mid") or raw.get("mid"),
                    "aid": detail.get("aid") or raw.get("aid") or raw.get("id"),
                    "bvid": detail.get("bvid") or raw.get("bvid"),
                    "pic": detail.get("pic") or raw.get("pic"),
                    "pubdate": detail.get("pubdate") or raw.get("pubdate"),
                    "play": (detail.get("stat") or {}).get("view") or raw.get("play"),
                    "video_review": (detail.get("stat") or {}).get("danmaku") or raw.get("video_review"),
                }
                author = normalized.get("author") or ""
                matched = author in whitelist
                confidence, reason = _hit_relevance(
                    normalized, up_name=author if matched else "", aliases=[base_query],
                    tags=args.tags or [], season_query=base_query,
                )
                return (confidence, reason, normalized, matched), detail
            except (httpx.HTTPError, httpx.TransportError, ValueError):
                return candidate, None

        verified_rows = await asyncio.gather(*(
            verify(candidate) for candidate in candidates[: min(max(args.limit * 2, 8), 16)]
        ))
        content_matches: dict[str, BiliTranscriptMatch] = {}

        def candidate_key(row: tuple[tuple[float, str, dict, bool], dict | None]) -> str:
            raw = row[0][2]
            return str(raw.get("bvid") or raw.get("aid") or raw.get("id") or "")

        async def transcript_check(
            row: tuple[tuple[float, str, dict, bool], dict | None],
            *,
            allow_asr: bool,
        ) -> BiliTranscriptMatch | None:
            (confidence, _reason, raw, _matched), detail = row
            if confidence < 0.58 or not detail:
                return None
            result = await GetBiliVideoSubtitlesTool().run(BiliVideoSubtitleArgs(
                aid=raw.get("aid") or raw.get("id"),
                bvid=raw.get("bvid"),
                max_segments=160,
                allow_asr=allow_asr,
                sample_across_video=True,
            ))
            if not result.ok or result.data is None or not result.data.segments:
                return None
            source: Literal["subtitle", "asr"] = (
                "asr" if result.data.source == "bili_asr" else "subtitle"
            )
            return _match_video_transcript(
                base_query,
                _clean_bili_title(raw.get("title") or ""),
                result.data.segments,
                source=source,
            )

        # Public subtitle lookup is cheap enough for a tiny finalist pool. ASR remains an
        # optional, single-candidate fallback only near the acceptance threshold.
        content_rows = [
            row for row in verified_rows
            if row[1] is not None and row[0][0] >= 0.58
        ][:3]
        if content_rows:
            public_checks = await asyncio.gather(*(
                transcript_check(row, allow_asr=False) for row in content_rows
            ))
            for row, match in zip(content_rows, public_checks, strict=False):
                if match is not None:
                    content_matches[candidate_key(row)] = match
            if settings.asr_provider != "off":
                asr_row = next((
                    row for row in content_rows
                    if candidate_key(row) not in content_matches and 0.58 <= row[0][0] <= 0.78
                ), None)
                if asr_row is not None:
                    asr_match = await transcript_check(asr_row, allow_asr=True)
                    if asr_match is not None:
                        content_matches[candidate_key(asr_row)] = asr_match

        videos: list[BiliVideoMeta] = []
        rejected: list[BiliRejectedCandidate] = []
        for (confidence, reason, raw, matched), detail in verified_rows:
            title = _clean_bili_title(raw.get("title") or "")
            author = str(raw.get("author") or "")
            if confidence < 0.58:
                rejected.append(BiliRejectedCandidate(
                    title=title, author=author,
                    match_confidence=round(confidence, 3), reason=reason,
                ))
                continue
            match = content_matches.get(str(raw.get("bvid") or raw.get("aid") or raw.get("id") or ""))
            if match is not None and match.confidence < 0.52:
                rejected.append(BiliRejectedCandidate(
                    title=title,
                    author=author,
                    match_confidence=match.confidence,
                    reason=match.reason,
                ))
                continue
            if match is not None:
                confidence = 0.45 * confidence + 0.55 * match.confidence
                reason = f"{reason}；内容核验：{match.reason}"
            url = raw.get("arcurl") or f"https://www.bilibili.com/video/{raw.get('bvid') or ('av' + str(raw.get('aid') or raw.get('id')))}"
            content_type, content_type_reason = classify_season_video(title, raw.get("pubdate"), base_query)
            videos.append(BiliVideoMeta(
                title=title, url=str(url).replace("http://", "https://"),
                aid=raw.get("aid") or raw.get("id"), bvid=raw.get("bvid"),
                author=author, mid=raw.get("mid"), thumbnail_url=_clean_bili_image(raw.get("pic")),
                play=raw.get("play"),
                danmaku=raw.get("video_review"), pubdate=raw.get("pubdate"),
                content_type=content_type, content_type_reason=content_type_reason,
                matched_whitelist=matched, match_confidence=round(confidence, 3),
                match_reason=reason, verified=bool(detail),
                verification_status=(
                    "content_verified" if match is not None and match.verified
                    else "view_verified" if detail else "search_metadata"
                ),
                content_verified=bool(match and match.verified),
                content_match_confidence=match.confidence if match else 0.0,
                content_match_reason=match.reason if match else "未获得公开字幕；保留元数据校验结论",
                transcript_source=match.source if match else "none",
                content_mentions=match.mentions if match else 0,
            ))
        videos.sort(key=lambda video: (-video.match_confidence, not video.verified, -(video.pubdate or 0)))
        videos = videos[: args.limit]
        if not videos:
            warnings.append("没有候选通过标题/季度一致性阈值；仅返回 B站搜索导航，避免给出错误直链。")
        elif any(not video.verified for video in videos):
            warnings.append("部分候选仅通过搜索元数据校验；打开后仍建议核对标题与发布时间。")
        if content_matches:
            warnings.append(
                f"已对 {len(content_matches)} 个边界候选读取字幕正文；标题党、合集顺带提及或版本冲突会被拒绝。"
            )
        elif content_rows:
            warnings.append("候选未暴露公开字幕；本轮未为此批量启动 ASR，结果仍明确标为元数据校验。")
        return ToolResult(
            ok=True,
            data=BiliGuideSearchResult(
                query=q, count=len(videos), videos=videos,
                navigation_url=_bili(q), rejected=rejected[:8], warnings=warnings,
            ),
            sources=[
                Citation(title=f"Bilibili — {v.title}", url=v.url, source="bilibili")
                for v in videos[:5]
            ],
        )


class SearchBiliSubjectVideosTool(Tool):
    name = "search_bilibili_subject_videos"
    description = (
        "搜索某一部动画的具体 B站视频并分类为普通投稿中的完整正片候选、疑似正片、PV、漫评、回顾或二创。"
        "完整正片依据作品匹配、时长、分P与标题特征判断，不要求UP主名称命中Staff；"
        "普通投稿始终与B站番剧库正版页分开，并标注版权未核验。"
    )
    args_model = BiliSubjectVideosArgs
    result_model = BiliSubjectVideosResult

    async def run(self, args: BiliSubjectVideosArgs) -> ToolResult[BiliSubjectVideosResult]:
        query = args.query.strip()
        aliases = list(dict.fromkeys([query, *(x.strip() for x in args.aliases if x.strip())]))[:8]
        warnings: list[str] = []
        version_conflicts: list[BiliVersionConflict] = []
        search_failures: list[str] = []

        if args.media_kind == "movie":
            if args.lifecycle == "upcoming":
                suffixes = ("正式 PV", "上映 前瞻", "制作情报", "无剧透")
            elif args.lifecycle_phase in {"theatrical", "awaiting_streaming", "awaiting_bd"}:
                suffixes = ("上映", "无剧透 影评", "制作解析", "流媒体", "BD")
            else:
                suffixes = ("完整版", "回顾", "制作解析", "影评")
        elif args.media_kind == "ova":
            suffixes = ("正片", "OVA", "制作解析", "回顾")
        elif args.lifecycle == "upcoming":
            suffixes = ("官方 PV", "预告", "前瞻", "漫评")
        elif args.lifecycle == "airing":
            suffixes = ("正片", "第1话", "首集 漫评", "PV")
        elif args.lifecycle == "archive":
            suffixes = ("正片", "全集", "完整版", "回顾")
        else:
            suffixes = ("正片", "官方 PV", "漫评", "回顾")
        variants = list(dict.fromkeys([query, *(f"{query} {suffix}" for suffix in suffixes)]))[:5]

        async def search_variant(value: str) -> dict | None:
            try:
                return await _bili_search_async(value)
            except (httpx.HTTPError, httpx.TransportError, ValueError):
                try:
                    return await asyncio.to_thread(_sync_bili_search, value)
                except (httpx.HTTPError, httpx.TransportError, ValueError) as exc:
                    warnings.append(f"搜索变体《{value}》不可用：{type(exc).__name__}")
                    search_failures.append(str(exc))
                    return None

        payloads = await asyncio.gather(*(search_variant(value) for value in variants))
        cache_marks = [
            payload.get("_otomo_cache") or {}
            for payload in payloads
            if isinstance(payload, dict)
        ]
        cache_hit = any(bool(mark.get("hit")) for mark in cache_marks)
        rate_limited = any(bool(mark.get("rate_limited")) for mark in cache_marks) or any(
            _is_rate_limited(ValueError(reason)) for reason in search_failures
        )
        verified_times = [str(mark.get("verified_at") or "") for mark in cache_marks if mark.get("verified_at")]
        search_partial = len([payload for payload in payloads if payload]) < len(variants)
        seen: set[str] = set()
        candidates: list[tuple[float, str, dict]] = []
        hidden_ids = {str(value).lower() for value in args.hidden_video_ids if str(value).strip()}
        muted_uploaders = {value.strip().lower() for value in args.muted_uploaders if value.strip()}
        preferred_uploaders = {value.strip().lower() for value in args.preferred_uploaders if value.strip()}
        for payload in payloads:
            if not payload:
                continue
            for raw in ((payload.get("data") or {}).get("result") or []):
                key = str(raw.get("bvid") or raw.get("aid") or raw.get("id") or raw.get("arcurl") or "")
                if not key or key in seen:
                    continue
                if key.lower() in hidden_ids or str(raw.get("author") or "").strip().lower() in muted_uploaders:
                    continue
                seen.add(key)
                confidence, reason = _hit_relevance(
                    raw,
                    up_name="",
                    aliases=aliases,
                    tags=[],
                    season_query=" ".join(aliases),
                )
                if confidence >= 0.46:
                    candidates.append((confidence, reason, raw))
        def precheck_priority(row: tuple[float, str, dict]) -> tuple[float, int]:
            confidence, _reason, raw = row
            role, _uploader, _watch, _identity, _content, _caution = classify_subject_video(
                _clean_bili_title(raw.get("title") or ""),
                str(raw.get("author") or ""),
                str(raw.get("description") or ""),
                staff_names=args.staff_names,
                match_confidence=confidence,
            )
            bonus = {
                "public_full_episode": 0.28,
                "official_pv": 0.18,
                "review": 0.15,
                "retrospective": 0.15,
                "fan_creation": 0.07,
                "episode_candidate": 0.16,
                "related": -0.3,
            }[role]
            version_ok, _version_reason = _subject_version_compatibility(
                aliases,
                _clean_bili_title(raw.get("title") or ""),
                subject_platform=args.subject_platform,
            )
            if not version_ok and role in {"public_full_episode", "episode_candidate"}:
                bonus = -0.5
            return -(confidence + bonus), -int(raw.get("pubdate") or 0)

        candidates.sort(key=precheck_priority)

        async def verify(row: tuple[float, str, dict]) -> tuple[float, str, dict, bool, dict]:
            confidence, reason, raw = row
            try:
                payload = await asyncio.to_thread(
                    _sync_bili_view, raw.get("aid") or raw.get("id"), raw.get("bvid"),
                )
                detail = payload.get("data") or {}
                if not detail:
                    return confidence, reason, raw, False, payload.get("_otomo_cache") or {}
                normalized = {
                    **raw,
                    "title": detail.get("title") or raw.get("title"),
                    "description": detail.get("desc") or raw.get("description") or "",
                    "author": (detail.get("owner") or {}).get("name") or raw.get("author"),
                    "mid": (detail.get("owner") or {}).get("mid") or raw.get("mid"),
                    "aid": detail.get("aid") or raw.get("aid") or raw.get("id"),
                    "bvid": detail.get("bvid") or raw.get("bvid"),
                    "pic": detail.get("pic") or raw.get("pic"),
                    "pubdate": detail.get("pubdate") or raw.get("pubdate"),
                    "play": (detail.get("stat") or {}).get("view") or raw.get("play"),
                    "video_review": (detail.get("stat") or {}).get("danmaku") or raw.get("video_review"),
                    "duration": detail.get("duration") or raw.get("duration"),
                    "pages": detail.get("pages") or raw.get("pages") or [],
                    "videos": detail.get("videos") or raw.get("videos"),
                    "copyright": detail.get("copyright") if detail.get("copyright") is not None else raw.get("copyright"),
                    "rights": detail.get("rights") or raw.get("rights") or {},
                }
                confidence, reason = _hit_relevance(
                    normalized,
                    up_name="",
                    aliases=aliases,
                    tags=[],
                    season_query=" ".join(aliases),
                )
                return confidence, reason, normalized, True, payload.get("_otomo_cache") or {}
            except (httpx.HTTPError, httpx.TransportError, ValueError) as exc:
                return confidence, reason, raw, False, {"rate_limited": _is_rate_limited(exc)}

        checked = await asyncio.gather(*(
            verify(row) for row in candidates[: min(max(args.limit * 3, 10), 20)]
        ))
        ranked: list[tuple[float, BiliSubjectVideoMeta]] = []
        role_bonus = {
            "public_full_episode": 0.22,
            "official_pv": 0.12,
            "review": 0.08,
            "retrospective": 0.08,
            "fan_creation": 0.02,
            "episode_candidate": -0.04,
            "related": 0.0,
        }
        for confidence, reason, raw, view_verified, view_mark in checked:
            cache_hit = cache_hit or bool(view_mark.get("hit"))
            rate_limited = rate_limited or bool(view_mark.get("rate_limited"))
            if view_mark.get("verified_at"):
                verified_times.append(str(view_mark["verified_at"]))
            if confidence < 0.56:
                continue
            title = _clean_bili_title(raw.get("title") or "")
            author = str(raw.get("author") or "")
            pages = [item for item in (raw.get("pages") or []) if isinstance(item, dict)]
            page_titles = [str(item.get("part") or "").strip() for item in pages if str(item.get("part") or "").strip()]
            page_duration = sum(max(int(item.get("duration") or 0), 0) for item in pages)
            duration_seconds = max(int(raw.get("duration") or 0), page_duration)
            copyright_code = int(raw.get("copyright")) if raw.get("copyright") in {1, 2, "1", "2"} else None
            role, uploader_class, watch_candidate, identity_evidence, content_evidence, caution = classify_subject_video(
                title,
                author,
                str(raw.get("description") or ""),
                staff_names=args.staff_names,
                duration_seconds=duration_seconds,
                page_titles=page_titles,
                copyright_code=copyright_code,
                match_confidence=confidence,
                expected_duration_seconds=(round(args.expected_episode_minutes * 60) if args.expected_episode_minutes else None),
            )
            version_ok, version_reason = _subject_version_compatibility(
                aliases,
                title,
                page_titles,
                args.subject_platform,
            )
            if version_reason:
                content_evidence.append(version_reason)
            # The work hub is editorial, not a raw Bilibili search page.  Merch,
            # radio, event, material and other generic title matches stay behind
            # the navigation link instead of filling cards or the conflict list.
            if role == "related":
                continue
            # Version consistency is a shared card boundary.  A season-two
            # recap/PV is just as misleading on a season-one page as a wrong-
            # season full episode, especially when the decisive signal lives in
            # multi-P titles rather than the top-level title.
            if not version_ok:
                conflict_url = raw.get("arcurl") or f"https://www.bilibili.com/video/{raw.get('bvid') or ('av' + str(raw.get('aid') or raw.get('id')))}"
                version_conflicts.append(BiliVersionConflict(
                    title=title,
                    url=str(conflict_url).replace("http://", "https://"),
                    aid=raw.get("aid") or raw.get("id"),
                    bvid=raw.get("bvid"),
                    author=author,
                    reason=version_reason,
                ))
                continue
            # Unverified full-episode uploads are deliberately harder to surface than reviews.
            if role == "episode_candidate" and confidence < 0.7:
                continue
            url = raw.get("arcurl") or f"https://www.bilibili.com/video/{raw.get('bvid') or ('av' + str(raw.get('aid') or raw.get('id')))}"
            url = str(url).replace("http://", "https://")
            page_links = []
            for index, page in enumerate(pages, start=1):
                try:
                    page_number = max(int(page.get("page") or index), 1)
                except (TypeError, ValueError):
                    page_number = index
                parsed = urllib.parse.urlsplit(url)
                query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                query_pairs = [(key, value) for key, value in query_pairs if key != "p"]
                query_pairs.append(("p", str(page_number)))
                page_url = urllib.parse.urlunsplit((
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    urllib.parse.urlencode(query_pairs),
                    parsed.fragment,
                ))
                page_links.append({
                    "page": page_number,
                    "title": str(page.get("part") or f"P{page_number}").strip(),
                    "url": page_url,
                    "duration_seconds": max(int(page.get("duration") or 0), 0) or None,
                })
            content_type, content_type_reason = classify_season_video(title, raw.get("pubdate"), query)
            editorial_role, spoiler_risk = _editorial_role(title, role)
            item = BiliSubjectVideoMeta(
                title=title,
                url=url,
                aid=raw.get("aid") or raw.get("id"),
                bvid=raw.get("bvid"),
                author=author,
                mid=raw.get("mid"),
                thumbnail_url=_clean_bili_image(raw.get("pic")),
                play=raw.get("play"),
                danmaku=raw.get("video_review"),
                pubdate=raw.get("pubdate"),
                content_type=content_type,
                content_type_reason=content_type_reason,
                matched_whitelist=author in _whitelist_by_name(),
                match_confidence=round(confidence, 3),
                match_reason=reason,
                verified=view_verified,
                verification_status="view_verified" if view_verified else "search_metadata",
                role=role,
                uploader_class=uploader_class,
                watch_candidate=watch_candidate,
                identity_evidence=identity_evidence,
                content_evidence=content_evidence,
                duration_seconds=duration_seconds or None,
                page_count=max(len(pages), int(raw.get("videos") or 0), 1),
                page_titles=page_titles[:8],
                page_links=page_links[:100],
                episode_coverage=_episode_coverage(title, page_titles),
                copyright_declaration="repost" if copyright_code == 2 else "original" if copyright_code == 1 else "unknown",
                editorial_role=editorial_role,
                spoiler_risk=spoiler_risk,
                caution=caution,
            )
            preference_bonus = 0.18 if author.strip().lower() in preferred_uploaders else 0.0
            ranked.append((confidence + role_bonus[role] + preference_bonus, item))
        ranked.sort(key=lambda row: (-row[0], -(row[1].pubdate or 0)))

        # Transcript/ASR is intentionally reserved for at most two top boundary
        # candidates.  It must never turn every subject search into a download/
        # transcription job, but it can catch obvious long-form UP narration.
        boundary_rows = [
            item for _score, item in ranked
            if item.role == "episode_candidate"
            or (item.watch_candidate and not item.episode_coverage and item.match_confidence < 0.86)
        ][:2]

        async def inspect_boundary(item: BiliSubjectVideoMeta) -> tuple[BiliSubjectVideoMeta, bool, bool]:
            try:
                subtitle_result = await asyncio.wait_for(
                    GetBiliVideoSubtitlesTool().run(BiliVideoSubtitleArgs(
                        aid=item.aid,
                        bvid=item.bvid,
                        max_segments=60,
                        allow_asr=settings.asr_provider != "off",
                        sample_across_video=True,
                    )),
                    timeout=min(max(settings.http_timeout * 2, 8), 24),
                )
            except (TimeoutError, asyncio.CancelledError):
                return item, False, False
            except Exception:  # noqa: BLE001 - transcript enrichment must never break search
                return item, False, False
            if not subtitle_result.ok or not subtitle_result.data:
                return item, False, False
            transcript = " ".join(segment.text for segment in subtitle_result.data.segments).lower()
            narration_signals = [
                signal for signal in (
                    "本期视频", "大家好我是", "今天我们", "这期节目", "剧情讲解", "剧情解析",
                    "一口气看", "带大家看", "接下来我们", "点赞投币", "三连", "关注我",
                    "这部动画讲述", "本作讲述",
                )
                if signal in transcript
            ]
            source_label = "公开字幕" if subtitle_result.data.source == "bili_public_subtitle" else "ASR"
            item.content_verified = True
            item.transcript_source = "subtitle" if subtitle_result.data.source == "bili_public_subtitle" else "asr"
            if len(narration_signals) >= 2:
                item.role = "retrospective"
                item.editorial_role = "recap"
                item.spoiler_risk = "high"
                item.watch_candidate = False
                if item.uploader_class == "unknown":
                    item.uploader_class = "creator"
                item.content_match_confidence = 0.88
                item.content_match_reason = f"{source_label}连续命中UP口播/讲解信号：" + "、".join(narration_signals[:4])
                item.content_evidence.append(item.content_match_reason)
                item.caution = "字幕内容表明它更像剧情讲解或漫评，已从正片入口降级。"
                return item, True, True
            item.content_match_confidence = 0.64
            item.content_match_reason = f"已抽样核验{source_label}，未发现成组的UP口播/剧情讲解信号"
            item.content_evidence.append(item.content_match_reason)
            return item, True, False

        if boundary_rows:
            inspected = await asyncio.gather(*(inspect_boundary(item) for item in boundary_rows))
            checked_count = sum(checked for _item, checked, _downgraded in inspected)
            downgraded_count = sum(downgraded for _item, _checked, downgraded in inspected)
            if checked_count:
                warnings.append(f"已对 {checked_count} 个最高优先级边界候选抽样读取公开字幕/ASR。")
            if downgraded_count:
                warnings.append(f"其中 {downgraded_count} 个命中连续UP口播/剧情讲解信号，已从正片入口降级。")

        # Build an editorial set rather than returning five near-identical
        # reviews. One item per useful role is chosen first; remaining slots
        # are filled by score under per-role safety limits.
        role_limits = {
            "public_full_episode": 3,
            "official_pv": 1,
            "review": 2,
            "retrospective": 2,
            "fan_creation": 1,
            "episode_candidate": 1,
        }
        selected: list[BiliSubjectVideoMeta] = []
        role_counts: dict[str, int] = {}
        editorial_order = (
            ["official", "no_spoiler_review", "deep_analysis", "review", "fan"]
            if args.lifecycle == "upcoming"
            else ["watch", "no_spoiler_review", "deep_analysis", "official", "recap", "review"]
            if args.lifecycle == "airing"
            else ["watch", "recap", "deep_analysis", "no_spoiler_review", "official", "review"]
        )
        for wanted in editorial_order:
            match = next(
                (
                    item for _score, item in ranked
                    if item.editorial_role == wanted
                    and item not in selected
                    and role_counts.get(item.role, 0) < role_limits.get(item.role, 0)
                ),
                None,
            )
            if match is None:
                continue
            selected.append(match)
            role_counts[match.role] = role_counts.get(match.role, 0) + 1
            if len(selected) >= args.limit:
                break
        for _score, item in ranked:
            if item in selected:
                continue
            if role_counts.get(item.role, 0) >= role_limits.get(item.role, 0):
                continue
            selected.append(item)
            role_counts[item.role] = role_counts.get(item.role, 0) + 1
            if len(selected) >= args.limit:
                break
        selected = selected[: args.limit]
        watch_candidates = [item for item in selected if item.watch_candidate]
        if watch_candidates:
            warnings.append(
                "发现作品匹配、时长与正片/分P特征同时成立的B站普通投稿；已作为可看正片候选展示，"
                "但它不是番剧库正版入口，版权与上传授权未核验。"
            )
        if any(item.role == "episode_candidate" for item in selected):
            warnings.append("存在标题像正片但时长、分P或作品一致性证据不足的稿件；只放在疑似区，不作为默认观看入口。")
        if version_conflicts:
            warnings.append(
                f"已从当前篇章移出 {len(version_conflicts)} 个季数/媒介形态冲突的视频候选；"
                + "；".join(f"《{item.title}》：{item.reason}" for item in version_conflicts[:2])
            )
        if not selected:
            warnings.append("没有具体视频通过作品标题与版本一致性阈值；保留 B站搜索导航。")
        if cache_hit:
            warnings.append("本轮优先复用了已核验的B站搜索/稿件缓存，减少等待和触发限流的概率。")
        if search_partial:
            warnings.append("部分B站搜索变体暂不可用；已用其余变体与缓存继续返回，不把单点失败当成整轮失败。")
        if rate_limited:
            warnings.append("B站本轮触发限流；已尽量使用缓存降级，未缓存部分不会用不可靠结果填充。")
        return ToolResult(
            ok=True,
            data=BiliSubjectVideosResult(
                query=query,
                count=len(selected),
                watch_candidates=watch_candidates,
                videos=selected,
                version_conflicts=version_conflicts[:6],
                navigation_url=_bili(query),
                cache_hit=cache_hit,
                search_partial=search_partial,
                rate_limited=rate_limited,
                last_verified=max(verified_times, default=""),
                account_mode=bilibili_account_mode(),
                warnings=warnings,
            ),
            sources=[Citation(title=f"Bilibili — {item.title}", url=item.url, source="bilibili") for item in selected[:5]],
        )


class GetBiliVideoCommentsTool(Tool):
    name = "get_bilibili_video_comments"
    description = (
        "读取 B站公开视频的一页评论抽样，用于新番导视/漫评视频下的观众期待、担心点、口碑氛围。"
        "只读公开评论，不抓视频内容；评论是话语源，不是事实源，并且默认有剧透风险。"
    )
    args_model = BiliVideoCommentsArgs
    result_model = BiliVideoCommentsResult

    async def run(self, args: BiliVideoCommentsArgs) -> ToolResult[BiliVideoCommentsResult]:
        try:
            data = await asyncio.to_thread(_sync_bili_replies, args.aid, args.limit)
        except (httpx.HTTPError, httpx.TransportError, ValueError) as e:
            return ToolResult(ok=False, error=f"B站评论抓取失败：{type(e).__name__}")
        comments: list[str] = []
        for raw in ((data.get("data") or {}).get("replies") or []):
            msg = ((raw.get("content") or {}).get("message") or "").strip()
            if msg:
                comments.append(msg[:220])
        if args.query:
            q = args.query
            comments.sort(key=lambda x: 0 if q in x else 1)
        comments = comments[: args.limit]
        aspect_opinions = _extract_aspect_opinions([
            CommentEvidence(source="B站评论", samples=comments)
        ])
        aspect_summary = _build_aspect_summary(aspect_opinions)
        url = f"https://www.bilibili.com/video/av{args.aid}"
        return ToolResult(
            ok=True,
            data=BiliVideoCommentsResult(
                aid=args.aid,
                count=len(comments),
                comments=comments,
                aspect_opinions=aspect_opinions,
                aspect_summary=aspect_summary,
                opinion_summary=_format_aspect_summary(aspect_summary),
                source_url=url,
                caveats=[
                    "B站评论是话语源，不是事实源。",
                    "评论可能包含剧透、玩梗或情绪化表达，回答时必须标注来源和不确定性。",
                ],
            ),
            sources=[Citation(title=f"Bilibili 评论 av{args.aid}", url=url, source="bilibili")],
        )


class GetBiliVideoSubtitlesTool(Tool):
    name = "get_bilibili_video_subtitles"
    description = (
        "读取 B站公开视频的公开字幕/ASR 片段，用于导视/漫评视频内容摘要。"
        "如果视频没有公开字幕或被风控，会明确失败；字幕是话语源，不是事实源。"
    )
    args_model = BiliVideoSubtitleArgs
    result_model = BiliVideoSubtitleResult

    async def run(self, args: BiliVideoSubtitleArgs) -> ToolResult[BiliVideoSubtitleResult]:
        if args.aid is None and not args.bvid:
            return ToolResult(ok=False, error="aid 或 bvid 至少传一个")
        try:
            pages = await asyncio.to_thread(_sync_bili_pagelist, args.aid, args.bvid)
            first = ((pages.get("data") or []) or [{}])[0]
            cid = first.get("cid")
            if not cid:
                return ToolResult(ok=False, error="未能从 B站 pagelist 获取 cid")
            player = await asyncio.to_thread(_sync_bili_player, args.aid, args.bvid, int(cid))
        except (httpx.HTTPError, httpx.TransportError, ValueError) as e:
            return ToolResult(ok=False, error=f"B站字幕元数据读取失败：{type(e).__name__}")
        subtitles = (((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
        video_id = args.bvid or (f"av{args.aid}" if args.aid else "")
        source_url = f"https://www.bilibili.com/video/{video_id}" if video_id else "https://www.bilibili.com/"
        if not subtitles:
            if not args.allow_asr:
                return ToolResult(
                    ok=False,
                    error="该视频未暴露公开字幕；本次轻量核验未启动 ASR。",
                )
            asr_segments, asr_caveats, asr_error = await _maybe_asr_segments(source_url, args.max_segments)
            if not asr_segments:
                return ToolResult(
                    ok=False,
                    error=asr_error or "该视频未暴露公开字幕，且 ASR 未启用；可回退到标题、简介、弹幕或评论区摘要。",
                )
            return ToolResult(
                ok=True,
                data=BiliVideoSubtitleResult(
                    aid=args.aid,
                    bvid=args.bvid,
                    cid=int(cid),
                    subtitle_url="",
                    source="bili_asr",
                    count=len(asr_segments),
                    segments=asr_segments,
                    rough_summary=_rough_subtitle_summary(asr_segments),
                    caveats=asr_caveats,
                ),
                sources=[Citation(title=f"Bilibili ASR {video_id}", url=source_url, source="bilibili")],
            )
        sub = subtitles[0]
        url = sub.get("subtitle_url") or ""
        if not url:
            return ToolResult(ok=False, error="字幕条目缺少 subtitle_url")
        try:
            payload = await asyncio.to_thread(_sync_subtitle_json, url)
        except (httpx.HTTPError, httpx.TransportError, ValueError) as e:
            return ToolResult(ok=False, error=f"B站字幕正文读取失败：{type(e).__name__}")
        body = payload.get("body") or []
        if args.sample_across_video and len(body) > args.max_segments:
            indexes = sorted({
                round(index * (len(body) - 1) / max(args.max_segments - 1, 1))
                for index in range(args.max_segments)
            })
            selected_body = [body[index] for index in indexes]
        else:
            selected_body = body[: args.max_segments]
        segments = []
        for raw in selected_body:
            text_value = str(raw.get("content") or "").strip()
            if text_value:
                segments.append(
                    BiliSubtitleSegment(
                        start=raw.get("from"),
                        end=raw.get("to"),
                        text=text_value[:220],
                    )
                )
        return ToolResult(
            ok=True,
            data=BiliVideoSubtitleResult(
                aid=args.aid,
                bvid=args.bvid,
                cid=int(cid),
                subtitle_url=url,
                source="bili_public_subtitle",
                count=len(segments),
                segments=segments,
                rough_summary=_rough_subtitle_summary(segments),
                caveats=[
                    "B站字幕/ASR 是视频话语源，不是 canonical 事实源。",
                    "字幕可能不完整、自动识别错误或包含剧透；回答时需标注来源和风险。",
                ],
            ),
            sources=[Citation(title=f"Bilibili 字幕 {video_id}", url=source_url, source="bilibili")],
        )


class GetBiliVideoDanmakuTool(Tool):
    name = "get_bilibili_video_danmaku"
    description = (
        "读取 B站公开视频弹幕 XML 抽样，用于无字幕导视/漫评视频的观众即时反应、梗和讨论氛围。"
        "弹幕是话语源，不是视频正文；可能高剧透、玩梗、刷屏。"
    )
    args_model = BiliVideoDanmakuArgs
    result_model = BiliVideoDanmakuResult

    async def run(self, args: BiliVideoDanmakuArgs) -> ToolResult[BiliVideoDanmakuResult]:
        if args.aid is None and not args.bvid:
            return ToolResult(ok=False, error="aid 或 bvid 至少传一个")
        try:
            pages = await asyncio.to_thread(_sync_bili_pagelist, args.aid, args.bvid)
            first = ((pages.get("data") or []) or [{}])[0]
            cid = first.get("cid")
            if not cid:
                return ToolResult(ok=False, error="未能从 B站 pagelist 获取 cid")
            xml_text = await asyncio.to_thread(_sync_bili_danmaku_xml, int(cid))
        except (httpx.HTTPError, httpx.TransportError, ValueError) as e:
            return ToolResult(ok=False, error=f"B站弹幕读取失败：{type(e).__name__}")
        items = _parse_danmaku(xml_text, args.limit)
        if args.query:
            q = args.query
            items.sort(key=lambda x: 0 if q in x.text else 1)
        samples = [x.text for x in items[: args.limit]]
        aspect_opinions = _extract_aspect_opinions([CommentEvidence(source="B站弹幕", samples=samples)])
        aspect_summary = _build_aspect_summary(aspect_opinions)
        source_url = _video_url(args.aid, args.bvid)
        return ToolResult(
            ok=True,
            data=BiliVideoDanmakuResult(
                aid=args.aid,
                bvid=args.bvid,
                cid=int(cid),
                count=len(items),
                danmaku=items[: args.limit],
                aspect_opinions=aspect_opinions,
                aspect_summary=aspect_summary,
                opinion_summary=_format_aspect_summary(aspect_summary) or _rough_danmaku_summary(items),
                source_url=source_url,
                caveats=[
                    "B站弹幕是即时话语源，不是视频正文或 canonical 事实源。",
                    "弹幕可能刷屏、玩梗、含剧透；只适合作为观众反应/氛围证据。",
                ],
            ),
            sources=[Citation(title=f"Bilibili 弹幕 {args.bvid or f'av{args.aid}'}", url=source_url, source="bilibili")],
        )


class SummarizeBiliVideoContentTool(Tool):
    name = "summarize_bilibili_video_content"
    description = (
        "总结 B站导视/漫评视频的可公开读取内容，并按 字幕/ASR → 弹幕 → 评论 → 元数据 降级。"
        "适合无字幕视频：会明确说明实际读到了哪一层，不会假装看过画面/PPT。"
    )
    args_model = BiliVideoContentArgs
    result_model = BiliVideoContentResult

    async def run(self, args: BiliVideoContentArgs) -> ToolResult[BiliVideoContentResult]:
        aid, bvid, ref_notes = await _resolve_video_ref(args.url, args.aid, args.bvid)
        if aid is None and not bvid:
            return ToolResult(ok=False, error="需要 url、aid 或 bvid 至少一个")
        title, cid = "", None
        desc, owner, stat = "", "", {}
        view_error = ""
        try:
            view = await asyncio.to_thread(_sync_bili_view, aid, bvid)
            data = view.get("data") or {}
            aid = int(data.get("aid") or aid or 0) or aid
            bvid = data.get("bvid") or bvid
            cid = data.get("cid")
            title = _clean_bili_title(data.get("title") or "")
            desc = str(data.get("desc") or "").strip()
            owner = ((data.get("owner") or {}).get("name") or "").strip()
            stat = data.get("stat") or {}
        except Exception as e:  # noqa: BLE001
            view_error = f"B站 view 元数据读取失败：{type(e).__name__}"
        source_url = _video_url(aid, bvid)

        metadata = [
            title,
            f"UP：{owner}" if owner else "",
            f"播放 {stat.get('view')} · 弹幕 {stat.get('danmaku')}" if stat else "",
            desc[:240],
        ]
        subtitles, danmaku, comments = await asyncio.gather(
            GetBiliVideoSubtitlesTool().run(BiliVideoSubtitleArgs(aid=aid, bvid=bvid, max_segments=min(args.limit, 160))),
            GetBiliVideoDanmakuTool().run(BiliVideoDanmakuArgs(aid=aid, bvid=bvid, limit=args.limit, query=args.query)),
            GetBiliVideoCommentsTool().run(BiliVideoCommentsArgs(aid=aid, query=args.query, limit=min(args.limit, 50)))
            if aid else asyncio.sleep(0, result=ToolResult(ok=False, error="缺少 aid，跳过评论读取")),
        )

        read_layers: list[str] = []
        caveats: list[str] = []
        subtitle_summary: list[str] = []
        subtitle_segments: list[BiliSubtitleSegment] = []
        danmaku_summary: list[str] = []
        danmaku_samples: list[BiliDanmakuItem] = []
        comment_summary: list[str] = []
        comment_samples: list[str] = []

        if subtitles.ok and subtitles.data is not None and subtitles.data.count:
            subtitle_layer = "asr" if subtitles.data.source == "bili_asr" else "subtitle"
            read_layers.append(subtitle_layer)
            cid = subtitles.data.cid or cid
            subtitle_summary = subtitles.data.rough_summary
            subtitle_segments = subtitles.data.segments[:12]
            caveats.extend(subtitles.data.caveats)
        else:
            caveats.append(subtitles.error or "该视频未暴露公开字幕/ASR。")

        if danmaku.ok and danmaku.data is not None and danmaku.data.count:
            read_layers.append("danmaku")
            cid = danmaku.data.cid or cid
            danmaku_summary = danmaku.data.opinion_summary or _rough_danmaku_summary(danmaku.data.danmaku)
            danmaku_samples = danmaku.data.danmaku[:20]
            caveats.extend(danmaku.data.caveats)
        elif danmaku.error:
            caveats.append(danmaku.error)

        if comments.ok and comments.data is not None and comments.data.count:
            read_layers.append("comments")
            comment_summary = comments.data.opinion_summary or comments.data.comments[:6]
            comment_samples = comments.data.comments[:20]
            caveats.extend(comments.data.caveats)
        elif comments.error:
            caveats.append(comments.error)

        metadata_summary = [x for x in metadata if x]
        if metadata_summary:
            read_layers.append("metadata")
        content_summary = subtitle_summary or [
            "未读到公开字幕/ASR；当前无法直接知道视频正文或 PPT 画面写了什么。"
        ]
        audience_summary = []
        for item in [*danmaku_summary[:5], *comment_summary[:5]]:
            if item and item not in audience_summary:
                audience_summary.append(item)
        access_level: Literal["multi", "subtitle", "asr", "danmaku", "comments", "metadata", "unavailable"]
        if subtitle_summary and (danmaku_summary or comment_summary):
            access_level = "multi"
        elif subtitle_summary:
            access_level = "asr" if "asr" in read_layers else "subtitle"
        elif danmaku_summary:
            access_level = "danmaku"
        elif comment_summary:
            access_level = "comments"
        elif metadata_summary:
            access_level = "metadata"
        else:
            access_level = "unavailable"
        analysis_plan = [
            "如果用户要正文观点，以 subtitle_summary 为主；弹幕/评论只作观众反应。",
            "如果该视频是无字幕 PPT/放歌导视，需要用户上传视频文件或关键帧，再调用 analyze_video_frames 做 OCR/VLM。",
            "涉及作品事实、播出时间、制作阵容时必须回到 Bangumi/yuc 等事实源核验。",
        ]
        if view_error:
            caveats.append(view_error)
        caveats.extend(ref_notes)
        # 去重并保留顺序，避免面板被重复 caveat 淹没。
        caveats = list(dict.fromkeys([x for x in caveats if x]))[:10]
        return ToolResult(
            ok=True,
            data=BiliVideoContentResult(
                aid=aid,
                bvid=bvid,
                cid=cid,
                title=title,
                source_url=source_url,
                access_level=access_level,
                read_layers=read_layers,
                content_summary=content_summary[:8],
                audience_summary=audience_summary[:8],
                subtitle_summary=subtitle_summary[:8],
                danmaku_summary=danmaku_summary[:8],
                comment_summary=comment_summary[:8],
                metadata_summary=metadata_summary,
                subtitle_segments=subtitle_segments,
                danmaku_samples=danmaku_samples,
                comment_samples=comment_samples,
                analysis_plan=analysis_plan,
                caveats=caveats,
            ),
            sources=[Citation(title=title or source_url, url=source_url, source="bilibili")],
        )


def build_video_tools() -> list[Tool]:
    return [
        FindVideosTool(),
        FindGuideVideosTool(),
        SearchBiliGuideVideosTool(),
        SearchBiliSubjectVideosTool(),
        GetBiliVideoCommentsTool(),
        GetBiliVideoSubtitlesTool(),
        GetBiliVideoDanmakuTool(),
        SummarizeBiliVideoContentTool(),
    ]
