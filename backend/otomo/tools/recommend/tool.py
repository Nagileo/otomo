"""在线内容推荐工具（B-online，多策略召回 + 平衡打分）。

漏斗（在线只能内容/知识侧——Bangumi API 无跨用户共现）：
  多路召回并集 → 排除已看 → 评分补全 → 统一平衡打分 → 去重+多样性 → top-N。agent 据 reasons 解释。

召回 provider（治"热门已看完"的饱和，见 docs/06 §8）：
  1) 标签召回：口味/心境标签 heat 召回（explore 用次级标签拓展；niche 往热度榜深处挖）
  2) 图谱召回：你最爱作品的监督/制作组/原作 → 他们你没看的其他作品（强信号、最契合 LLM+图谱）

打分（已平衡，避免图谱压过标签）：affinity(标签贴合) + 封顶的 graph_bonus + 质量项。
  · 普通：质量=人气×口碑（rank）。  · niche：质量=高分 × 低人气（score 高、投票少）。
评分补全：图谱召回的候选 person_subjects 不带 rating，对 top 候选按需 get_subject 拉评分，才能按质量排。
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
_MAX_COLLECT = 800
_NICHE_OFFSET = 30        # 冷门模式：跳过最热门，挖热度榜中尾部
_GRAPH_FAV = 3
_GRAPH_ROLES = {"导演", "监督", "动画制作", "原作", "系列构成", "脚本", "制作"}
_ENRICH_TOP = 15         # 对前 N 个缺评分的候选按需补 get_subject

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


def _quality_popular(rating: dict) -> float:
    """人气+口碑：排名越前越高（0~1）。"""
    rk = rating.get("rank") or 0
    return 1.0 / (1.0 + rk ** 0.5) if rk > 0 else 0.0


def _quality_niche(rating: dict) -> float:
    """冷门佳作：以**高分为主**，低人气只做微调（避免把平庸冷门顶上来）。"""
    score = rating.get("score") or 0
    if score < 7.5:  # 冷门"佳作"——分数不达标的不算
        return 0.0
    total = rating.get("total") or 0
    rarity = 1.0 / (1.0 + total / 800.0)  # 投票越少越高
    return (score / 10.0) + 0.6 * rarity  # 分数主导，稀有度微调


def _blank(x: dict) -> dict:
    return {
        "name": x.get("name_cn") or x.get("name"),
        "matched": set(), "graph": set(), "weight": 0.0,
        "rating": x.get("rating") or {},
    }


class RecommendArgs(BaseModel):
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    tags: list[str] | None = Field(
        None, description="额外/心境标签（如 ['治愈','百合']），与口味标签合并召回；心境推荐时由你提炼"
    )
    limit: int = Field(8, ge=1, le=20)
    username: str | None = Field(None, description="不传则用当前账号（需 token）")
    niche: bool = Field(False, description="冷门挖宝：偏高分低人气、少推大热门（重度用户/想挖宝时用）")
    explore: bool = Field(False, description="口味拓展：用次级标签探索邻近题材，跳出核心舒适区")
    use_graph: bool = Field(True, description="图谱召回：从你最爱作品的监督/制作组找其未看的其他作品")


class RecItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    score: float
    reasons: list[str]
    bangumi_score: float | None = None
    rank: int | None = None


class RecommendResult(BaseModel):
    subject_type: str
    based_on_tags: list[str]
    mode: str = "normal"
    items: list[RecItem] = Field(default_factory=list)


class RecommendTool(Tool):
    name = "recommend_subjects"
    description = (
        "根据用户口味推荐 TA 没看过的作品，支持 anime/book/music/game/real。"
        "多路召回（标签 + 图谱：你爱的作品的监督/制作组的其他作品）。"
        "重度用户/挖冷门设 niche=true；想跳出舒适区设 explore=true。"
        "用于『据我口味推荐 / 类似X / 挖点冷门 / 换个口味 / 今天想看治愈的』。"
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

    async def _enrich_ratings(self, ranked_ids: list[tuple[int, dict]]) -> None:
        """对前 N 个缺评分的候选按需补评分（图谱召回的 person_subjects 不带 rating）。"""
        n = 0
        for sid, c in ranked_ids:
            if n >= _ENRICH_TOP:
                break
            if c["rating"].get("score"):
                continue
            n += 1
            try:
                raw = await self.client.get_subject(sid)
                c["rating"] = raw.get("rating") or {}
            except Exception:  # noqa: BLE001
                pass

    async def run(self, args: RecommendArgs) -> ToolResult[RecommendResult]:
        stype = SUBJECT_TYPE[args.subject_type]
        username = args.username or (await self.client.get_me()).get("username")

        items = await self.client.get_all_user_collections(username, stype, None, max_items=_MAX_COLLECT)
        seen = {it["subject"]["id"] for it in items if it.get("subject", {}).get("id")}
        watched = [it for it in items if it.get("type") == 2]
        profile = compute_taste_profile(username, watched)
        all_tags = [t["tag"] for t in profile.top_tags]
        user_tags = {t["tag"]: float(t["weight"]) for t in profile.top_tags[:8]}
        maxw = max(user_tags.values()) if user_tags else 1.0

        mood = _expand_moods(args.tags or [])
        core = all_tags[2:8] if args.explore else all_tags[:6]  # explore：用次级标签拓展
        recall_tags = list(dict.fromkeys(mood + core))[:_MAX_RECALL_TAGS]

        cand: dict[int, dict] = {}
        await self._tag_recall(cand, stype, recall_tags, user_tags, maxw, mood, seen, args.niche)
        if args.use_graph:
            fav_ids = [
                it["subject"]["id"]
                for it in sorted(watched, key=lambda it: -(it.get("rate") or 0))
                if it.get("subject", {}).get("id")
            ]
            await self._graph_recall(cand, stype, fav_ids, seen)

        def affinity(c: dict) -> float:
            return c["weight"] / maxw  # ≈ 加权命中标签数

        def graph_bonus(c: dict) -> float:
            return min(len(c["graph"]), 2) * 0.6  # 封顶 ~1.2，避免压过标签

        # 预排（不含质量）→ 给 top 候选补评分 → 终排（含质量）
        prelim = sorted(cand.items(), key=lambda kv: -(affinity(kv[1]) + graph_bonus(kv[1])))[:25]
        await self._enrich_ratings(prelim)

        def score(c: dict) -> float:
            if args.niche:
                return 0.5 * affinity(c) + 0.5 * graph_bonus(c) + 2.0 * _quality_niche(c["rating"])
            return affinity(c) + graph_bonus(c) + 1.2 * _quality_popular(c["rating"])

        ranked = sorted(prelim, key=lambda kv: -score(kv[1]))
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

        mode = "niche" if args.niche else ("explore" if args.explore else "normal")
        return ToolResult(
            ok=True,
            data=RecommendResult(
                subject_type=args.subject_type, based_on_tags=recall_tags, mode=mode, items=out
            ),
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
        )
