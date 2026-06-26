"""User behavior analysis tools.

These tools use Bangumi collection structure only: rates, collection status, subject tags,
and optional user comment fields when present. They do not infer private motives as facts.
"""
from __future__ import annotations

import asyncio
import html
import math
import re
from collections import Counter
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from ..comments.tool import EpisodeCommentsArgs, GetEpisodeCommentsTool
from ..bangumi.client import SUBJECT_TYPE, BangumiClient
from ..review.tool import AspectOpinion, CommentEvidence, _extract_aspect_opinions

_MAX_ITEMS = 1000
_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
_POS = ("喜欢", "好看", "神", "优秀", "佳作", "舒服", "治愈", "感动", "推荐", "有趣", "稳定", "精彩")
_NEG = ("不喜欢", "烂", "差", "崩", "无聊", "尬", "雷", "失望", "劝退", "一般", "拖", "难受")


class UserOpinionArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传用当前账号")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    limit: int = Field(200, ge=20, le=1000)


class OpinionSignal(BaseModel):
    label: str
    count: int
    examples: list[str] = Field(default_factory=list)


class UserOpinionResult(BaseModel):
    username: str
    subject_type: str
    analyzed: int
    comments_seen: int
    positive_tags: list[OpinionSignal] = Field(default_factory=list)
    negative_tags: list[OpinionSignal] = Field(default_factory=list)
    positive_comment_samples: list[str] = Field(default_factory=list)
    negative_comment_samples: list[str] = Field(default_factory=list)
    aspect_opinions: list[AspectOpinion] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class TasteCompareArgs(BaseModel):
    username: str | None = Field(None, description="目标用户；不传用当前账号")
    peer_username: str = Field(..., description="要比较同步率的 Bangumi 用户名")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"


class SharedRatingItem(BaseModel):
    id: int
    name: str
    user_rate: int
    peer_rate: int
    delta: int = 0
    image: str | None = None


class PeerAffinity(BaseModel):
    username: str
    rating_similarity: float
    own_space_similarity: float
    peer_space_similarity: float
    union_similarity: float
    common_rated: int
    own_rated: int
    peer_rated: int
    common_collections: int
    confidence: Literal["low", "medium", "high"] = "low"
    peer_weight: float = 0.0
    liked_together: list[SharedRatingItem] = Field(default_factory=list)
    disliked_together: list[SharedRatingItem] = Field(default_factory=list)
    biggest_disagreements: list[SharedRatingItem] = Field(default_factory=list)
    explanation: str = ""


class TasteCompareResult(BaseModel):
    username: str
    peer_username: str
    subject_type: str
    affinity: PeerAffinity
    caveats: list[str] = Field(default_factory=list)


class SyncRecommendArgs(BaseModel):
    username: str | None = Field(None, description="目标用户；不传用当前账号")
    peer_usernames: list[str] | None = Field(None, max_length=8, description="显式指定同好/朋友 Bangumi 用户名")
    auto_friends: bool = Field(False, description="是否从 Bangumi 好友页 best-effort 抓取 peer_usernames")
    max_auto_peers: int = Field(8, ge=1, le=20, description="自动好友最多取多少个")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    limit: int = Field(8, ge=1, le=20)


class SyncRecItem(BaseModel):
    id: int
    name: str
    peer_score: float
    liked_by: list[str] = Field(default_factory=list)
    affinity_score: float = 0.0
    reason: str = ""
    image: str | None = None


class SyncRecommendResult(BaseModel):
    username: str
    subject_type: str
    peers: list[str]
    overlap: dict[str, int] = Field(default_factory=dict)
    affinities: list[PeerAffinity] = Field(default_factory=list)
    items: list[SyncRecItem] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class FriendListArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传用当前账号")
    limit: int = Field(50, ge=1, le=200)


class FriendBrief(BaseModel):
    username: str
    nickname: str = ""
    url: str


