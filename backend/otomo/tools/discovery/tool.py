"""发现 / 预测工具（收口早期 backlog）：评分预测 + 萌点检索 + 分集口碑雷达 + 全站热门。

前三个复用现有 Bangumi client 方法；全站热门走 next.bgm.tv 的 p1 端点（网页版同源数据）。
"""
from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from ...profile import compute_taste_profile
from .._cache import acached
from .._concurrency import gather_limited
from ..bangumi.client import SUBJECT_TYPE, BangumiClient
from ..comments.tool import EpisodeCommentsArgs, GetEpisodeCommentsTool

_SUBJECT_T = Literal["anime", "book", "music", "game", "real"]


async def _username(client: BangumiClient, username: str | None) -> str | None:
    if username:
        return username
    try:
        me = await client.get_me()
    except Exception:  # noqa: BLE001
        return None
    return me.get("username") or str(me.get("id")) or None


def _tags_of(item: dict) -> set[str]:
    return {
        t.get("name") for t in ((item.get("subject") or {}).get("tags") or [])
        if isinstance(t, dict) and t.get("name")
    }


def _subject_name(item: dict) -> str:
    subj = item.get("subject") or {}
    return subj.get("name_cn") or subj.get("name") or f"subject {subj.get('id')}"


# --------------------------------------------------------------------------- #
# 1) 评分预测
# --------------------------------------------------------------------------- #
class PredictRatingArgs(BaseModel):
    subject_id: int = Field(..., description="Bangumi 条目 ID")
    username: str | None = Field(None, description="不传用当前 token 账号")


class RatingPrediction(BaseModel):
    subject_id: int
    title: str
    predicted_rating: float
    global_score: float | None = None
    user_avg: float | None = None
    confidence: Literal["low", "medium", "high"] = "low"
    matched_tags: list[str] = Field(default_factory=list)
    similar_works: list[dict] = Field(default_factory=list)  # 协同依据：你给相似已看作品的实际评分
    rationale: str = ""
    caveats: list[str] = Field(default_factory=list)


class PredictMyRatingTool(Tool):
    name = "predict_my_rating"
    description = (
        "预测当前用户会给某作品打几分：结合口味画像标签匹配、用户打分严格度和全站评分。"
        "用于『我会喜欢这部吗/估个分/值不值得看』。是个性化估计，非真实评分。"
    )
    args_model = PredictRatingArgs
    result_model = RatingPrediction

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: PredictRatingArgs) -> ToolResult[RatingPrediction]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error="未提供 username 且无法获取当前账号（需要 BANGUMI_TOKEN）")
        detail = await self.client.get_subject(args.subject_id)
        stype = detail.get("type") or 2
        title = detail.get("name_cn") or detail.get("name") or f"subject {args.subject_id}"
        subj_tags = [t.get("name") for t in (detail.get("tags") or []) if t.get("name")][:15]
        global_score = (detail.get("rating") or {}).get("score")

        items = await self.client.get_all_user_collections(username, stype, collection_type=2, max_items=1000)
        rated = [it for it in items if it.get("rate")]
        user_avg = sum(int(it["rate"]) for it in rated) / len(rated) if rated else None
        target_tags = set(subj_tags)

        # 协同：找你看过、与目标标签相似(Jaccard)的作品，用你的真实评分加权——比纯画像更准、可解释。
        sims: list[tuple[float, int, str]] = []
        for it in rated:
            it_tags = _tags_of(it)
            union = target_tags | it_tags
            if not union:
                continue
            jac = len(target_tags & it_tags) / len(union)
            if jac >= 0.12:
                sims.append((round(jac, 3), int(it["rate"]), _subject_name(it)))
        sims.sort(key=lambda x: -x[0])
        top = sims[:6]
        collab = (sum(j * r for j, r, _ in top) / sum(j for j, _, _ in top)) if top else None

        profile = compute_taste_profile(username, items)
        user_tags = {t["tag"]: float(t["weight"]) for t in profile.top_tags}
        maxw = max(user_tags.values()) if user_tags else 1.0
        matched = [t for t in subj_tags if t in user_tags]
        base = global_score if global_score else 7.0

        if collab is not None:  # 协同为主、全站为辅
            predicted = 0.6 * collab + 0.4 * base
            conf = "high" if len(top) >= 4 else "medium"
            rationale = (
                f"你给相似的《{top[0][2]}》打了 {top[0][1]} 分等 {len(top)} 部相似作品（协同预测主依据），"
                f"全站 {global_score or '暂无'}"
            )
        else:  # 无相似已评作品 → 降级到画像 + 严格度
            severity = (user_avg - 7.2) if user_avg is not None else 0.0
            affinity = sum(user_tags.get(t, 0.0) for t in subj_tags) / maxw
            predicted = base + min(affinity * 0.35, 1.2) - 0.4 + severity * 0.35
            conf = "medium" if matched else "low"
            rationale = (
                f"没找到你看过的相似作品，按画像估：全站 {global_score or '暂无'}，"
                f"你均分 {round(user_avg, 1) if user_avg else '未知'}，命中口味标签 {len(matched)} 个"
            )
        predicted = round(max(1.0, min(10.0, predicted)), 1)
        return ToolResult(
            ok=True,
            data=RatingPrediction(
                subject_id=args.subject_id, title=title, predicted_rating=predicted,
                global_score=global_score, user_avg=round(user_avg, 2) if user_avg else None,
                confidence=conf, matched_tags=matched[:8],
                similar_works=[{"name": n, "your_rate": r, "similarity": j} for j, r, n in top],
                rationale=rationale,
                caveats=["预测是个性化估计，不代表真实观感；相似作品越多越准。"],
            ),
            sources=[Citation(title=f"Bangumi — {title}", url=f"https://bgm.tv/subject/{args.subject_id}", source="bangumi")],
        )


