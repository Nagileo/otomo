"""在线内容推荐工具（B1-online）。

漏斗（在线只能内容侧——Bangumi API 无跨用户共现）：
  口味画像(top 标签) + 可选心境标签 → 按标签 heat 召回候选 → 排除已看 → 按(个人标签权重 + 质量)重排 → top-N。
agent 据返回的 matched_tags 生成"为什么推荐"。CF/MF/LTR 属离线轨（recsys-offline，有 user×item 矩阵）。

通用 subject_type：anime / book(漫画·小说) / music / game / real。
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...profile import compute_taste_profile
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

_RECALL_PER_TAG = 50  # 召回深度（重度用户热门番大多已看，需更深才剩得下未看的）
_MAX_RECALL_TAGS = 8
_MAX_COLLECT = 800  # 拉全部状态收藏以"完全排除已看"（你有 600+，旧的 300 会漏）

# 心境词 → 真实 Bangumi 标签（LLM 给的口语词未必是真标签，做一层映射，避免信号被静默丢弃）
_MOOD_TAG_MAP: dict[str, list[str]] = {
    "不费脑": ["日常", "治愈", "轻松"], "轻松": ["日常", "治愈", "搞笑"],
    "治愈": ["治愈", "日常"], "催泪": ["催泪", "感动"], "感动": ["催泪", "感动"],
    "热血": ["热血", "战斗"], "燃": ["热血", "战斗"], "致郁": ["致郁", "暗黑"],
    "虐": ["致郁", "虐心"], "甜": ["恋爱", "甜"], "搞笑": ["搞笑", "日常"],
    "恐怖": ["恐怖", "惊悚"], "悬疑": ["悬疑", "推理"],
}

_SERIES_RE = re.compile(
    r"(第?[0-9０-９一二三四五六七八九十]+[季期部篇]|Ⅱ|Ⅲ|Ⅳ|Ⅴ|剧场版|总集篇|OVA|OAD|"
    r"Final|完结篇|后篇|前篇|新章|续篇?|\s+|[:：].*$|[0-9０-９]+$)"
)


def _series_key(name: str) -> str:
    """把同系列不同季/剧场版归一到一个键，用于去重（轻音 S1/S2、辉夜多季等）。"""
    return _SERIES_RE.sub("", name or "").strip().lower()


def _expand_moods(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        out.extend(_MOOD_TAG_MAP.get(t, [t]))
    return list(dict.fromkeys(out))


class RecommendArgs(BaseModel):
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    tags: list[str] | None = Field(
        None, description="额外/心境标签（如 ['治愈','百合']），会与用户口味标签合并召回；心境推荐时由你从用户描述提炼"
    )
    limit: int = Field(8, ge=1, le=20)
    username: str | None = Field(None, description="不传则用当前账号（需 token）")


class RecItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    score: float
    matched_tags: list[str]
    bangumi_score: float | None = None
    rank: int | None = None


class RecommendResult(BaseModel):
    subject_type: str
    based_on_tags: list[str]
    items: list[RecItem] = Field(default_factory=list)


def _quality(rating: dict) -> float:
    rk = rating.get("rank") or 0
    return 1.0 / (1.0 + rk ** 0.5) if rk > 0 else 0.0


class RecommendTool(Tool):
    name = "recommend_subjects"
    description = (
        "根据用户口味（+可选心境标签）推荐 TA 没看过的作品，支持 anime/book/music/game/real。"
        "用于『据我口味推荐 / 今天想看治愈的 / 推荐几部百合番/游戏』。返回候选与匹配的标签理由。"
    )
    args_model = RecommendArgs
    result_model = RecommendResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: RecommendArgs) -> ToolResult[RecommendResult]:
        stype = SUBJECT_TYPE[args.subject_type]
        username = args.username
        if not username:
            username = (await self.client.get_me()).get("username")

        # 拉全部状态收藏：用于"完全排除已看" + 从"看过"算口味
        items = await self.client.get_all_user_collections(username, stype, None, max_items=_MAX_COLLECT)
        seen = {it["subject"]["id"] for it in items if it.get("subject", {}).get("id")}
        watched = [it for it in items if it.get("type") == 2]
        profile = compute_taste_profile(username, watched)
        user_tags = {t["tag"]: float(t["weight"]) for t in profile.top_tags[:6]}
        maxw = max(user_tags.values()) if user_tags else 1.0

        mood = _expand_moods(args.tags or [])  # 心境词 → 合法标签
        recall_tags = list(dict.fromkeys(mood + list(user_tags.keys())))[:_MAX_RECALL_TAGS]

        cand: dict[int, dict] = {}
        for tag in recall_tags:
            res = await self.client.search_subjects("", stype, sort="heat", limit=_RECALL_PER_TAG, tags=[tag])
            w = maxw if tag in mood else user_tags.get(tag, 1.0)  # 心境标签给到与最高口味标签同权
            for x in res.get("data", []):
                sid = x.get("id")
                if not sid or sid in seen:
                    continue
                c = cand.setdefault(
                    sid,
                    {"name": x.get("name_cn") or x.get("name"), "matched": set(), "weight": 0.0,
                     "rating": x.get("rating") or {}},
                )
                c["matched"].add(tag)
                c["weight"] += w

        # 归一打分：标签项(0~#matched) + 质量项(0~1.5)，量级可比、质量真正起作用
        def score(c: dict) -> float:
            return c["weight"] / maxw + 1.5 * _quality(c["rating"])

        ranked = sorted(cand.items(), key=lambda kv: -score(kv[1]))
        out: list[RecItem] = []
        seen_series: set[str] = set()
        for sid, c in ranked:  # 系列去重 + 多样性：每个系列只取最优一部
            sk = _series_key(c["name"])
            if sk in seen_series:
                continue
            seen_series.add(sk)
            out.append(RecItem(
                id=sid, name=c["name"], score=round(score(c), 3),
                matched_tags=sorted(c["matched"]),
                bangumi_score=(c["rating"] or {}).get("score"),
                rank=(c["rating"] or {}).get("rank"),
            ))
            if len(out) >= args.limit:
                break
        return ToolResult(
            ok=True,
            data=RecommendResult(subject_type=args.subject_type, based_on_tags=recall_tags, items=out),
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
        )


def build_recommend_tools(client: BangumiClient) -> list[Tool]:
    return [RecommendTool(client)]