class FriendListResult(BaseModel):
    username: str
    count: int
    friends: list[FriendBrief] = Field(default_factory=list)
    source_url: str
    caveats: list[str] = Field(default_factory=list)


class AbandonAnalysisArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传用当前账号")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    include_on_hold: bool = Field(True, description="是否同时分析搁置")
    limit: int = Field(20, ge=1, le=50)


class AbandonItem(BaseModel):
    id: int | None = None
    name: str
    status: str
    rate: int | None = None
    comment: str | None = None
    ep_status: int | None = None
    tags: list[str] = Field(default_factory=list)
    possible_reasons: list[str] = Field(default_factory=list)
    episode_discussion: list[str] = Field(default_factory=list)
    next_episode_discussion: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium"] = "low"
    image: str | None = None


class AbandonAnalysisResult(BaseModel):
    username: str
    subject_type: str
    count: int
    common_tags: list[OpinionSignal] = Field(default_factory=list)
    items: list[AbandonItem] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


def _comment_of(item: dict) -> str:
    for key in ("comment",):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _subject_name(item: dict) -> str:
    subj = item.get("subject") or {}
    return subj.get("name_cn") or subj.get("name") or f"subject {subj.get('id')}"


def _subject_image(item: dict) -> str | None:
    img = (item.get("subject") or {}).get("images") or {}
    return img.get("common") or img.get("medium") or img.get("grid")


def _tags(item: dict) -> list[str]:
    return [
        t.get("name")
        for t in ((item.get("subject") or {}).get("tags") or [])
        if isinstance(t, dict) and t.get("name")
    ][:12]


def _subject_id(item: dict) -> int | None:
    sid = (item.get("subject") or {}).get("id")
    return int(sid) if sid else None


