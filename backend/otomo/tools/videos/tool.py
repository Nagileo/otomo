"""相关视频外链工具（外部知识增强档之一）。

给作品/角色/话题构造 B站搜索外链（综合 / 解析考据 / 二创MAD），作为"延伸观看"。
**仅 link-out**：不调 B站 API、不抓取、不嵌入视频（避免反爬与版权）。
"""
from __future__ import annotations

import asyncio
import urllib.parse
import html
import re
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
    aid: int | None = Field(None, description="B站 av/aid；aid 或 bvid 至少传一个")
    bvid: str | None = Field(None, description="B站 BV 号；aid 或 bvid 至少传一个")
    query: str | None = Field(None, description="关注点，如『新番导视提到哪些作品』")
    limit: int = Field(80, ge=10, le=200)


class GuideVideoLink(BaseModel):
    label: str
    url: str
    up_name: str
    up_url: str
    positioning: str
    match_reason: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"


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
    access_level: Literal["subtitle", "danmaku", "comments", "metadata", "unavailable"] = "unavailable"
    subtitle_summary: list[str] = Field(default_factory=list)
    danmaku_summary: list[str] = Field(default_factory=list)
    comment_summary: list[str] = Field(default_factory=list)
    metadata_summary: list[str] = Field(default_factory=list)
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
    },
    {
        "name": "泛式",
        "uid": "63231",
        "positioning": "评价向导视/综合漫评",
        "intents": {"season", "review", "all"},
        "keywords": ["新番导视", "评价", "推荐"],
        "tags": {"新番", "季度", "漫评", "评价"},
    },
    {
        "name": "瓶子君152",
        "uid": "730732",
        "positioning": "评价向漫评/动画杂谈",
        "intents": {"season", "review", "all"},
        "keywords": ["新番导视", "评价", "杂谈"],
        "tags": {"新番", "季度", "漫评", "评价"},
    },
    {
        "name": "台长",
        "uid": "213741",
        "positioning": "综合漫评/动画杂谈",
        "intents": {"season", "review", "all"},
        "keywords": ["新番导视", "评价", "杂谈"],
        "tags": {"新番", "季度", "漫评", "评价"},
    },
    {
        "name": "FlowerMX-花梦",
        "uid": "13181306",
        "positioning": "百合向新番导视/推荐",
        "intents": {"season", "review", "yuri", "all"},
        "keywords": ["百合", "新番导视", "推荐"],
        "tags": {"百合", "GL", "新番", "季度"},
    },
    {
        "name": "峻岸上的喀秋莎_Channel",
        "uid": "228172909",
        "positioning": "百合作品翻译/介绍",
        "intents": {"yuri", "review", "all"},
        "keywords": ["百合", "介绍", "推荐"],
        "tags": {"百合", "GL", "翻译"},
    },
    {
        "name": "芳文观星台",
        "uid": "1585955812",
        "positioning": "芳文社/Kirara 系盘点",
        "intents": {"kirara", "review", "all"},
        "keywords": ["芳文社", "Kirara", "きらら"],
        "tags": {"芳文社", "Kirara", "きらら", "日常"},
    },
    {
        "name": "大猫猫组",
        "uid": "526330959",
        "positioning": "芳文社/Kirara 系内容",
        "intents": {"kirara", "review", "all"},
        "keywords": ["芳文社", "Kirara", "きらら"],
        "tags": {"芳文社", "Kirara", "きらら", "日常"},
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


def _guide_score(up: dict, intent: str, tags: list[str]) -> tuple[int, str, str]:
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
    confidence = "high" if score >= 5 else ("medium" if score >= 3 else "low")
    return score, "；".join(dict.fromkeys(reasons)) or "通用导视入口", confidence


def _guide_links(query: str, intent: str, limit: int, tags: list[str] | None = None) -> list[GuideVideoLink]:
    q = query.strip()
    tags = tags or []
    ranked: list[tuple[int, int, GuideVideoLink]] = []
    for up in _GUIDE_UPS:
        score, reason, confidence = _guide_score(up, intent, tags)
        if intent != "all" and score <= 0:
            continue
        keyword_tag = next((t for t in tags if t in up.get("tags", set())), "")
        keyword = " ".join([q, up["name"], keyword_tag or up["keywords"][0]]).strip()
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
            ),
        ))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [x[2] for x in ranked[:limit]]


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
            sources=[Citation(title=l.label, url=l.url, source="bilibili") for l in links],
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
            sources=[Citation(title=l.label, url=l.url, source="bilibili") for l in links],
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
        if not subtitles:
            return ToolResult(
                ok=False,
                error="该视频未暴露公开字幕/ASR；可回退到标题、简介或评论区摘要。",
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
        video_id = args.bvid or (f"av{args.aid}" if args.aid else "")
        source_url = f"https://www.bilibili.com/video/{video_id}" if video_id else "https://www.bilibili.com/"
        return ToolResult(
            ok=True,
            data=BiliVideoSubtitleResult(
                aid=args.aid,
                bvid=args.bvid,
                cid=int(cid),
                subtitle_url=url,
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
        if args.aid is None and not args.bvid:
            return ToolResult(ok=False, error="aid 或 bvid 至少传一个")
        aid, bvid, title, cid = args.aid, args.bvid, "", None
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
        except Exception:  # noqa: BLE001
            desc, owner, stat = "", "", {}
        source_url = _video_url(aid, bvid)

        subtitles = await GetBiliVideoSubtitlesTool().run(
            BiliVideoSubtitleArgs(aid=aid, bvid=bvid, max_segments=min(args.limit, 160))
        )
        if subtitles.ok and subtitles.data is not None and subtitles.data.rough_summary:
            return ToolResult(
                ok=True,
                data=BiliVideoContentResult(
                    aid=aid,
                    bvid=bvid,
                    cid=subtitles.data.cid,
                    title=title,
                    source_url=source_url,
                    access_level="subtitle",
                    subtitle_summary=subtitles.data.rough_summary,
                    metadata_summary=[x for x in [title, f"UP：{owner}" if owner else "", desc[:180]] if x],
                    caveats=subtitles.data.caveats + ["已读取公开字幕/ASR；仍不等同于完整视频画面理解。"],
                ),
                sources=[Citation(title=title or source_url, url=source_url, source="bilibili")],
            )

        danmaku = await GetBiliVideoDanmakuTool().run(
            BiliVideoDanmakuArgs(aid=aid, bvid=bvid, limit=args.limit, query=args.query)
        )
        if danmaku.ok and danmaku.data is not None and danmaku.data.count:
            return ToolResult(
                ok=True,
                data=BiliVideoContentResult(
                    aid=aid,
                    bvid=bvid,
                    cid=danmaku.data.cid,
                    title=title,
                    source_url=source_url,
                    access_level="danmaku",
                    danmaku_summary=danmaku.data.opinion_summary or _rough_danmaku_summary(danmaku.data.danmaku),
                    metadata_summary=[x for x in [title, f"UP：{owner}" if owner else "", desc[:180]] if x],
                    caveats=danmaku.data.caveats + ["该视频没有可用公开字幕时，当前结果只代表弹幕反应，不代表视频 PPT/画面内容。"],
                ),
                sources=[Citation(title=title or source_url, url=source_url, source="bilibili")],
            )

        if aid:
            comments = await GetBiliVideoCommentsTool().run(
                BiliVideoCommentsArgs(aid=aid, query=args.query, limit=min(args.limit, 50))
            )
            if comments.ok and comments.data is not None and comments.data.count:
                return ToolResult(
                    ok=True,
                    data=BiliVideoContentResult(
                        aid=aid,
                        bvid=bvid,
                        cid=cid,
                        title=title,
                        source_url=source_url,
                        access_level="comments",
                        comment_summary=comments.data.opinion_summary or comments.data.comments[:6],
                        metadata_summary=[x for x in [title, f"UP：{owner}" if owner else "", desc[:180]] if x],
                        caveats=comments.data.caveats + ["未读到字幕/弹幕正文时，当前结果只代表评论区氛围。"],
                    ),
                    sources=[Citation(title=title or source_url, url=source_url, source="bilibili")],
                )

        metadata = [
            title,
            f"UP：{owner}" if owner else "",
            f"播放 {stat.get('view')} · 弹幕 {stat.get('danmaku')}" if stat else "",
            desc[:240],
        ]
        return ToolResult(
            ok=True,
            data=BiliVideoContentResult(
                aid=aid,
                bvid=bvid,
                cid=cid,
                title=title,
                source_url=source_url,
                access_level="metadata",
                metadata_summary=[x for x in metadata if x],
                caveats=[
                    "未读取到公开字幕、弹幕或评论；只能返回视频元数据。",
                    "对于无字幕 PPT/放歌类视频，需要后续接入抽帧+OCR/VLM 才能理解画面文字。",
                ],
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
