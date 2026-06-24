"""在线内容推荐工具（B-online，多策略召回）。

漏斗（在线只能内容/知识侧——Bangumi API 无跨用户共现）：
  多路召回并集 → 排除已看 → 去重+多样性 → 统一打分 → top-N。agent 据 reasons 解释。

召回 provider（治重度用户"热门已看完"的饱和，见 docs/06 §8）：
  1) 标签召回：口味/心境标签 heat 召回（niche 时往热度榜深处挖、偏小众高分）
  2) 图谱召回：你最爱作品的监督/制作组/原作 → 他们你还没看的其他作品（强信号、最契合 LLM+图谱）

CF/MF/LTR 属离线轨（recsys-offline，有 user×item 矩阵）。通用 subject_type：anime/book/music/game/real。
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...profile import compute_taste_profile
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

_RECALL_PER_TAG = 50
_MAX_RECALL_TAGS = 8
_MAX_COLLECT = 800       # 拉全部状态收藏以"完全排除已看"
_NICHE_OFFSET = 30       # 冷门模式：跳过最热门，挖热度榜中尾部
_GRAPH_FAV = 3           # 从前几部最爱做图谱召回
_GRAPH_ROLES = {"导演", "监督", "动画制作", "原作", "系列构成", "脚本", "制作"}

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
    return _SERIES_RE.sub("", name or "").strip().lower()


def _expand_moods(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        out.extend(_MOOD_TAG_MAP.get(t, [t]))
    return list(dict.fromkeys(out))


def _quality(rating: dict) -> float:
    rk = rating.get("rank") or 0
    return 1.0 / (1.0 + rk ** 0.5) if rk > 0 else 0.0


class RecommendArgs(BaseModel):
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    tags: list[str] | None = Field(
        None, description="额外/心境标签（如 ['治愈','百合']），与口味标签合并召回；心境推荐时由你提炼"
    )
    limit: int = Field(8, ge=1, le=20)
    username: str | None = Field(None, description="不传则用当前账号（需 token）")
    niche: bool = Field(
        False, description="冷门挖宝模式：偏小众高分、少推大热门（重度用户/想挖宝时用）"
    )
    use_graph: bool = Field(
        True, description="图谱召回：从你最爱作品的监督/制作组找其未看的其他作品（治'热门已看完'）"
    )


class RecItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    score: float
    reasons: list[str]            # 标签命中 + 图谱理由（如"同监督·佐藤順一"）
    bangumi_score: float | None = None
    rank: int | None = None


class RecommendResult(BaseModel):
    subject_type: str
    based_on_tags: list[str]
    niche: bool = False
    items: list[RecItem] = Field(default_factory=list)


class RecommendTool(Tool):
    name = "recommend_subjects"
    description = (
        "根据用户口味推荐 TA 没看过的作品，支持 anime/book/music/game/real。"
        "多路召回（标签 + 图谱：你爱的作品的监督/制作组的其他作品）。"
        "重度用户/想挖冷门时设 niche=true。用于『据我口味推荐 / 类似X / 挖点冷门 / 今天想看治愈的』。"
    )
    args_model = RecommendArgs
    result_model = RecommendResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def _tag_recall(self, cand, stype, recall_tags, user_tags, maxw, mood, seen, niche):
        offset = _NICHE_OFFSET if niche else 0
        for tag in recall_tags:
            res = await self.client.search_subjects(
                "", stype, sort="heat", limit=_RECALL_PER_TAG, tags=[tag], offset=offset
            )
            w = maxw if tag in mood else user_tags.get(tag, 1.0)
            for x in res.get("data", []):
                sid = x.get("id")
                if not sid or sid in seen:
                    continue
                c = cand.setdefault(sid, _blank(x))
                c["matched"].add(tag)
                c["weight"] += w

    async def _graph_recall(self, cand, stype, fav_ids, seen):
        """从最爱作品的监督/制作组/原作 → 他们的其他未看作品（强信号召回）。"""
        for sid in fav_ids[:_GRAPH_FAV]:
            persons = await self.client.get_subject_persons(sid)
            picks = [p for p in (persons or []) if p.get("relation") in _GRAPH_ROLES and p.get("id")][:2]
            for p in picks:
                works = await self.client.get_person_subjects(p["id"])
                for w in works or []:
                    if w.get("type") != stype:
                        continue
                    wid = w.get("id")
                    if not wid or wid in seen:
                        continue
                    c = cand.setdefault(wid, _blank(w))
                    c["graph"].add(f"同{p.get('relation')}·{p.get('name')}")

    async def run(self, args: RecommendArgs) -> ToolResult[RecommendResult]:
        stype = SUBJECT_TYPE[args.subject_type]
        username = args.username or (await self.client.get_me()).get("username")

        items = await self.client.get_all_user_collections(username, stype, None, max_items=_MAX_COLLECT)
        seen = {it["subject"]["id"] for it in items if it.get("subject", {}).get("id")}
        watched = [it for it in items if it.get("type") == 2]
        profile = compute_taste_profile(username, watched)
        user_tags = {t["tag"]: float(t["weight"]) for t in profile.top_tags[:6]}
        maxw = max(user_tags.values()) if user_tags else 1.0
        mood = _expand_moods(args.tags or [])
        recall_tags = list(dict.fromkeys(mood + list(user_tags.keys())))[:_MAX_RECALL_TAGS]

        cand: dict[int, dict] = {}
        await self._tag_recall(cand, stype, recall_tags, user_tags, maxw, mood, seen, args.niche)
        if args.use_graph:
            fav_ids = [
                it["subject"]["id"]
                for it in sorted(watched, key=lambda it: -(it.get("rate") or 0))
                if it.get("subject", {}).get("id")
            ]
            await self._graph_recall(cand, stype, fav_ids, seen)

        def score(c: dict) -> float:
            base = c["weight"] / maxw
            if args.niche:  # 偏高分、不偏人气
                q = (c["rating"].get("score") or 0) / 10.0
            else:           # 偏高排名（人气+质量）
                q = _quality(c["rating"])
            return base + 1.5 * q + 1.0 * len(c["graph"])

        ranked = sorted(cand.items(), key=lambda kv: -score(kv[1]))
        out: list[RecItem] = []
        seen_series: set[str] = set()
        for sid, c in ranked:
            sk = _series_key(c["name"])
            if sk in seen_series:
                continue
            seen_series.add(sk)
            out.append(RecItem(
                id=sid, name=c["name"], score=round(score(c), 3),
                reasons=sorted(c["matched"]) + sorted(c["graph"]),
                bangumi_score=(c["rating"] or {}).get("score"),
                rank=(c["rating"] or {}).get("rank"),
            ))
            if len(out) >= args.limit:
                break

        return ToolResult(
            ok=True,
            data=RecommendResult(
                subject_type=args.subject_type, based_on_tags=recall_tags, niche=args.niche, items=out
            ),
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
        )


def _blank(x: dict) -> dict:
    return {
        "name": x.get("name_cn") or x.get("name"),
        "matched": set(), "graph": set(), "weight": 0.0,
        "rating": x.get("rating") or {},
    }