def _rated_items(items: list[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for item in items:
        sid = _subject_id(item)
        rate = item.get("rate") or 0
        if sid and rate:
            out[sid] = item
    return out


def _collection_ids(items: list[dict]) -> set[int]:
    return {sid for item in items if (sid := _subject_id(item))}


def _rating_value(rate: int | float | None) -> float:
    """Map Bangumi 1-10 ratings to a centered vector coordinate.

    5.5 is neutral; high scores become positive, low scores negative. This lets
    cosine similarity express 同喜同悲 / 反向口味 instead of only overlap.
    """
    if not rate:
        return 0.0
    return (float(rate) - 5.5) / 4.5


def _cosine(a: dict[int, float], b: dict[int, float], keys: set[int]) -> float:
    if not keys:
        return 0.0
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(a.get(k, 0.0) ** 2 for k in keys))
    nb = math.sqrt(sum(b.get(k, 0.0) ** 2 for k in keys))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def _confidence(common_rated: int) -> str:
    if common_rated >= 40:
        return "high"
    if common_rated >= 12:
        return "medium"
    return "low"


def _confidence_factor(label: str) -> float:
    return {"high": 1.0, "medium": 0.72, "low": 0.35}.get(label, 0.35)


def _shared_item(own: dict, peer: dict) -> SharedRatingItem:
    sid = _subject_id(own) or _subject_id(peer) or 0
    own_rate = int(own.get("rate") or 0)
    peer_rate = int(peer.get("rate") or 0)
    return SharedRatingItem(
        id=sid,
        name=_subject_name(own) or _subject_name(peer),
        user_rate=own_rate,
        peer_rate=peer_rate,
        delta=abs(own_rate - peer_rate),
        image=_subject_image(own) or _subject_image(peer),
    )


def _build_affinity(peer_username: str, own_items: list[dict], peer_items: list[dict]) -> PeerAffinity:
    own_rated_items = _rated_items(own_items)
    peer_rated_items = _rated_items(peer_items)
    own_vec = {sid: _rating_value(item.get("rate")) for sid, item in own_rated_items.items()}
    peer_vec = {sid: _rating_value(item.get("rate")) for sid, item in peer_rated_items.items()}
    own_keys = set(own_vec)
    peer_keys = set(peer_vec)
    common = own_keys & peer_keys
    union = own_keys | peer_keys
    base = _cosine(own_vec, peer_vec, common)
    own_space = base * math.sqrt(len(common) / len(own_keys)) if own_keys and common else 0.0
    peer_space = base * math.sqrt(len(common) / len(peer_keys)) if peer_keys and common else 0.0
    union_space = base * (len(common) / len(union)) if union else 0.0
    confidence = _confidence(len(common))
    peer_weight = max(0.0, base) * _confidence_factor(confidence)

    shared = [_shared_item(own_rated_items[sid], peer_rated_items[sid]) for sid in common]
    liked = sorted(
        [x for x in shared if x.user_rate >= 8 and x.peer_rate >= 8],
        key=lambda x: (-x.user_rate - x.peer_rate, x.delta, x.name),
    )[:5]
    disliked = sorted(
        [x for x in shared if x.user_rate <= 5 and x.peer_rate <= 5],
        key=lambda x: (x.user_rate + x.peer_rate, x.delta, x.name),
    )[:5]
    disagreements = sorted(shared, key=lambda x: (-x.delta, -max(x.user_rate, x.peer_rate), x.name))[:5]
    own_collections = _collection_ids(own_items)
    peer_collections = _collection_ids(peer_items)
    common_collections = len(own_collections & peer_collections)

    if len(common) == 0:
        explanation = "没有共同评分，无法判断同步率。"
    elif base >= 0.65:
        explanation = "共同评分上的口味高度同步，适合作为强同好推荐源。"
    elif base >= 0.35:
        explanation = "共同评分上的口味有一定同步，可作为中等置信同好推荐源。"
    elif base > 0:
        explanation = "共同评分略偏同步，但样本或一致性不足，推荐时只能弱参考。"
    else:
        explanation = "共同评分不呈同步，推荐时不应给这个 peer 太高权重。"

    return PeerAffinity(
        username=peer_username,
        rating_similarity=round(base, 4),
        own_space_similarity=round(own_space, 4),
        peer_space_similarity=round(peer_space, 4),
        union_similarity=round(union_space, 4),
        common_rated=len(common),
        own_rated=len(own_keys),
        peer_rated=len(peer_keys),
        common_collections=common_collections,
        confidence=confidence,
        peer_weight=round(peer_weight, 4),
        liked_together=liked,
        disliked_together=disliked,
        biggest_disagreements=disagreements,
        explanation=explanation,
    )


def _sentiment(text: str) -> int:
    score = 0
    if any(k in text for k in _POS):
        score += 1
    if any(k in text for k in _NEG):
        score -= 1
    return score


async def _resolve_username_from_page(username_or_uid: str) -> str:
    if not username_or_uid.isdigit():
        return username_or_uid
    url = f"https://bgm.tv/user/{username_or_uid}"
    last: Exception | None = None
    async with httpx.AsyncClient(
        timeout=settings.http_timeout,
        headers={"User-Agent": _BROWSER_UA},
        follow_redirects=True,
    ) as c:
        for attempt in range(3):
            try:
                r = await c.get(url)
                if r.status_code in {500, 502, 503, 504}:
                    last = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                m = re.search(r"/user/([^/?#]+)", str(r.url))
                return m.group(1) if m else username_or_uid
            except httpx.TransportError as e:
                last = e
                await asyncio.sleep(0.5 * (attempt + 1))
    if last:
        raise last
    return username_or_uid


async def _username(client: BangumiClient, username: str | None) -> str:
    if username:
        return await _resolve_username_from_page(username)
    me = await client.get_me()
    return me.get("username") or str(me.get("id"))


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return html.unescape(value).strip()


def _parse_friend_list(page: str, limit: int) -> list[FriendBrief]:
    m = re.search(r'<ul id="memberUserList"[^>]*>(.*?)</ul>', page, flags=re.S | re.I)
    if not m:
        return []
    block = m.group(1)
    out: list[FriendBrief] = []
    seen: set[str] = set()
    for username, label in re.findall(r'<a href="/user/([^"/]+)" class="avatar"[^>]*>(.*?)</a>', block, flags=re.S | re.I):
        if username in seen:
            continue
        seen.add(username)
        out.append(FriendBrief(username=username, nickname=_clean_text(label), url=f"https://bgm.tv/user/{username}"))
        if len(out) >= limit:
            break
    return out


async def _fetch_friends(username: str, limit: int) -> tuple[list[FriendBrief], str]:
    url = f"https://bgm.tv/user/{username}/friends"
    last: Exception | None = None
    async with httpx.AsyncClient(
        timeout=settings.http_timeout,
        headers={"User-Agent": _BROWSER_UA},
        follow_redirects=True,
    ) as c:
        for attempt in range(3):
            try:
                r = await c.get(url)
                if r.status_code in {500, 502, 503, 504}:
                    last = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return _parse_friend_list(r.text, limit), str(r.url)
            except httpx.TransportError as e:
                last = e
                await asyncio.sleep(0.5 * (attempt + 1))
    assert last is not None
    raise last


class UserOpinionTool(Tool):
    name = "analyze_user_opinions"
    description = (
        "分析用户收藏中的评分与可见私评/短评，提炼喜欢/不喜欢的标签和代表样本。"
        "用于比 get_taste_profile 更细的推荐理由、避雷点分析。"
    )
    args_model = UserOpinionArgs
    result_model = UserOpinionResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: UserOpinionArgs) -> ToolResult[UserOpinionResult]:
        username = await _username(self.client, args.username)
        items = await self.client.get_all_user_collections(
            username, SUBJECT_TYPE[args.subject_type], collection_type=2, max_items=args.limit
        )
        pos_tags: Counter[str] = Counter()
        neg_tags: Counter[str] = Counter()
        pos_samples: list[str] = []
        neg_samples: list[str] = []
        all_comment_samples: list[str] = []
        comments_seen = 0
        for item in items:
            rate = item.get("rate") or 0
            comment = _comment_of(item)
            if comment:
                comments_seen += 1
                all_comment_samples.append(f"{_subject_name(item)}：{comment[:160]}")
            signal = _sentiment(comment)
            if rate >= 8 or signal > 0:
                pos_tags.update(_tags(item))
                if comment:
                    pos_samples.append(f"{_subject_name(item)}：{comment[:120]}")
            if (rate and rate <= 5) or signal < 0:
                neg_tags.update(_tags(item))
                if comment:
                    neg_samples.append(f"{_subject_name(item)}：{comment[:120]}")
        result = UserOpinionResult(
            username=username,
            subject_type=args.subject_type,
            analyzed=len(items),
            comments_seen=comments_seen,
            positive_tags=[OpinionSignal(label=k, count=v) for k, v in pos_tags.most_common(10)],
            negative_tags=[OpinionSignal(label=k, count=v) for k, v in neg_tags.most_common(10)],
            positive_comment_samples=pos_samples[:8],
            negative_comment_samples=neg_samples[:8],
            aspect_opinions=_extract_aspect_opinions([
                CommentEvidence(source="Bangumi 用户私评", samples=all_comment_samples[:80])
            ]),
            caveats=[
                "用户私评字段不一定公开或稳定返回；没有私评时主要依据评分。",
                "情感分析是关键词级弱信号，只能作为推荐解释辅助。",
            ],
        )
        return ToolResult(
            ok=True,
            data=result,
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
        )


