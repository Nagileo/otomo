"""口味画像工具（A4）：读用户 Bangumi 看过的动画 → 聚合口味 → 写入长期记忆。

- 不传 username 时用 token 经 /v0/me 解析当前用户（你自己的号）。
- 传 username 则读其公开收藏（多用户、零授权）。
- 当前开发阶段每次按最新收藏重算；上线后再按需要启用缓存。
"""
from __future__ import annotations

import asyncio
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...memory import LongTermMemory
from ...memory.consolidate import now_iso
from ...memory.models import UserMemory, memory_summary
from ...profile import TasteProfile, compute_taste_profile
from ...security_context import can_access_private_user
from ...subscription_read import public_subscription_summary
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

_MAX_ITEMS = 1000  # 重度用户常 >300，尽量拉全（分页每页 50）；再大就分批/采样


class TasteArgs(BaseModel):
    subject_type: Literal["anime", "book", "music", "game", "real"] = Field(
        "anime", description="对哪类作品画像（anime/book(漫画·小说)/music/game/real）；默认动画"
    )
    username: str | None = Field(
        None, description="Bangumi 用户名；不传则用当前登录账号（需 token）"
    )
    refresh: bool = Field(False, description="兼容参数；当前开发阶段始终重新计算")


class TasteReportArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则用当前账号")
    subject_types: list[Literal["anime", "book", "music", "game", "real"]] = Field(
        default_factory=lambda: ["anime", "book", "game", "music"],
        max_length=5,
        description="要汇总的媒介类型",
    )
    include_memory: bool = Field(True, description="是否合并长期记忆/aspect/推荐反馈")


class CollectionDashboardArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则用当前账号")
    subject_types: list[Literal["anime", "book", "music", "game", "real"]] = Field(
        default_factory=lambda: ["anime", "book", "game", "music", "real"],
        max_length=5,
        description="要纳入仪表盘的媒介类型",
    )
    max_items_per_type: int = Field(1000, ge=100, le=3000, description="每个媒介最多拉取多少收藏")
    include_memory: bool = True
    enrich_people: bool = Field(True, description="是否对代表性高分条目补 staff/studio/CV 统计")
    enrich_limit: int = Field(24, ge=0, le=60, description="每个媒介最多补多少个条目的 staff/CV enrichment")


class TasteReportSection(BaseModel):
    subject_type: str
    watched: int = 0
    rated: int = 0
    avg_rating: float | None = None
    top_tags: list[dict] = Field(default_factory=list)
    favorites: list[str] = Field(default_factory=list)
    aspect_likes: list[dict] = Field(default_factory=list)
    aspect_dislikes: list[dict] = Field(default_factory=list)
    persona: str = ""
    next_actions: list[str] = Field(default_factory=list)


class TasteReportResult(BaseModel):
    username: str
    sections: list[TasteReportSection] = Field(default_factory=list)
    global_likes: list[dict] = Field(default_factory=list)
    global_dislikes: list[dict] = Field(default_factory=list)
    recent_feedback: list[dict] = Field(default_factory=list)
    share_summary: str = ""
    report_tags: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    memory: dict | None = None


class DashboardMediaStats(BaseModel):
    subject_type: str
    total: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    rated: int = 0
    avg_rating: float | None = None
    rating_distribution: dict[str, int] = Field(default_factory=dict)
    year_distribution: dict[str, int] = Field(default_factory=dict)
    decade_distribution: dict[str, int] = Field(default_factory=dict)
    yearly_activity: list[dict] = Field(default_factory=list)
    top_tags: list[dict] = Field(default_factory=list)
    tag_drift: list[dict] = Field(default_factory=list)
    studio_affinity: list[dict] = Field(default_factory=list)
    staff_affinity: list[dict] = Field(default_factory=list)
    cv_affinity: list[dict] = Field(default_factory=list)
    high_rated: list[dict] = Field(default_factory=list)
    backlog: list[dict] = Field(default_factory=list)
    on_hold_or_abandoned: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CollectionDashboardResult(BaseModel):
    username: str
    generated_at: str = ""
    totals: dict[str, int] = Field(default_factory=dict)
    media: list[DashboardMediaStats] = Field(default_factory=list)
    global_top_tags: list[dict] = Field(default_factory=list)
    rating_strictness: str = ""
    plan_summary: dict[str, int] = Field(default_factory=dict)
    subscriptions: dict = Field(default_factory=dict)
    memory_signals: dict[str, list[dict]] = Field(default_factory=dict)
    enrichment: dict = Field(default_factory=dict)
    recommendations_for_next_step: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


