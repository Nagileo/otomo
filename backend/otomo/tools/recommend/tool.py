"""在线内容推荐工具（B1-online）。

漏斗（在线只能内容侧——Bangumi API 无跨用户共现）：
  口味画像(top 标签) + 可选心境标签 → 按标签 heat 召回候选 → 排除已看 → 按(个人标签权重 + 质量)重排 → top-N。
agent 据返回的 matched_tags 生成"为什么推荐"。CF/MF/LTR 属离线轨（recsys-offline，有 user×item 矩阵）。

通用 subject_type：anime / book(漫画·小说) / music / game / real。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...profile import compute_taste_profile
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

_RECALL_PER_TAG = 20
_MAX_RECALL_TAGS = 6


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

        items = await self.client.get_all_user_collections(username, stype, None, max_items=300)
        seen = {it["subject"]["id"] for it in items if it.get("subject", {}).get("id")}
        watched = [it for it in items if it.get("type") == 2]
        profile = compute_taste_profile(username, watched)
        user_tags = {t["tag"]: float(t["weight"]) for t in profile.top_tags[:6]}

        mood = args.tags or []
        boost = (max(user_tags.values()) if user_tags else 1.0)  # 心境标签给到与最高口味标签同权
        recall_tags = list(dict.fromkeys(mood + list(user_tags.keys())))[:_MAX_RECALL_TAGS]

        cand: dict[int, dict] = {}
        for tag in recall_tags:
            res = await self.client.search_subjects("", stype, sort="heat", limit=_RECALL_PER_TAG, tags=[tag])
            w = boost if tag in mood else user_tags.get(tag, 1.0)
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

        ranked = sorted(
            cand.items(), key=lambda kv: -(kv[1]["weight"] + 2.0 * _quality(kv[1]["rating"]))
        )
        out = [
            RecItem(
                id=sid, name=c["name"],
                score=round(c["weight"] + 2.0 * _quality(c["rating"]), 3),
                matched_tags=sorted(c["matched"]),
                bangumi_score=(c["rating"] or {}).get("score"),
                rank=(c["rating"] or {}).get("rank"),
            )
            for sid, c in ranked[: args.limit]
        ]
        return ToolResult(
            ok=True,
            data=RecommendResult(subject_type=args.subject_type, based_on_tags=recall_tags, items=out),
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
        )


def build_recommend_tools(client: BangumiClient) -> list[Tool]:
    return [RecommendTool(client)]