class ListBangumiFriendsTool(Tool):
    name = "list_bangumi_friends"
    description = (
        "从 Bangumi 好友页 best-effort 抓取好友用户名列表。不是官方 v0 API，页面结构变化时会降级为空。"
        "用于自动同好推荐前的 peer 发现。"
    )
    args_model = FriendListArgs
    result_model = FriendListResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: FriendListArgs) -> ToolResult[FriendListResult]:
        username = await _username(self.client, args.username)
        try:
            friends, source_url = await _fetch_friends(username, args.limit)
        except (httpx.HTTPError, httpx.TransportError) as e:
            return ToolResult(ok=False, error=f"好友页抓取失败：{type(e).__name__}")
        return ToolResult(
            ok=True,
            data=FriendListResult(
                username=username,
                count=len(friends),
                friends=friends,
                source_url=source_url,
                caveats=["好友列表来自 Bangumi 网页解析，不是官方 v0 API；页面结构变化时可能失效。"],
            ),
            sources=[Citation(title=f"Bangumi @{username} 好友", url=source_url, source="bangumi")],
        )


class CompareUserTasteTool(Tool):
    name = "compare_user_taste"
    description = (
        "计算两个 Bangumi 用户在指定类型上的评分同步率/口味夹角：共同评分余弦、个人空间、对方空间、并集空间，"
        "并返回共同高分、共同低分和最大分歧。用于解释好友/同好是否适合作为推荐来源。"
    )
    args_model = TasteCompareArgs
    result_model = TasteCompareResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: TasteCompareArgs) -> ToolResult[TasteCompareResult]:
        username = await _username(self.client, args.username)
        stype = SUBJECT_TYPE[args.subject_type]
        own = await self.client.get_all_user_collections(username, stype, None, max_items=_MAX_ITEMS)
        peer = await self.client.get_all_user_collections(args.peer_username, stype, None, max_items=_MAX_ITEMS)
        affinity = _build_affinity(args.peer_username, own, peer)
        return ToolResult(
            ok=True,
            data=TasteCompareResult(
                username=username,
                peer_username=args.peer_username,
                subject_type=args.subject_type,
                affinity=affinity,
                caveats=[
                    "同步率基于公开/授权收藏评分；私有收藏不可见时会降低置信度。",
                    "评分向量以 5.5 为中性点，余弦越高代表共同评分越同喜同悲；负值代表口味方向相反。",
                ],
            ),
            sources=[
                Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi"),
                Citation(title=f"Bangumi @{args.peer_username}", url=f"https://bgm.tv/user/{args.peer_username}", source="bangumi"),
            ],
        )