def _persona(profile: TasteProfile, subject_type: str) -> str:
    tags = [str(x.get("tag")) for x in profile.top_tags[:6] if x.get("tag")]
    tag_text = "、".join(tags[:4]) if tags else "标签样本不足"
    if profile.watched == 0:
        return f"{subject_type} 样本不足，暂时不做人设判断。"
    if subject_type == "anime":
        if any(t in tags for t in ("日常", "治愈", "百合", "芳文社")):
            return f"偏轻日常/情绪体验型动画口味，核心标签是 {tag_text}。"
        if any(t in tags for t in ("战斗", "热血", "奇幻", "科幻")):
            return f"偏类型爽点和世界观驱动，核心标签是 {tag_text}。"
    if subject_type == "game":
        if any(t in tags for t in ("galgame", "视觉小说", "ADV", "剧情")):
            return f"偏文本/角色/剧情体验型游戏口味，核心标签是 {tag_text}。"
    if subject_type == "music":
        return f"音乐口味更适合按作品关联、OST/主题歌/角色歌分流，当前标签是 {tag_text}。"
    if subject_type == "book":
        return f"book 口味需要区分漫画/轻小说/小说，当前主要标签是 {tag_text}。"
    return f"{subject_type} 口味标签：{tag_text}。"


def _next_actions(subject_type: str, profile: TasteProfile, has_aspect: bool) -> list[str]:
    out: list[str] = []
    if profile.watched < 5:
        out.append("样本偏少，先用 2-3 个澄清问题或显式标签做冷启动。")
    if not has_aspect:
        out.append("可运行 build_aspect_profile，用私评建立好球区/雷区。")
    if subject_type == "book":
        out.append("推荐时显式选择 comic/light_novel/novel，避免 book 混池。")
    if subject_type == "music":
        out.append("音乐推荐按 OST/主题歌/角色歌/艺人专辑分流，必要时用 MusicBrainz 补元数据。")
    if subject_type == "anime":
        out.append("可用 plan_watch_copilot 把想看/在看/搁置转成本周队列。")
    return out[:4]


_STATUS_LABEL = {
    1: "想看/想读/想玩",
    2: "看过/读过/玩过",
    3: "在看/在读/在玩",
    4: "搁置",
    5: "抛弃",
}


def _subject(item: dict) -> dict:
    return item.get("subject") or item


def _subject_title(item: dict) -> str:
    subj = _subject(item)
    return subj.get("name_cn") or subj.get("name") or ""


def _year(item: dict) -> str:
    date = str(_subject(item).get("date") or "")
    return date[:4] if len(date) >= 4 and date[:4].isdigit() else ""


def _dashboard_subject_card(item: dict) -> dict:
    subj = _subject(item)
    images = subj.get("images") or {}
    return {
        "id": subj.get("id"),
        "name": _subject_title(item),
        "rate": item.get("rate") or 0,
        "status": _STATUS_LABEL.get(int(item.get("type") or 0), "未知"),
        "ep_status": item.get("ep_status"),
        "date": subj.get("date") or "",
        "image": images.get("common") or images.get("medium") or images.get("grid") or "",
    }


def _subject_id(item: dict) -> int | None:
    sid = _subject(item).get("id")
    try:
        return int(sid)
    except (TypeError, ValueError):
        return None


def _item_rate(item: dict) -> int:
    try:
        return int(item.get("rate") or 0)
    except (TypeError, ValueError):
        return 0


def _item_status(item: dict) -> int:
    try:
        return int(item.get("type") or 0)
    except (TypeError, ValueError):
        return 0


def _tag_names(item: dict) -> list[str]:
    names: list[str] = []
    for tag in _subject(item).get("tags") or []:
        name = (tag or {}).get("name")
        if name:
            names.append(str(name))
    return names