# --------------------------------------------------------------------------- #
# 2) 萌点检索 / 复杂多维筛选
# --------------------------------------------------------------------------- #
class TraitSearchArgs(BaseModel):
    tags: list[str] = Field(..., min_length=1, max_length=8, description="萌点/题材标签组合（取交集），如 ['百合','废萌','芳文社']")
    exclude_tags: list[str] = Field(default_factory=list, max_length=8, description="排除含这些标签的作品，如 ['后宫','致郁']")
    subject_type: _SUBJECT_T = "anime"
    min_score: float = Field(0.0, ge=0.0, le=10.0, description="最低 Bangumi 评分")
    year_from: int | None = Field(None, description="起始年份（含）")
    year_to: int | None = Field(None, description="结束年份（含）")
    sort: Literal["rank", "heat", "score"] = Field("rank", description="排序：rank 综合排名 / heat 热度 / score 评分")
    limit: int = Field(15, ge=1, le=30)


class TraitItem(BaseModel):
    id: int
    name: str
    score: float | None = None
    rank: int | None = None
    date: str | None = None
    image: str | None = None


class TraitSearchResult(BaseModel):
    tags: list[str]
    count: int
    items: list[TraitItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SearchByTraitsTool(Tool):
    name = "search_by_traits"
    description = (
        "按萌点/题材标签组合 + 评分/年份做多维筛选检索（标签取交集）。"
        "用于『找 百合+废萌+芳文社 的高分番 / 2020 年后 治愈+音乐 的作品』这类精确筛选。"
    )
    args_model = TraitSearchArgs
    result_model = TraitSearchResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: TraitSearchArgs) -> ToolResult[TraitSearchResult]:
        air_date: list[str] | None = None
        if args.year_from or args.year_to:
            lo = f">={args.year_from}-01-01" if args.year_from else ">=1900-01-01"
            hi = f"<{(args.year_to + 1)}-01-01" if args.year_to else "<2100-01-01"
            air_date = [lo, hi]
        raw = await self.client.search_subjects(
            "", SUBJECT_TYPE[args.subject_type], sort=args.sort, limit=min(args.limit * 3, 50),
            tags=args.tags, air_date=air_date,
        )
        exclude = {e.strip() for e in args.exclude_tags if e.strip()}
        items: list[TraitItem] = []
        for s in (raw.get("data") or []):
            if not s.get("id"):
                continue
            s_tags = {
                (t.get("name") if isinstance(t, dict) else str(t))
                for t in (s.get("tags") or s.get("meta_tags") or [])
            }
            if exclude and (exclude & s_tags):
                continue
            score = (s.get("rating") or {}).get("score") or s.get("score")
            if args.min_score and (score or 0) < args.min_score:
                continue
            img = (s.get("images") or {}).get("common") or (s.get("images") or {}).get("grid")
            items.append(TraitItem(
                id=s["id"], name=s.get("name_cn") or s.get("name") or "",
                score=score, rank=(s.get("rating") or {}).get("rank") or s.get("rank"),
                date=s.get("date"), image=img,
            ))
            if len(items) >= args.limit:
                break
        notes = [f"标签交集 {args.tags}，按 {args.sort} 排序"]
        if args.min_score:
            notes.append(f"已过滤评分 < {args.min_score}")
        if exclude:
            notes.append(f"已排除标签：{sorted(exclude)}")
        return ToolResult(
            ok=True,
            data=TraitSearchResult(tags=args.tags, count=len(items), items=items, notes=notes),
            sources=[Citation(title=i.name, url=f"https://bgm.tv/subject/{i.id}", source="bangumi", image=i.image) for i in items[:5]],
        )


