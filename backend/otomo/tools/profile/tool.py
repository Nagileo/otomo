"""口味画像工具（A4）：读用户 Bangumi 看过的动画 → 聚合口味 → 写入长期记忆。

- 不传 username 时用 token 经 /v0/me 解析当前用户（你自己的号）。
- 传 username 则读其公开收藏（多用户、零授权）。
- 当前开发阶段每次按最新收藏重算；上线后再按需要启用缓存。
"""
from __future__ import annotations

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


def build_profile_tools(client: BangumiClient, ltm: LongTermMemory) -> list[Tool]:
    return [TasteProfileTool(client, ltm), TasteReportTool(client, ltm)]