def _rating_strictness(avg: float | None, rated: int) -> str:
    if not avg or rated < 8:
        return "评分样本偏少，暂不判断严格度。"
    if avg >= 8:
        return "评分偏宽松/偏爱型：高分比例较高，适合用雷区与弃坑样本做负反馈校准。"
    if avg <= 6.2:
        return "评分偏严格：推荐应更看重高置信口碑与明确命中，不宜只靠热门。"
    return "评分分布较均衡：适合综合标签、评分、同步率和近期反馈排序。"


def _yearly_activity(items: list[dict]) -> list[dict]:
    by_year: dict[str, dict] = {}
    for item in items:
        year = _year(item)
        if not year:
            continue
        bucket = by_year.setdefault(
            year,
            {
                "year": year,
                "total": 0,
                "rated": 0,
                "completed": 0,
                "planning_or_current": 0,
                "on_hold_or_abandoned": 0,
                "high_rated": 0,
                "_score_sum": 0,
            },
        )
        rate = _item_rate(item)
        status = _item_status(item)
        bucket["total"] += 1
        if rate > 0:
            bucket["rated"] += 1
            bucket["_score_sum"] += rate
        if rate >= 9:
            bucket["high_rated"] += 1
        if status == 2:
            bucket["completed"] += 1
        elif status in {1, 3}:
            bucket["planning_or_current"] += 1
        elif status in {4, 5}:
            bucket["on_hold_or_abandoned"] += 1
    rows: list[dict] = []
    for year, bucket in sorted(by_year.items(), key=lambda kv: kv[0], reverse=True):
        rated = int(bucket.pop("rated") or 0)
        score_sum = int(bucket.pop("_score_sum") or 0)
        bucket["rated"] = rated
        bucket["avg_rating"] = round(score_sum / rated, 2) if rated else None
        rows.append(bucket)
    return rows


def _weighted_tags(items: list[dict]) -> Counter[str]:
    tags: Counter[str] = Counter()
    for item in items:
        weight = max(_item_rate(item), 1)
        for name in _tag_names(item):
            tags[name] += weight
    return tags


def _tag_drift(items: list[dict]) -> list[dict]:
    dated = [x for x in items if _year(x)]
    if len(dated) < 8:
        return []
    dated.sort(key=lambda x: (_year(x), _subject_title(x)))
    mid = len(dated) // 2
    older, recent = dated[:mid], dated[mid:]
    old_tags = _weighted_tags(older)
    recent_tags = _weighted_tags(recent)
    old_total = max(sum(old_tags.values()), 1)
    recent_total = max(sum(recent_tags.values()), 1)
    rows: list[dict] = []
    for tag in set(old_tags) | set(recent_tags):
        old_share = old_tags[tag] / old_total
        recent_share = recent_tags[tag] / recent_total
        delta = recent_share - old_share
        if abs(delta) < 0.01:
            continue
        rows.append(
            {
                "tag": tag,
                "trend": "rising" if delta > 0 else "receding",
                "delta": round(delta, 4),
                "recent_weight": int(recent_tags[tag]),
                "older_weight": int(old_tags[tag]),
                "recent_share": round(recent_share, 4),
                "older_share": round(old_share, 4),
            }
        )
    return sorted(rows, key=lambda x: abs(float(x.get("delta") or 0)), reverse=True)[:14]


def _sample_enrichment_items(items: list[dict], limit: int) -> list[dict]:
    if limit <= 0:
        return []
    scored = [x for x in items if _subject_id(x)]
    scored.sort(
        key=lambda x: (
            _item_rate(x),
            1 if _item_status(x) == 2 else 0,
            _year(x),
            _subject_title(x),
        ),
        reverse=True,
    )
    out: list[dict] = []
    seen: set[int] = set()
    for item in scored:
        sid = _subject_id(item)
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(item)
        if len(out) >= limit:
            break
    return out


_STUDIO_ROLE_HINTS = ("动画制作", "制作", "制作协力", "studio", "开发")
_STAFF_ROLE_HINTS = (
    "导演", "监督", "脚本", "系列构成", "原作", "音乐", "人物设定", "角色设计",
    "美术", "摄影", "演出", "分镜", "企画", "制作", "动画制作", "开发", "剧本", "原画",
)


def _person_name(row: dict) -> str:
    return str(row.get("name_cn") or row.get("name") or "").strip()


