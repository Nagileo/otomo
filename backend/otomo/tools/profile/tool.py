"""口味画像工具（A4）：读用户 Bangumi 看过的动画 → 聚合口味 → 写入长期记忆。

- 不传 username 时用 token 经 /v0/me 解析当前用户（你自己的号）。
- 传 username 则读其公开收藏（多用户、零授权）。
- 当前开发阶段每次按最新收藏重算；上线后再按需要启用缓存。
"""
from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...memory import LongTermMemory
from ...memory.consolidate import now_iso
from ...memory.models import memory_summary
from ...profile import TasteProfile, compute_taste_profile
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
    top_tags: list[dict] = Field(default_factory=list)
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
    weekly_subscription: dict = Field(default_factory=dict)
    memory_signals: dict[str, list[dict]] = Field(default_factory=dict)
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


def _rating_strictness(avg: float | None, rated: int) -> str:
    if not avg or rated < 8:
        return "评分样本偏少，暂不判断严格度。"
    if avg >= 8:
        return "评分偏宽松/偏爱型：高分比例较高，适合用雷区与弃坑样本做负反馈校准。"
    if avg <= 6.2:
        return "评分偏严格：推荐应更看重高置信口碑与明确命中，不宜只靠热门。"
    return "评分分布较均衡：适合综合标签、评分、同步率和近期反馈排序。"


def _dashboard_stats(subject_type: str, items: list[dict]) -> DashboardMediaStats:
    status = Counter(_STATUS_LABEL.get(int(x.get("type") or 0), "未知") for x in items)
    rates = [int(x.get("rate") or 0) for x in items if int(x.get("rate") or 0) > 0]
    rating_dist = Counter(str(x) for x in rates)
    years = Counter(_year(x) for x in items if _year(x))
    decades = Counter(f"{y[:3]}0s" for y in years for _ in range(years[y]))
    tags: Counter[str] = Counter()
    for item in items:
        rate = int(item.get("rate") or 0)
        weight = max(rate, 1)
        for tag in _subject(item).get("tags") or []:
            name = (tag or {}).get("name")
            if name:
                tags[name] += weight
    high = sorted([x for x in items if int(x.get("rate") or 0) >= 9], key=lambda x: -int(x.get("rate") or 0))
    backlog = [x for x in items if int(x.get("type") or 0) in {1, 3}]
    dropped = [x for x in items if int(x.get("type") or 0) in {4, 5}]
    notes: list[str] = []
    if not items:
        notes.append("该媒介没有可见收藏。")
    if dropped:
        notes.append("搁置/抛弃条目可进入弃坑分析，结合 ep_status 与分集讨论定位节点。")
    if subject_type == "book":
        notes.append("book 池内包含漫画/轻小说/小说，推荐时应继续按标签拆分。")
    if subject_type == "music":
        notes.append("music 池更适合按 OST/主题歌/角色歌/艺人专辑拆分。")
    return DashboardMediaStats(
        subject_type=subject_type,
        total=len(items),
        status_counts=dict(status.most_common()),
        rated=len(rates),
        avg_rating=round(sum(rates) / len(rates), 2) if rates else None,
        rating_distribution=dict(sorted(rating_dist.items(), key=lambda kv: int(kv[0]))),
        year_distribution=dict(years.most_common(12)),
        decade_distribution=dict(decades.most_common()),
        top_tags=[{"tag": k, "weight": v} for k, v in tags.most_common(14)],
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
        mem = self.ltm.load_user(username)
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
                recent_feedback=[x.model_dump(mode="json") for x in mem.recent_feedback[:10]] if args.include_memory else [],
                share_summary=share_summary,
                report_tags=top_report_tags,
                caveats=[
                    "口味报告只使用 Bangumi 公开/授权收藏和 Otomo 长期记忆；私有不可见数据不会被纳入。",
                    "aspect 好球区/雷区是 derived_from_feedback 弱信号，显式偏好优先。",
                ],
                memory=memory_summary(mem).model_dump(mode="json", exclude_none=True),
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
        mem = self.ltm.load_user(username)
        media: list[DashboardMediaStats] = []
        global_tags: Counter[str] = Counter()
        seen_types = list(dict.fromkeys(args.subject_types))
        for subject_type in seen_types:
            items = await self.client.get_all_user_collections(
                username,
                SUBJECT_TYPE[subject_type],
                collection_type=None,
                max_items=args.max_items_per_type,
            )
            stats = _dashboard_stats(subject_type, items)
            media.append(stats)
            for row in stats.top_tags:
                global_tags[str(row.get("tag"))] += int(row.get("weight") or 0)
            mem.profile_snapshot[subject_type] = {
                "total": stats.total,
                "rated": stats.rated,
                "avg_rating": stats.avg_rating,
                "status_counts": stats.status_counts,
                "top_tags": stats.top_tags[:12],
                "updated_at": now_iso(),
            }
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
            weekly_subscription=mem.weekly_digest_subscription.model_dump(mode="json"),
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
                "staff/CV/studio 严肃统计需要额外逐条拉取 staff/persons，当前先预留为后续 enrichment。",
            ],
        )
        return ToolResult(
            ok=True,
            data=result,
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
        )


def build_profile_tools(client: BangumiClient, ltm: LongTermMemory) -> list[Tool]:
    return [TasteProfileTool(client, ltm), TasteReportTool(client, ltm), CollectionDashboardTool(client, ltm)]
