"""相关视频外链工具（外部知识增强档之一）。

给作品/角色/话题构造 B站搜索外链（综合 / 解析考据 / 二创MAD），作为"延伸观看"。
**仅 link-out**：不调 B站 API、不抓取、不嵌入视频（避免反爬与版权）。
"""
from __future__ import annotations

import asyncio
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
    whitelist_only: bool = Field(True, description="是否只保留白名单 UP；默认 true")
    limit: int = Field(8, ge=1, le=20)


class BiliVideoCommentsArgs(BaseModel):
    aid: int = Field(..., description="B站 av/aid；可先用 search_bilibili_guide_videos 获得")
    query: str | None = Field(None, description="可选语义关键词；当前只做轻量词法优先，不做全文 RAG")
    limit: int = Field(20, ge=1, le=50)


class BiliVideoSubtitleArgs(BaseModel):
    aid: int | None = Field(None, description="B站 av/aid；aid 或 bvid 至少传一个")
    bvid: str | None = Field(None, description="B站 BV 号；aid 或 bvid 至少传一个")
    max_segments: int = Field(60, ge=10, le=160, description="最多返回多少条字幕片段")


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
    author: str
    play: int | None = None
    danmaku: int | None = None
    pubdate: int | None = None
    match_confidence: float = 0.0
    match_reason: str = ""


class GuideVideoLink(BaseModel):
    label: str
    url: str
    up_name: str
    up_url: str
    positioning: str
    match_reason: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    route_score: int = 0
    verticals: list[SubjectVertical] = Field(default_factory=list)
    verified: bool = False
    verified_hits: list[GuideVideoHit] = Field(default_factory=list)
    verification_query: str = ""
    verification_note: str = ""


class BiliVideoMeta(BaseModel):
    title: str
    url: str
    aid: int | None = None
    bvid: str | None = None
    author: str
    mid: int | None = None
    play: int | None = None
    danmaku: int | None = None
    pubdate: int | None = None
    matched_whitelist: bool = False
    match_reason: str = ""


class BiliGuideSearchResult(BaseModel):
    query: str
    count: int
    videos: list[BiliVideoMeta] = Field(default_factory=list)


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


def _guide_links(query: str, intent: str, limit: int, tags: list[str] | None = None) -> list[GuideVideoLink]:
    q = query.strip()
    tags = tags or []
    verticals = classify_subject_verticals(tags, title=q)
    ranked: list[tuple[int, int, GuideVideoLink]] = []
    for up in _GUIDE_UPS:
        score, reason, confidence = _guide_score(up, intent, tags, verticals)
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
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [x[2] for x in ranked[:limit]]


def _hit_relevance(raw: dict, *, up_name: str, aliases: list[str], tags: list[str], season_query: str = "") -> tuple[float, str]:
    title = _clean_bili_title(raw.get("title") or "")
    author = raw.get("author") or ""
    title_key = _norm_video_text(title)
    alias_keys = [_norm_video_text(x) for x in aliases if _norm_video_text(x)]
    score = 0.0
    reasons: list[str] = []
    if author == up_name:
        score += 0.35
        reasons.append("UP 精确命中")
    if any(k and (k in title_key or title_key in k) and min(len(k), len(title_key)) >= 4 for k in alias_keys):
        score += 0.38
        reasons.append("标题命中作品名")
    season_key = _norm_video_text(season_query)
    if season_key and season_key in title_key:
        score += 0.22
        reasons.append("标题命中季度查询")
    guide_hits = [k for k in ("新番", "导视", "推荐", "评价", "杂谈", "百合", "芳文", "kirara", "きらら") if k.lower() in title.lower()]
    if guide_hits:
        score += min(0.18, 0.06 * len(guide_hits))
        reasons.append("标题命中导视/圈层词：" + "、".join(guide_hits[:3]))
    tag_hits = [t for t in tags if t and t.lower() in title.lower()]
    if tag_hits:
        score += min(0.12, 0.04 * len(tag_hits))
        reasons.append("标题命中标签：" + "、".join(tag_hits[:3]))
    return min(score, 1.0), "；".join(reasons) or "弱相关搜索结果"