# --------------------------------------------------------------------------- #
# 3) 分集口碑雷达
# --------------------------------------------------------------------------- #
class EpisodeRadarArgs(BaseModel):
    subject_id: int = Field(..., description="Bangumi 条目 ID")
    progress_episode: int | None = Field(None, description="只看到第 N 集；防剧透，只返回 sort≤N 的集")
    top: int = Field(5, ge=1, le=10, description="返回讨论数最高的几集")
    with_summary: bool = Field(False, description="是否对 top 高能集抓讨论样本做质性摘要（稍慢，默认关）")


class EpisodePoint(BaseModel):
    sort: float
    ep: int | None = None
    ep_id: int | None = None
    name: str = ""
    comments: int = 0
    airdate: str | None = None
    discussion: list[str] = Field(default_factory=list)  # 质性摘要：讨论样本（with_summary 时）


class EpisodeRadarResult(BaseModel):
    subject_id: int
    total: int
    curve: list[EpisodePoint] = Field(default_factory=list)   # 讨论数曲线（按 sort）
    peaks: list[EpisodePoint] = Field(default_factory=list)    # 高能集（讨论数 top）
    filtered_by_progress: int | None = None
    notes: list[str] = Field(default_factory=list)


class EpisodeBuzzRadarTool(Tool):
    name = "episode_buzz_radar"
    description = (
        "分集口碑雷达：取作品正片各集的讨论数曲线，找出讨论最热的『高能集』。"
        "用于『这部番哪几集最热闹/名场面在第几集/口碑高峰』。"
        "用户给了进度就只看到该集，防剧透。"
    )
    args_model = EpisodeRadarArgs
    result_model = EpisodeRadarResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client
        self.episode_comments = GetEpisodeCommentsTool(client)

    async def run(self, args: EpisodeRadarArgs) -> ToolResult[EpisodeRadarResult]:
        raw = await self.client.get_episodes(args.subject_id, ep_type=0, limit=200)
        rows = raw.get("data") or []
        points: list[EpisodePoint] = []
        for e in rows:
            sort = e.get("sort")
            if sort is None:
                continue
            points.append(EpisodePoint(
                sort=float(sort), ep=e.get("ep"), ep_id=e.get("id"),
                name=e.get("name_cn") or e.get("name") or "",
                comments=int(e.get("comment") or 0), airdate=e.get("airdate") or None,
            ))
        filtered = None
        if args.progress_episode is not None:
            before = len(points)
            points = [p for p in points if p.sort <= args.progress_episode]
            filtered = before - len(points)
        points.sort(key=lambda p: p.sort)
        peaks = sorted(points, key=lambda p: -p.comments)[: args.top]
        notes = ["讨论数是热度/话题度信号，不等于质量；高能集可能含剧透。"]
        if filtered:
            notes.append(f"已按进度第 {args.progress_episode} 集过滤掉 {filtered} 个后续集。")
        if args.with_summary:  # 对 top 2 高能集抓讨论样本（防剧透：max_episode_sort 受进度约束）
            for p in peaks[:2]:
                if not p.ep_id:
                    continue
                try:
                    res = await self.episode_comments.run(EpisodeCommentsArgs(
                        ep_id=p.ep_id, subject_id=args.subject_id, episode_sort=p.sort,
                        max_episode_sort=args.progress_episode, limit=4,
                    ))
                    if res.ok and res.data and not getattr(res.data, "blocked_by_spoiler", False):
                        p.discussion = list(res.data.comments)[:4]
                except Exception:  # noqa: BLE001
                    pass
        return ToolResult(
            ok=True,
            data=EpisodeRadarResult(
                subject_id=args.subject_id, total=raw.get("total") or len(rows),
                curve=points, peaks=peaks, filtered_by_progress=filtered, notes=notes,
            ),
            sources=[Citation(title=f"subject {args.subject_id} · 分集热度", url=f"https://bgm.tv/subject/{args.subject_id}/ep", source="bangumi")],
        )