def _relation(row: dict) -> str:
    return str(row.get("relation") or "staff").strip() or "staff"


def _role_matches(relation: str, hints: tuple[str, ...]) -> bool:
    relation_l = relation.lower()
    return any(h.lower() in relation_l for h in hints)


def _rank_affinity(counter: dict[tuple[str, str], dict], limit: int = 10) -> list[dict]:
    rows = sorted(
        counter.values(),
        key=lambda x: (int(x.get("count") or 0), float(x.get("weighted_score") or 0), str(x.get("name") or "")),
        reverse=True,
    )
    out: list[dict] = []
    for row in rows[:limit]:
        copied = dict(row)
        works = copied.get("works") or []
        copied["works"] = works[:5]
        copied["weighted_score"] = round(float(copied.get("weighted_score") or 0), 2)
        out.append(copied)
    return out


def _bump_affinity(
    counter: dict[tuple[str, str], dict],
    name: str,
    relation: str,
    item: dict,
) -> None:
    if not name:
        return
    key = (name, relation)
    rate = _item_rate(item)
    row = counter.setdefault(
        key,
        {
            "name": name,
            "relation": relation,
            "count": 0,
            "weighted_score": 0.0,
            "works": [],
        },
    )
    row["count"] += 1
    row["weighted_score"] += max(rate, 1)
    work = _dashboard_subject_card(item)
    if work.get("id") and all(w.get("id") != work.get("id") for w in row["works"]):
        row["works"].append(work)