async def verify_guide_video_links(
    query: str,
    links: list[GuideVideoLink],
    *,
    title_aliases: list[str] | None = None,
    tags: list[str] | None = None,
    max_links: int = 2,
    max_hits_per_link: int = 1,
    min_confidence: float = 0.55,
) -> list[GuideVideoLink]:
    """对路由出的白名单 UP 做真实 B站搜索验证。

    命中失败不删除导航入口，只把 verified=false 和 verification_note 暴露给前端，避免把"适合这个圈层"
    误说成"这个 UP 已经讲过这部作品"。
    """
    tags = tags or []
    aliases = [x for x in (title_aliases or []) if x]
    verified_links = [link.model_copy(deep=True) for link in links]
    for idx, link in enumerate(verified_links[:max_links]):
        vertical_terms = [v.label for v in link.verticals[:2]]
        search_query = " ".join(dict.fromkeys([*(aliases[:1] or [query]), link.up_name, *(vertical_terms or tags[:1])])).strip()
        link.verification_query = search_query
        try:
            data = await _bili_search_async(search_query)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 412:
                link.verification_note = f"B站搜索验证失败：HTTP {e.response.status_code}"
                continue
            try:
                data = await asyncio.to_thread(_sync_bili_search, search_query)
            except (httpx.HTTPError, httpx.TransportError, ValueError) as fallback_e:
                link.verification_note = f"B站搜索验证失败：HTTP 412 / fallback {type(fallback_e).__name__}"
                continue
        except (httpx.HTTPError, httpx.TransportError, ValueError) as e:
            link.verification_note = f"B站搜索验证失败：{type(e).__name__}"
            continue

        candidates: list[tuple[float, GuideVideoHit]] = []
        for raw in ((data.get("data") or {}).get("result") or []):
            author = raw.get("author") or ""
            if author != link.up_name:
                continue
            url = raw.get("arcurl") or (f"https://www.bilibili.com/video/{raw.get('bvid')}" if raw.get("bvid") else "")
            if not url:
                continue
            conf, reason = _hit_relevance(raw, up_name=link.up_name, aliases=aliases, tags=tags, season_query=query)
            if conf < min_confidence:
                continue
            candidates.append((
                conf,
                GuideVideoHit(
                    title=_clean_bili_title(raw.get("title") or ""),
                    url=url.replace("http://", "https://"),
                    aid=raw.get("aid") or raw.get("id"),
                    bvid=raw.get("bvid"),
                    author=author,
                    play=raw.get("play"),
                    danmaku=raw.get("video_review"),
                    pubdate=raw.get("pubdate"),
                    match_confidence=round(conf, 3),
                    match_reason=reason,
                ),
            ))
        candidates.sort(key=lambda x: -x[0])
        link.verified_hits = [x[1] for x in candidates[:max_hits_per_link]]
        link.verified = bool(link.verified_hits)
        link.verification_note = (
            f"已命中 {len(link.verified_hits)} 个白名单相关视频。"
            if link.verified else
            "未命中足够相关的具体视频，仅保留 UP/搜索入口。"
        )
    return verified_links


def _whitelist_by_name() -> dict[str, dict]:
    return {u["name"]: u for u in _GUIDE_UPS}


def _bili_json(data: dict) -> dict:
    """B站把风控/错误放在 200 响应体的 code 字段（-412 风控 / -404 等），HTTP 状态仍是 200。

    code!=0 时抛 ValueError，让上层的 except 统一按"抓取失败"降级，而不是静默返回空列表
    （否则 agent 会误以为"没有导视视频/没有评论"）。
    """
    code = data.get("code", 0)
    if code not in (0, None):
        raise ValueError(f"bilibili code={code}: {data.get('message') or ''}")
    return data


@scached()
def _sync_bili_search(query: str) -> dict:
    r = httpx.get(
        _BILI_SEARCH_API,
        params={"search_type": "video", "keyword": query, "page": 1},
        headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"},
        timeout=settings.http_timeout,
    )
    r.raise_for_status()
    return _bili_json(r.json())


@scached()
def _sync_bili_replies(aid: int, limit: int) -> dict:
    r = httpx.get(
        _BILI_REPLY_API,
        params={"type": 1, "oid": aid, "sort": 1, "pn": 1, "ps": min(limit, 50)},
        headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"},
        timeout=settings.http_timeout,
    )
    r.raise_for_status()
    return _bili_json(r.json())


@acached()
async def _bili_search_async(q: str) -> dict:
    async with httpx.AsyncClient(
        timeout=settings.http_timeout,
        headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"},
    ) as c:
        r = await c.get(_BILI_SEARCH_API, params={"search_type": "video", "keyword": q, "page": 1})
        r.raise_for_status()
        return _bili_json(r.json())


def _summarize_aspect_opinions(opinions: list[AspectOpinion]) -> list[str]:
    return _format_aspect_summary(_build_aspect_summary(opinions))


@scached()
def _sync_bili_view(aid: int | None, bvid: str | None) -> dict:
    params = {"aid": aid} if aid else {"bvid": bvid}
    r = httpx.get(
        _BILI_VIEW_API,
        params=params,
        headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"},
        timeout=settings.http_timeout,
    )
    r.raise_for_status()
    return _bili_json(r.json())