_TRENDING_API = "https://next.bgm.tv/p1/trending/subjects"


class TrendingArgs(BaseModel):
    subject_type: _SUBJECT_T = Field("anime", description="条目类型；全站热门以 anime 数据最全")
    limit: int = Field(12, ge=1, le=24)
    offset: int = Field(0, ge=0, le=200)


class TrendingItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    name_cn: str = ""
    score: float | None = None
    rank: int | None = None
    collects: int | None = None
    meta_tags: list[str] = Field(default_factory=list)
    info: str = ""
    image: str | None = None
    url: str = ""


class TrendingResult(BaseModel):
    subject_type: str
    count: int = 0
    items: list[TrendingItem] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


@acached(ttl=settings.cache_ttl * 12)  # 热门榜变化慢，1 小时级缓存足够
async def _fetch_trending(type_id: int, limit: int, offset: int) -> list[dict[str, Any]]:
    async def fetch() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.bangumi_user_agent},
        ) as client:
            res = await client.get(_TRENDING_API, params={"type": type_id, "limit": limit, "offset": offset})
            res.raise_for_status()
            payload = res.json()
        rows = payload.get("data") if isinstance(payload, dict) else payload
        return [x for x in rows or [] if isinstance(x, dict)]

    result = await gather_limited([fetch()], host="bangumi")
    first = result[0]
    if isinstance(first, BaseException):
        raise first
    return first


class GetTrendingSubjectsTool(Tool):
    name = "get_trending_subjects"
    description = (
        "查询 Bangumi 全站当前热门条目（与网页版『热门』同源）。用于『最近什么番火 / 全站热门 / 大家都在看什么』。"
        "这是站点私有端点数据，结构可能变动；结果带缓存。"
    )
    args_model = TrendingArgs
    result_model = TrendingResult

    async def run(self, args: TrendingArgs) -> ToolResult[TrendingResult]:
        type_id = SUBJECT_TYPE[args.subject_type]
        try:
            rows = await _fetch_trending(type_id, args.limit, args.offset)
        except Exception as e:  # noqa: BLE001
            return ToolResult(ok=False, error=f"热门数据暂不可用（非正式端点）：{type(e).__name__}")
        items: list[TrendingItem] = []
        for row in rows:
            subj = row.get("subject") if isinstance(row.get("subject"), dict) else row
            sid = subj.get("id")
            if not sid:
                continue
            rating = subj.get("rating") or {}
            images = subj.get("images") or {}
            items.append(
                TrendingItem(
                    id=int(sid),
                    name=str(subj.get("name") or ""),
                    name_cn=str(subj.get("nameCN") or subj.get("name_cn") or subj.get("name") or ""),
                    score=rating.get("score"),
                    rank=rating.get("rank"),
                    collects=row.get("count") or subj.get("collects"),
                    meta_tags=[str(t) for t in (subj.get("metaTags") or [])][:8],
                    info=str(subj.get("info") or "")[:120],
                    image=images.get("common") or images.get("medium") or images.get("large"),
                    url=f"https://bgm.tv/subject/{sid}",
                )
            )
        result = TrendingResult(
            subject_type=args.subject_type,
            count=len(items),
            items=items,
            caveats=[
                "数据来自 next.bgm.tv 的非正式 trending 端点（网页版同源），结构可能随站点更新变动。",
                "热门反映当前收藏/讨论热度，不等于质量评价；结果有 1 小时级缓存。",
            ],
        )
        sources = [
            Citation(title=x.name_cn or x.name, url=x.url, source="bangumi", image=x.image)
            for x in items[:6]
        ]
        return ToolResult(ok=True, data=result, sources=sources)