async def _enrich_people_stats(
    client: BangumiClient,
    subject_type: str,
    items: list[dict],
    limit: int,
) -> dict:
    sampled = _sample_enrichment_items(items, limit)
    if not sampled:
        return {
            "sampled_count": 0,
            "sampled_subjects": [],
            "studio_affinity": [],
            "staff_affinity": [],
            "cv_affinity": [],
            "failures": [],
        }
    semaphore = asyncio.Semaphore(4)
    staff_counter: dict[tuple[str, str], dict] = {}
    studio_counter: dict[tuple[str, str], dict] = {}
    cv_counter: dict[tuple[str, str], dict] = {}
    failures: list[dict] = []

    async def load_persons(item: dict) -> None:
        sid = _subject_id(item)
        if not sid:
            return
        try:
            async with semaphore:
                persons = await client.get_subject_persons(sid)
        except Exception as exc:  # noqa: BLE001 - enrichment 不应拖垮仪表盘
            failures.append({"subject_id": sid, "name": _subject_title(item), "stage": "persons", "error": str(exc)[:160]})
            return
        for row in persons or []:
            name = _person_name(row)
            relation = _relation(row)
            if not name:
                continue
            if _role_matches(relation, _STAFF_ROLE_HINTS):
                _bump_affinity(staff_counter, name, relation, item)
            if subject_type == "anime" and _role_matches(relation, _STUDIO_ROLE_HINTS):
                _bump_affinity(studio_counter, name, relation, item)
            elif subject_type == "game" and _role_matches(relation, ("开发", "发行", "制作")):
                _bump_affinity(studio_counter, name, relation, item)

    async def load_characters(item: dict) -> None:
        if subject_type not in {"anime", "game"}:
            return
        sid = _subject_id(item)
        if not sid:
            return
        try:
            async with semaphore:
                characters = await client.get_subject_characters(sid)
        except Exception as exc:  # noqa: BLE001
            failures.append({"subject_id": sid, "name": _subject_title(item), "stage": "characters", "error": str(exc)[:160]})
            return
        for char in (characters or [])[:14]:
            actors = char.get("actors") or []
            if not actors:
                continue
            actor = actors[0]
            name = str(actor.get("name") or actor.get("name_cn") or "").strip()
            if name:
                _bump_affinity(cv_counter, name, "CV", item)

    await asyncio.gather(*(load_persons(item) for item in sampled))
    await asyncio.gather(*(load_characters(item) for item in sampled[: max(8, limit // 2)]))
    return {
        "sampled_count": len(sampled),
        "sampled_subjects": [_dashboard_subject_card(x) for x in sampled[:10]],
        "studio_affinity": _rank_affinity(studio_counter),
        "staff_affinity": _rank_affinity(staff_counter),
        "cv_affinity": _rank_affinity(cv_counter),
        "failures": failures[:8],
    }


def _dashboard_stats(subject_type: str, items: list[dict], enrichment: dict | None = None) -> DashboardMediaStats:
    status = Counter(_STATUS_LABEL.get(_item_status(x), "未知") for x in items)
    rates = [_item_rate(x) for x in items if _item_rate(x) > 0]
    rating_dist = Counter(str(x) for x in rates)
    years = Counter(_year(x) for x in items if _year(x))
    decades = Counter(f"{y[:3]}0s" for y in years for _ in range(years[y]))
    tags = _weighted_tags(items)
    high = sorted([x for x in items if _item_rate(x) >= 9], key=lambda x: -_item_rate(x))
    backlog = [x for x in items if _item_status(x) in {1, 3}]
    dropped = [x for x in items if _item_status(x) in {4, 5}]
    notes: list[str] = []
    if not items:
        notes.append("该媒介没有可见收藏。")
    if dropped:
        notes.append("搁置/抛弃条目可进入弃坑分析，结合 ep_status 与分集讨论定位节点。")
    if subject_type == "book":
        notes.append("book 池内包含漫画/轻小说/小说，推荐时应继续按标签拆分。")
    if subject_type == "music":
        notes.append("music 池更适合按 OST/主题歌/角色歌/艺人专辑拆分。")
    if enrichment and enrichment.get("sampled_count"):
        notes.append(f"staff/CV/studio enrichment 基于 {enrichment.get('sampled_count')} 个高分/已评分代表条目采样统计。")
    return DashboardMediaStats(
        subject_type=subject_type,
        total=len(items),
        status_counts=dict(status.most_common()),
        rated=len(rates),
        avg_rating=round(sum(rates) / len(rates), 2) if rates else None,
        rating_distribution=dict(sorted(rating_dist.items(), key=lambda kv: int(kv[0]))),
        year_distribution=dict(years.most_common(12)),
        decade_distribution=dict(decades.most_common()),
        yearly_activity=_yearly_activity(items),
        top_tags=[{"tag": k, "weight": v} for k, v in tags.most_common(14)],
        tag_drift=_tag_drift(items),
        studio_affinity=(enrichment or {}).get("studio_affinity") or [],
        staff_affinity=(enrichment or {}).get("staff_affinity") or [],
        cv_affinity=(enrichment or {}).get("cv_affinity") or [],
        high_rated=[_dashboard_subject_card(x) for x in high[:10]],
        backlog=[_dashboard_subject_card(x) for x in backlog[:10]],
        on_hold_or_abandoned=[_dashboard_subject_card(x) for x in dropped[:10]],
        notes=notes,
    )


class TasteProfileTool(Tool):
    name = "get_taste_profile"
    description = (
        "分析某用户的二次元口味画像（看过的动画的标签偏好、评分分布、年代、最爱作品）。"
        "用于『分析我的口味 / 我是什么二次元人格 / 据此推荐』。不传 username 用当前账号。"
    )
    args_model = TasteArgs
    result_model = TasteProfile

    def __init__(self, client: BangumiClient, _ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = _ltm

    async def run(self, args: TasteArgs) -> ToolResult[TasteProfile]:
        username = args.username
        if not username:
            me = await self.client.get_me()
            username = me.get("username") or str(me.get("id"))

        items = await self.client.get_all_user_collections(
            username, SUBJECT_TYPE[args.subject_type], collection_type=2, max_items=_MAX_ITEMS
        )
        profile = compute_taste_profile(username, items)
        if can_access_private_user(username):
            mem = self.ltm.load_user(username)
            mem.profile_snapshot[args.subject_type] = {
                "watched": profile.watched,
                "rated": profile.rated,
                "avg_rating": profile.avg_rating,
                "top_tags": profile.top_tags[:12],
                "favorites": profile.favorites[:8],
                "updated_at": now_iso(),
            }
            self.ltm.save_user(mem)
        return ToolResult(
            ok=True,
            data=profile,
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
        )


class TasteReportTool(Tool):
    name = "build_taste_report"
    description = (
        "生成可展示的跨媒介口味报告：基础画像、aspect 好球区/雷区、长期喜欢/避雷、推荐反馈和下一步推荐策略。"
        "用于『我的完整口味报告 / 年度二次元总结 / 我适合看什么类型 / 分享画像』。"
    )
    args_model = TasteReportArgs
    result_model = TasteReportResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def _username(self, username: str | None) -> str:
        if username:
            return username
        me = await self.client.get_me()
        return me.get("username") or str(me.get("id"))

    async def run(self, args: TasteReportArgs) -> ToolResult[TasteReportResult]:
        username = await self._username(args.username)
        private_memory = can_access_private_user(username)
        mem = self.ltm.load_user(username) if private_memory else UserMemory(username=username)
        sections: list[TasteReportSection] = []
        report_tags: list[str] = []
        seen_types = list(dict.fromkeys(args.subject_types))
        for subject_type in seen_types:
            items = await self.client.get_all_user_collections(
                username, SUBJECT_TYPE[subject_type], collection_type=2, max_items=_MAX_ITEMS
            )
            profile = compute_taste_profile(username, items)
            aspect = mem.aspect_profiles.get(subject_type)
            tags = [str(x.get("tag")) for x in profile.top_tags[:6] if x.get("tag")]
            report_tags.extend(tags[:3])
            mem.profile_snapshot[subject_type] = {
                "watched": profile.watched,
                "rated": profile.rated,
                "avg_rating": profile.avg_rating,
                "top_tags": profile.top_tags[:12],
                "favorites": profile.favorites[:8],
                "updated_at": now_iso(),
            }
            sections.append(
                TasteReportSection(
                    subject_type=subject_type,
                    watched=profile.watched,
                    rated=profile.rated,
                    avg_rating=profile.avg_rating,
                    top_tags=profile.top_tags[:12],
                    favorites=profile.favorites[:6],
                    aspect_likes=[x.model_dump(mode="json") for x in (aspect.likes[:6] if aspect else [])],
                    aspect_dislikes=[x.model_dump(mode="json") for x in (aspect.dislikes[:6] if aspect else [])],
                    persona=_persona(profile, subject_type),
                    next_actions=_next_actions(subject_type, profile, aspect is not None),
                )
            )
        if private_memory:
            self.ltm.save_user(mem)
        top_report_tags = list(dict.fromkeys(report_tags))[:10]
        share_summary = (
            f"@{username} 的 Otomo 口味画像："
            + ("、".join(top_report_tags[:6]) if top_report_tags else "样本不足")
            + "。"
        )
        return ToolResult(
            ok=True,
            data=TasteReportResult(
                username=username,
                sections=sections,
                global_likes=[x.model_dump(mode="json") for x in mem.likes[:10]] if args.include_memory else [],
                global_dislikes=[x.model_dump(mode="json") for x in mem.dislikes[:10]] if args.include_memory else [],
                recent_feedback=[x.model_dump(mode="json") for x in mem.feedback[-10:]]
                if args.include_memory and private_memory else [],
                share_summary=share_summary,
                report_tags=top_report_tags,
                caveats=[
                    "口味报告只使用 Bangumi 公开/授权收藏和 Otomo 长期记忆；私有不可见数据不会被纳入。",
                    "aspect 好球区/雷区是 derived_from_feedback 弱信号，显式偏好优先。",
                ],
                memory=memory_summary(mem).model_dump(mode="json", exclude_none=True)
                if private_memory else {},
            ),
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
        )


class CollectionDashboardTool(Tool):
    name = "build_collection_dashboard"
    description = (
        "生成完整收藏仪表盘：媒介分布、收藏状态、评分分布、年代趋势、Top标签、高分代表、待看/弃坑、计划板与周报状态。"
        "用于『我的收藏仪表盘 / 年度总结 / 口味数据面板 / 看板』。"
    )
    args_model = CollectionDashboardArgs
    result_model = CollectionDashboardResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def _username(self, username: str | None) -> str:
        if username:
            return username
        me = await self.client.get_me()
        return me.get("username") or str(me.get("id"))

    async def run(self, args: CollectionDashboardArgs) -> ToolResult[CollectionDashboardResult]:
        username = await self._username(args.username)
        private_memory = can_access_private_user(username)
        mem = self.ltm.load_user(username) if private_memory else UserMemory(username=username)
        media: list[DashboardMediaStats] = []
        global_tags: Counter[str] = Counter()
        enrichment_by_type: dict[str, dict] = {}
        seen_types = list(dict.fromkeys(args.subject_types))
        for subject_type in seen_types:
            items = await self.client.get_all_user_collections(
                username,
                SUBJECT_TYPE[subject_type],
                collection_type=None,
                max_items=args.max_items_per_type,
            )
            enrichment = {}
            if args.enrich_people and args.enrich_limit > 0:
                enrichment = await _enrich_people_stats(self.client, subject_type, items, args.enrich_limit)
                enrichment_by_type[subject_type] = enrichment
            stats = _dashboard_stats(subject_type, items, enrichment)
            media.append(stats)
            for row in stats.top_tags:
                global_tags[str(row.get("tag"))] += int(row.get("weight") or 0)
            mem.profile_snapshot[subject_type] = {
                "total": stats.total,
                "rated": stats.rated,
                "avg_rating": stats.avg_rating,
                "status_counts": stats.status_counts,
                "top_tags": stats.top_tags[:12],
                "tag_drift": stats.tag_drift[:8],
                "studio_affinity": stats.studio_affinity[:5],
                "staff_affinity": stats.staff_affinity[:5],
                "cv_affinity": stats.cv_affinity[:5],
                "updated_at": now_iso(),
            }
        if private_memory:
            self.ltm.save_user(mem)
        total_items = sum(x.total for x in media)
        total_rated = sum(x.rated for x in media)
        weighted_score = sum((x.avg_rating or 0) * x.rated for x in media)
        avg = round(weighted_score / total_rated, 2) if total_rated else None
        plan_status = Counter(x.status for x in mem.watch_plan)
        result = CollectionDashboardResult(
            username=username,
            generated_at=now_iso(),
            totals={
                "items": total_items,
                "rated": total_rated,
                "media_types": len(media),
                "watch_plan": len(mem.watch_plan),
                "pending_writes": len([x for x in mem.pending_write_actions if x.status == "pending"]),
                "unread_inbox": len([x for x in mem.inbox if x.unread]),
            },
            media=media,
            global_top_tags=[{"tag": k, "weight": v} for k, v in global_tags.most_common(18)],
            rating_strictness=_rating_strictness(avg, total_rated),
            plan_summary=dict(plan_status.most_common()),
            subscriptions=public_subscription_summary(username) if private_memory else {},
            enrichment={
                "enabled": bool(args.enrich_people and args.enrich_limit > 0),
                "limit_per_type": args.enrich_limit,
                "sampled_by_type": {
                    k: {
                        "sampled_count": v.get("sampled_count") or 0,
                        "sampled_subjects": v.get("sampled_subjects") or [],
                        "failures": v.get("failures") or [],
                    }
                    for k, v in enrichment_by_type.items()
                },
                "method": "每个媒介按高分/已评分代表条目采样，拉取 Bangumi persons/characters 后聚合 staff/studio/CV 命中。",
            },
            memory_signals={
                "likes": [x.model_dump(mode="json") for x in mem.likes[:8]] if args.include_memory else [],
                "dislikes": [x.model_dump(mode="json") for x in mem.dislikes[:8]] if args.include_memory else [],
                "recent_feedback": [x.model_dump(mode="json") for x in mem.recent_feedback[-8:]] if args.include_memory else [],
            },
            recommendations_for_next_step=[
                "用 plan_watch_copilot 把想看/在看/搁置转成本周队列。",
                "对搁置/抛弃条目运行 analyze_abandoned_subjects，补负反馈。",
                "对样本最多的媒介运行 build_aspect_profile，建立方面级好球区/雷区。",
                "开启 weekly digest 后可把本季追番、想看开播和计划板状态自动写入 inbox。",
            ],
            caveats=[
                "仪表盘只统计 Bangumi 可见收藏与 Otomo 本地记忆；平台外观看历史不会出现。",
                "staff/CV/studio 是代表作采样统计，不等同于全量收藏的绝对排名；高分样本会被优先纳入。",
            ],
        )
        return ToolResult(
            ok=True,
            data=result,
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
        )


def build_profile_tools(client: BangumiClient, ltm: LongTermMemory) -> list[Tool]:
    return [TasteProfileTool(client, ltm), TasteReportTool(client, ltm), CollectionDashboardTool(client, ltm)]