class SyncRecommendTool(Tool):
    name = "sync_user_recommendations"
    description = (
        "基于同好/朋友做同步率推荐：先计算评分同步率，再取高同步 peer 的高分且目标用户未看作品。"
        "可显式提供 peer_usernames，也可 auto_friends=true 从 Bangumi 好友页 best-effort 解析。"
    )
    args_model = SyncRecommendArgs
    result_model = SyncRecommendResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: SyncRecommendArgs) -> ToolResult[SyncRecommendResult]:
        username = await _username(self.client, args.username)
        peer_usernames = list(args.peer_usernames or [])
        caveats: list[str] = []
        if args.auto_friends and not peer_usernames:
            try:
                friends, _source_url = await _fetch_friends(username, args.max_auto_peers)
                peer_usernames = [f.username for f in friends[: args.max_auto_peers]]
                caveats.append("peer_usernames 来自 Bangumi 好友页 best-effort 解析。")
            except (httpx.HTTPError, httpx.TransportError):
                caveats.append("自动好友页解析失败；请显式提供 peer_usernames。")
        if not peer_usernames:
            return ToolResult(
                ok=True,
                data=SyncRecommendResult(
                    username=username,
                    subject_type=args.subject_type,
                    peers=[],
                    items=[],
                    caveats=caveats + ["没有可用 peer；请提供 peer_usernames 或开启 auto_friends。"],
                ),
            )
        stype = SUBJECT_TYPE[args.subject_type]
        own = await self.client.get_all_user_collections(username, stype, None, max_items=_MAX_ITEMS)
        own_seen = {i.get("subject", {}).get("id") for i in own if i.get("subject", {}).get("id")}
        candidates: dict[int, dict] = {}
        overlap: dict[str, int] = {}
        affinities: list[PeerAffinity] = []
        for peer in peer_usernames:
            peer_items = await self.client.get_all_user_collections(peer, stype, None, max_items=_MAX_ITEMS)
            affinity = _build_affinity(peer, own, peer_items)
            affinities.append(affinity)
            overlap[peer] = affinity.common_collections
            if affinity.peer_weight <= 0:
                continue
            for item in peer_items:
                rate = item.get("rate") or 0
                subj = item.get("subject") or {}
                sid = subj.get("id")
                if not sid or sid in own_seen or rate < 8 or item.get("type") != 2:
                    continue
                c = candidates.setdefault(
                    sid,
                    {
                        "name": subj.get("name_cn") or subj.get("name"),
                        "score": 0.0,
                        "affinity_score": 0.0,
                        "liked_by": [],
                        "peer_weights": [],
                        "image": _subject_image(item),
                    },
                )
                preference = max((rate - 5.5) / 4.5, 0.1)
                boost = affinity.peer_weight * preference
                c["score"] += boost
                c["affinity_score"] += affinity.peer_weight
                c["liked_by"].append(peer)
                c["peer_weights"].append((peer, affinity.peer_weight, rate))
        affinities.sort(key=lambda x: (-x.peer_weight, -x.common_rated, x.username))
        ranked = sorted(candidates.items(), key=lambda kv: (-kv[1]["score"], -len(kv[1]["liked_by"])))[: args.limit]
        items = [
            SyncRecItem(
                id=sid,
                name=c["name"],
                peer_score=round(c["score"], 3),
                liked_by=c["liked_by"],
                affinity_score=round(c["affinity_score"], 3),
                reason=(
                    f"{len(c['liked_by'])} 个同步率 peer 给高分；"
                    + "、".join(f"{p}(w={w:.2f}, {r}分)" for p, w, r in c["peer_weights"][:3])
                ),
                image=c.get("image"),
            )
            for sid, c in ranked
        ]
        return ToolResult(
            ok=True,
            data=SyncRecommendResult(
                username=username,
                subject_type=args.subject_type,
                peers=peer_usernames,
                overlap=overlap,
                affinities=affinities[: args.max_auto_peers if args.auto_friends else len(affinities)],
                items=items,
                caveats=caveats + [
                    "同步推荐基于公开/授权收藏；好友页解析不是官方 v0 API。",
                    "peer_weight 来自共同评分余弦相似度和样本置信度；共同评分太少时会被自动降权。",
                ],
            ),
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")]
            + [Citation(title=i.name, url=f"https://bgm.tv/subject/{i.id}", source="bangumi", image=i.image) for i in items[:5]],
        )