_ANILIST_GQL = "https://graphql.anilist.co"
_BIRTHDAY_QUERY = """
{ Page(perPage: %d) { characters(isBirthday: true, sort: FAVOURITES_DESC) {
    name { full native } image { medium } favourites siteUrl
    dateOfBirth { month day }
    media(perPage: 1, sort: POPULARITY_DESC) { nodes { title { native romaji } } }
} } }
"""


class BirthdayArgs(BaseModel):
    limit: int = Field(10, ge=1, le=20)
    moegirl_limit: int = Field(24, ge=0, le=60, description="萌娘完整名单最多列多少位；0 关闭萌娘源")


class BirthdayCharacter(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    name_native: str = ""
    month: int | None = None
    day: int | None = None
    favourites: int | None = None
    from_media: str = ""
    image: str | None = None
    anilist_url: str = ""
    bangumi_search_url: str = ""


class MoegirlBirthdayEntry(BaseModel):
    name: str
    from_media: str = ""
    url: str


class BirthdayResult(BaseModel):
    date: str
    count: int = 0
    characters: list[BirthdayCharacter] = Field(default_factory=list)
    moegirl_entries: list[MoegirlBirthdayEntry] = Field(default_factory=list)
    moegirl_category_url: str = ""
    caveats: list[str] = Field(default_factory=list)


@acached(ttl=3600.0)
async def _fetch_birthdays(limit: int) -> list[dict[str, Any]]:
    async def fetch() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
            res = await client.post(_ANILIST_GQL, json={"query": _BIRTHDAY_QUERY % limit})
            res.raise_for_status()
            payload = res.json()
        return ((payload.get("data") or {}).get("Page") or {}).get("characters") or []

    result = await gather_limited([fetch()], host="anilist")
    first = result[0]
    if isinstance(first, BaseException):
        raise first
    return first


# 萌娘「Category:M月D日」是生日分类（2026-07-05 实测：成员含游戏角色/声优/画师，
# 覆盖比 AniList 广）。API 的 categorymembers 被站方禁用（action-notallowed），
# 走 HTML 分类页；只取成员标题+链接（导航性元数据），不抓正文——ai-train 红线不碰。
import re as _re

_MOEGIRL_CAT_URL = "https://zh.moegirl.org.cn/Category:{mon}月{day}日"
_MOEGIRL_MEMBER_RE = _re.compile(r'<a href="/([^"?#]+)" title="([^"]+)">')


@acached(ttl=3600.0)
async def _fetch_moegirl_birthdays(mon: int, day: int, limit: int) -> list[dict[str, str]]:
    url = _MOEGIRL_CAT_URL.format(mon=mon, day=day)

    async def fetch() -> str:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.moegirl_user_agent},
            follow_redirects=True,
        ) as client:
            res = await client.get(url)
            res.raise_for_status()
            return res.text

    result = await gather_limited([fetch()], host="moegirl")
    html = result[0]
    if isinstance(html, BaseException):
        raise html
    anchor = html.find("mw-category")
    if anchor < 0:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for href, title in _MOEGIRL_MEMBER_RE.findall(html[anchor:]):
        if title.startswith(("Category:", "分类:", "萌娘百科:", "User:", "Template:")):
            continue
        if title in seen:
            continue
        seen.add(title)
        # 萌娘条目命名惯例「作品:角色」→ 拆出作品归属；无冒号的多为真人（声优/创作者）
        media, _, char = title.partition(":")
        name, from_media = (char, media) if char else (title, "")
        out.append({"name": name, "from_media": from_media, "url": f"https://zh.moegirl.org.cn/{href}"})
        if len(out) >= limit:
            break
    return out