@scached()
def _sync_bili_pagelist(aid: int | None, bvid: str | None) -> dict:
    params = {"aid": aid} if aid else {"bvid": bvid}
    r = httpx.get(
        _BILI_PAGELIST_API,
        params=params,
        headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"},
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
        headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"},
        timeout=settings.http_timeout,
    )
    r.raise_for_status()
    return _bili_json(r.json())


@scached()
def _sync_subtitle_json(url: str) -> dict:
    full = "https:" + url if url.startswith("//") else url
    r = httpx.get(
        full,
        headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"},
        timeout=settings.http_timeout,
    )
    r.raise_for_status()
    return r.json()


@scached()
def _sync_bili_danmaku_xml(cid: int) -> str:
    r = httpx.get(
        _BILI_DANMAKU_API.format(cid=cid),
        headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"},
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
        headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"},
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


async def _maybe_asr_segments(source_url: str, max_segments: int) -> tuple[list[BiliSubtitleSegment], list[str], str | None]:
    provider = (settings.asr_provider or "off").strip().lower()
    if provider in {"", "off", "none", "false"}:
        return [], ["ASR_PROVIDER=off，未启用本地转写。"], None
    if provider != "local":
        return [], [f"ASR_PROVIDER={settings.asr_provider} 暂未接入；当前仅支持 local。"], None
    try:
        segments = await _local_bili_asr(source_url, max_segments)
    except Exception as e:  # noqa: BLE001
        hint = "（B站 412 风控：导出浏览器 cookies.txt 并配置 ASR_COOKIES_FILE 可解除）" if "412" in str(e) else ""
        return [], [f"本地 ASR 转写失败：{type(e).__name__}: {e}{hint}"], str(e)
    caveats = [
        "本地 ASR 由 faster-whisper 识别公开视频音频，可能漏字、错字或错分段。",
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
        "搜索 B站导视/漫评视频元数据，返回标题、UP、播放量、BV 链接；默认只保留白名单 UP。"
        "用于新番导视和作品评价延伸的辅助排序。不读取评论、不抓视频内容、不做字幕转写。"
    )
    args_model = BiliGuideSearchArgs
    result_model = BiliGuideSearchResult

    async def run(self, args: BiliGuideSearchArgs) -> ToolResult[BiliGuideSearchResult]:
        q = " ".join([args.query.strip()] + (args.tags or [])).strip()
        whitelist = _whitelist_by_name()
        videos: list[BiliVideoMeta] = []
        seen: set[str] = set()
        try:
            data = await _bili_search_async(q)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 412:
                return ToolResult(ok=False, error=f"B站导视元数据搜索失败：HTTP {e.response.status_code}")
            try:
                data = await asyncio.to_thread(_sync_bili_search, q)
            except (httpx.HTTPError, httpx.TransportError, ValueError) as fallback_e:
                return ToolResult(ok=False, error=f"B站导视元数据搜索失败：HTTP 412 / fallback {type(fallback_e).__name__}")
        except (httpx.HTTPError, httpx.TransportError, ValueError) as e:
            return ToolResult(ok=False, error=f"B站导视元数据搜索失败：{type(e).__name__}")

        def add_from(data_obj: dict, only_author: str | None = None) -> None:
            for raw in ((data_obj.get("data") or {}).get("result") or []):
                author = raw.get("author") or ""
                if only_author and author != only_author:
                    continue
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
                reason = whitelist[author]["positioning"] if matched else "非白名单搜索结果"
                videos.append(
                    BiliVideoMeta(
                        title=_clean_bili_title(raw.get("title") or ""),
                        url=url,
                        aid=raw.get("aid") or raw.get("id"),
                        bvid=raw.get("bvid"),
                        author=author,
                        mid=raw.get("mid"),
                        play=raw.get("play"),
                        danmaku=raw.get("video_review"),
                        pubdate=raw.get("pubdate"),
                        matched_whitelist=matched,
                        match_reason=reason,
                    )
                )
                if len(videos) >= args.limit:
                    return

        add_from(data)
        if args.whitelist_only and not videos:
            for up_name in whitelist:
                try:
                    narrowed = await asyncio.to_thread(_sync_bili_search, f"{q} {up_name}")
                except (httpx.HTTPError, httpx.TransportError, ValueError):
                    continue
                add_from(narrowed, only_author=up_name)
                if len(videos) >= args.limit:
                    break
        return ToolResult(
            ok=True,
            data=BiliGuideSearchResult(query=q, count=len(videos), videos=videos),
            sources=[
                Citation(title=f"Bilibili — {v.title}", url=v.url, source="bilibili")
                for v in videos[:5]
            ],
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
        segments = []
        for raw in (payload.get("body") or [])[: args.max_segments]:
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
        GetBiliVideoCommentsTool(),
        GetBiliVideoSubtitlesTool(),
        GetBiliVideoDanmakuTool(),
        SummarizeBiliVideoContentTool(),
    ]