class AbandonAnalysisTool(Tool):
    name = "analyze_abandoned_subjects"
    description = (
        "分析用户搁置/抛弃作品的标签、评分和可见评论，给出低置信度弃坑模式。"
        "不能断言用户弃坑原因；只能说可能原因和证据。"
    )
    args_model = AbandonAnalysisArgs
    result_model = AbandonAnalysisResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client
        self.episode_comments = GetEpisodeCommentsTool(client)

    async def _episode_context(self, subject_id: int, ep_status: int | None) -> tuple[list[str], list[str]]:
        if not ep_status or ep_status <= 0:
            return [], []
        try:
            raw = await self.client.get_episodes(subject_id, ep_type=0, limit=200)
        except Exception:  # noqa: BLE001
            return [], []
        eps = raw.get("data") or []
        cur = next((e for e in eps if int(e.get("sort") or e.get("ep") or 0) == ep_status), None)
        nxt = next((e for e in eps if int(e.get("sort") or e.get("ep") or 0) == ep_status + 1), None)

        async def comments(ep: dict | None) -> list[str]:
            if not ep or not ep.get("id"):
                return []
            res = await self.episode_comments.run(
                EpisodeCommentsArgs(
                    ep_id=ep["id"],
                    subject_id=subject_id,
                    episode_sort=ep.get("sort") or ep.get("ep"),
                    max_episode_sort=ep_status + 1,
                    query="弃坑 节奏 作画 剧情 无聊 失望",
                    limit=4,
                )
            )
            if res.ok and res.data and not res.data.blocked_by_spoiler:
                return res.data.comments
            return []

        return await comments(cur), await comments(nxt)

    async def run(self, args: AbandonAnalysisArgs) -> ToolResult[AbandonAnalysisResult]:
        username = await _username(self.client, args.username)
        stype = SUBJECT_TYPE[args.subject_type]
        status_types = [5] + ([4] if args.include_on_hold else [])
        all_items: list[tuple[str, dict]] = []
        for ctype in status_types:
            label = "抛弃" if ctype == 5 else "搁置"
            rows = await self.client.get_all_user_collections(username, stype, collection_type=ctype, max_items=args.limit)
            all_items.extend((label, r) for r in rows)
        tag_counter: Counter[str] = Counter()
        out: list[AbandonItem] = []
        for status, item in all_items[: args.limit]:
            subj = item.get("subject") or {}
            sid = subj.get("id")
            tags = _tags(item)
            tag_counter.update(tags)
            comment = _comment_of(item)
            ep_status = item.get("ep_status") if isinstance(item.get("ep_status"), int) else None
            ep_discussion, next_discussion = ([], [])
            if sid and args.subject_type == "anime" and ep_status:
                ep_discussion, next_discussion = await self._episode_context(sid, ep_status)
            reasons: list[str] = []
            if comment:
                if _sentiment(comment) < 0:
                    reasons.append("用户评论中有负向词")
                reasons.append("存在用户评论，可优先以评论解释")
            if item.get("rate") and item.get("rate") <= 5:
                reasons.append("用户评分较低")
            if ep_status:
                reasons.append(f"用户进度停在第 {ep_status} 集附近，可结合该集/下一集讨论判断")
            if ep_discussion or next_discussion:
                reasons.append("已补充弃坑节点附近的 Bangumi 分集讨论样本")
            if not reasons:
                reasons.append("仅能确认收藏状态，不能推断具体原因")
            out.append(
                AbandonItem(
                    id=sid,
                    name=subj.get("name_cn") or subj.get("name") or "",
                    status=status,
                    rate=item.get("rate") or None,
                    comment=comment or None,
                    ep_status=ep_status,
                    tags=tags,
                    possible_reasons=reasons,
                    episode_discussion=ep_discussion,
                    next_episode_discussion=next_discussion,
                    confidence="medium" if comment or (item.get("rate") and item.get("rate") <= 5) else "low",
                    image=_subject_image(item),
                )
            )
        return ToolResult(
            ok=True,
            data=AbandonAnalysisResult(
                username=username,
                subject_type=args.subject_type,
                count=len(out),
                common_tags=[OpinionSignal(label=k, count=v) for k, v in tag_counter.most_common(10)],
                items=out,
                caveats=[
                    "抛弃/搁置不是原因标签；没有用户评论时只能给弱推测。",
                    "需要更细的弃坑节点分析时，应结合用户看到第几集和该集前后的分集讨论。",
                ],
            ),
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")]
            + [
                Citation(title=i.name, url=f"https://bgm.tv/subject/{i.id}", source="bangumi", image=i.image)
                for i in out[:5]
                if i.id is not None
            ],
        )


def build_user_analysis_tools(client: BangumiClient) -> list[Tool]:
    return [
        UserOpinionTool(client),
        ListBangumiFriendsTool(client),
        CompareUserTasteTool(client),
        SyncRecommendTool(client),
        AbandonAnalysisTool(client),
    ]