class GetCharacterBirthdaysTool(Tool):
    name = "get_character_birthdays"
    description = (
        "查询今天过生日的 ACGN 角色（AniList isBirthday，按全站人气排序）。"
        "用于『今天是谁的生日 / 生日角色 / 今天有什么角色过生日』。角色名以日文原名为准，可回锚 Bangumi 检索。"
    )
    args_model = BirthdayArgs
    result_model = BirthdayResult

    async def run(self, args: BirthdayArgs) -> ToolResult[BirthdayResult]:
        from datetime import date as _date
        from urllib.parse import quote as _quote

        today = _date.today()
        rows: list[dict[str, Any]] = []
        moegirl_entries: list[MoegirlBirthdayEntry] = []
        anilist_err = ""
        try:
            rows = await _fetch_birthdays(args.limit)
        except Exception as e:  # noqa: BLE001
            anilist_err = type(e).__name__
        if args.moegirl_limit > 0:
            try:
                raw_entries = await _fetch_moegirl_birthdays(today.month, today.day, args.moegirl_limit)
                moegirl_entries = [MoegirlBirthdayEntry(**x) for x in raw_entries]
            except Exception:  # noqa: BLE001
                pass
        if not rows and not moegirl_entries:
            return ToolResult(ok=False, error=f"生日数据暂不可用（AniList {anilist_err or 'empty'} / 萌娘不可达）")
        characters: list[BirthdayCharacter] = []
        for row in rows:
            name = row.get("name") or {}
            native = str(name.get("native") or "")
            dob = row.get("dateOfBirth") or {}
            media_nodes = ((row.get("media") or {}).get("nodes")) or []
            media_title = ""
            if media_nodes:
                title = media_nodes[0].get("title") or {}
                media_title = str(title.get("native") or title.get("romaji") or "")
            anchor = native or str(name.get("full") or "")
            characters.append(
                BirthdayCharacter(
                    name=str(name.get("full") or native or "未知角色"),
                    name_native=native,
                    month=dob.get("month"),
                    day=dob.get("day"),
                    favourites=row.get("favourites"),
                    from_media=media_title,
                    image=(row.get("image") or {}).get("medium"),
                    anilist_url=str(row.get("siteUrl") or ""),
                    bangumi_search_url=f"https://bgm.tv/mono_search/{_quote(anchor)}?cat=crt" if anchor else "",
                )
            )
        result = BirthdayResult(
            date=today.isoformat(),
            count=len(characters) + len(moegirl_entries),
            characters=characters,
            moegirl_entries=moegirl_entries,
            moegirl_category_url=_MOEGIRL_CAT_URL.format(mon=today.month, day=today.day),
            caveats=[
                "人气图卡来自 AniList（动画侧，按收藏人气排序）；完整名单来自萌娘百科生日分类（含游戏角色/声优/创作者，无排序）。",
                "两源收录口径不同，同一角色可能重复出现；角色详情可用条目链接核对。",
            ],
        )
        sources = [
            Citation(title=f"{c.name_native or c.name}（{c.from_media}）", url=c.anilist_url or c.bangumi_search_url, source="anilist", image=c.image)
            for c in characters[:5]
        ]
        if moegirl_entries:
            sources.append(Citation(title=f"萌娘百科 — {today.month}月{today.day}日生日分类", url=result.moegirl_category_url, source="moegirl"))
        return ToolResult(ok=True, data=result, sources=sources)


class CompareSubjectsArgs(BaseModel):
    subject_ids: list[int] = Field(default_factory=list, description="要对比的 Bangumi subject id，2~3 个")
    titles: list[str] = Field(default_factory=list, description="subject_ids 为空时按标题解析，2~3 个")
    subject_type: _SUBJECT_T = "anime"


class CompareColumn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    name_cn: str = ""
    date: str = ""
    eps: int | None = None
    score: float | None = None
    rank: int | None = None
    rating_total: int | None = None
    doing: int | None = None
    collect: int | None = None
    dropped: int | None = None
    top_tags: list[str] = Field(default_factory=list)
    unique_tags: list[str] = Field(default_factory=list)
    image: str | None = None
    url: str = ""


