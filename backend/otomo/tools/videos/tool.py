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

import httpx
from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
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


def _sync_bili_search(query: str) -> dict:
    r = httpx.get(
        _BILI_SEARCH_API,
        params={"search_type": "video", "keyword": query, "page": 1},
        headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"},
        timeout=settings.http_timeout,
    )
    r.raise_for_status()
    return _bili_json(r.json())


def _sync_bili_replies(aid: int, limit: int) -> dict:
    r = httpx.get(
        _BILI_REPLY_API,
        params={"type": 1, "oid": aid, "sort": 1, "pn": 1, "ps": min(limit, 50)},
        headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"},
        timeout=settings.http_timeout,
    )
    r.raise_for_status()
    return _bili_json(r.json())


def _summarize_aspect_opinions(opinions: list[AspectOpinion]) -> list[str]:
    return _format_aspect_summary(_build_aspect_summary(opinions))


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
            async with httpx.AsyncClient(
                timeout=settings.http_timeout,
                headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/"},
            ) as c:
                r = await c.get(
                    _BILI_SEARCH_API,
                    params={"search_type": "video", "keyword": q, "page": 1},
                )
                r.raise_for_status()
                data = _bili_json(r.json())
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


def build_video_tools() -> list[Tool]:
    return [FindVideosTool(), FindGuideVideosTool(), SearchBiliGuideVideosTool(), GetBiliVideoCommentsTool()]