class CompareSubjectsResult(BaseModel):
    columns: list[CompareColumn] = Field(default_factory=list)
    shared_tags: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class CompareSubjectsTool(Tool):
    name = "compare_subjects"
    description = (
        "并排对比 2~3 部作品的硬指标：评分/排名/评分人数/在看收视/弃番数/话数/年份/标签异同。"
        "用于『A 和 B 哪个好看 / 对比一下 X 和 Y』。主观取舍仍需结合 review_subject 的口碑证据。"
    )
    args_model = CompareSubjectsArgs
    result_model = CompareSubjectsResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def _resolve_ids(self, args: CompareSubjectsArgs) -> list[int]:
        if args.subject_ids:
            return args.subject_ids[:3]
        stype = SUBJECT_TYPE[args.subject_type]
        ids: list[int] = []
        for title in args.titles[:3]:
            try:
                raw = await self.client.search_subjects(title, stype, limit=3)
                rows = raw.get("data") or []
                if rows:
                    ids.append(int(rows[0]["id"]))
            except Exception:  # noqa: BLE001
                continue
        return ids

    async def run(self, args: CompareSubjectsArgs) -> ToolResult[CompareSubjectsResult]:
        ids = await self._resolve_ids(args)
        if len(ids) < 2:
            return ToolResult(ok=False, error="需要至少 2 个可解析的作品（subject_ids 或 titles）")
        raws = await gather_limited([self.client.get_subject(sid) for sid in ids], host="bangumi")
        columns: list[CompareColumn] = []
        for sid, raw in zip(ids, raws, strict=False):
            if isinstance(raw, BaseException):
                continue
            rating = raw.get("rating") or {}
            collection = raw.get("collection") or {}
            tags = [str(t.get("name")) for t in (raw.get("tags") or [])[:8] if isinstance(t, dict) and t.get("name")]
            columns.append(
                CompareColumn(
                    id=sid,
                    name=str(raw.get("name") or ""),
                    name_cn=str(raw.get("name_cn") or raw.get("name") or ""),
                    date=str(raw.get("date") or ""),
                    eps=raw.get("eps") or raw.get("total_episodes"),
                    score=rating.get("score"),
                    rank=rating.get("rank"),
                    rating_total=rating.get("total"),
                    doing=collection.get("doing"),
                    collect=collection.get("collect"),
                    dropped=collection.get("dropped"),
                    top_tags=tags,
                    image=(raw.get("images") or {}).get("common"),
                    url=f"https://bgm.tv/subject/{sid}",
                )
            )
        if len(columns) < 2:
            return ToolResult(ok=False, error="可对比的条目不足 2 个（部分条目获取失败）")
        tag_sets = [set(c.top_tags) for c in columns]
        shared = set.intersection(*tag_sets) if tag_sets else set()
        for c in columns:
            c.unique_tags = [t for t in c.top_tags if t not in shared][:5]
        highlights: list[str] = []
        by_score = max(columns, key=lambda c: c.score or 0)
        if by_score.score:
            highlights.append(f"评分更高：{by_score.name_cn}（{by_score.score}）")
        by_hot = max(columns, key=lambda c: (c.doing or 0) + (c.collect or 0))
        highlights.append(f"更热门（收藏+在看）：{by_hot.name_cn}")
        drop_rates = [
            (c, (c.dropped or 0) / max((c.collect or 0) + (c.dropped or 0), 1))
            for c in columns
        ]
        low_drop = min(drop_rates, key=lambda x: x[1])
        highlights.append(f"弃番率更低：{low_drop[0].name_cn}（{low_drop[1] * 100:.1f}%）")
        result = CompareSubjectsResult(
            columns=columns,
            shared_tags=sorted(shared)[:8],
            highlights=highlights,
            caveats=["硬指标对比截至查询时；『哪个更适合你』还取决于口味画像与口碑证据（可再调 review_subject / recommend）。"],
        )
        sources = [Citation(title=c.name_cn or c.name, url=c.url, source="bangumi", image=c.image) for c in columns]
        return ToolResult(ok=True, data=result, sources=sources)


def build_discovery_tools(client: BangumiClient) -> list[Tool]:
    return [
        PredictMyRatingTool(client),
        SearchByTraitsTool(client),
        EpisodeBuzzRadarTool(client),
        GetTrendingSubjectsTool(),
        GetCharacterBirthdaysTool(),
        CompareSubjectsTool(client),
    ]
