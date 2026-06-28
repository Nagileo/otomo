"""发现 / 预测工具（收口早期 backlog）：评分预测 + 萌点检索 + 分集口碑雷达。

都复用现有 Bangumi client 方法，不新增数据源。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...profile import compute_taste_profile
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

_SUBJECT_T = Literal["anime", "book", "music", "game", "real"]


async def _username(client: BangumiClient, username: str | None) -> str | None:
    if username:
        return username
    try:
        me = await client.get_me()
    except Exception:  # noqa: BLE001
        return None
    return me.get("username") or str(me.get("id")) or None


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
        profile = compute_taste_profile(username, items)
        user_tags = {t["tag"]: float(t["weight"]) for t in profile.top_tags}
        maxw = max(user_tags.values()) if user_tags else 1.0
        matched = [t for t in subj_tags if t in user_tags]
        affinity = sum(user_tags.get(t, 0.0) for t in subj_tags) / maxw  # ≈ 命中加权标签数

        base = global_score if global_score else 7.0
        severity = (user_avg - 7.2) if user_avg is not None else 0.0  # 用户比全站均分严/宽
        predicted = base + min(affinity * 0.35, 1.2) - 0.4 + severity * 0.35
        predicted = round(max(1.0, min(10.0, predicted)), 1)
        conf = "high" if len(matched) >= 3 and len(rated) >= 30 else ("medium" if matched else "low")
        return ToolResult(
            ok=True,
            data=RatingPrediction(
                subject_id=args.subject_id, title=title, predicted_rating=predicted,
                global_score=global_score, user_avg=round(user_avg, 2) if user_avg else None,
                confidence=conf, matched_tags=matched[:8],
                rationale=(
                    f"全站 {global_score or '暂无'}，你均分 {round(user_avg, 1) if user_avg else '未知'}，"
                    f"命中你口味标签 {len(matched)} 个"
                ),
                caveats=["预测是画像级弱信号，不代表真实观感；样本越多越准。"],
            ),
            sources=[Citation(title=f"Bangumi — {title}", url=f"https://bgm.tv/subject/{args.subject_id}", source="bangumi")],
        )


# --------------------------------------------------------------------------- #
# 2) 萌点检索 / 复杂多维筛选
# --------------------------------------------------------------------------- #
class TraitSearchArgs(BaseModel):
    tags: list[str] = Field(..., min_length=1, max_length=8, description="萌点/题材标签组合，如 ['百合','废萌','芳文社']")
    subject_type: _SUBJECT_T = "anime"
    min_score: float = Field(0.0, ge=0.0, le=10.0, description="最低 Bangumi 评分")
    year_from: int | None = Field(None, description="起始年份（含）")
    year_to: int | None = Field(None, description="结束年份（含）")
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
            "", SUBJECT_TYPE[args.subject_type], sort="rank", limit=min(args.limit * 2, 50),
            tags=args.tags, air_date=air_date,
        )
        items: list[TraitItem] = []
        for s in (raw.get("data") or []):
            if not s.get("id"):
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
        notes = [f"标签交集 {args.tags}，按 Bangumi rank 排序"]
        if args.min_score:
            notes.append(f"已过滤评分 < {args.min_score}")
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


class EpisodePoint(BaseModel):
    sort: float
    ep: int | None = None
    name: str = ""
    comments: int = 0
    airdate: str | None = None


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

    async def run(self, args: EpisodeRadarArgs) -> ToolResult[EpisodeRadarResult]:
        raw = await self.client.get_episodes(args.subject_id, ep_type=0, limit=200)
        rows = raw.get("data") or []
        points: list[EpisodePoint] = []
        for e in rows:
            sort = e.get("sort")
            if sort is None:
                continue
            points.append(EpisodePoint(
                sort=float(sort), ep=e.get("ep"), name=e.get("name_cn") or e.get("name") or "",
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
        return ToolResult(
            ok=True,
            data=EpisodeRadarResult(
                subject_id=args.subject_id, total=raw.get("total") or len(rows),
                curve=points, peaks=peaks, filtered_by_progress=filtered, notes=notes,
            ),
            sources=[Citation(title=f"subject {args.subject_id} · 分集热度", url=f"https://bgm.tv/subject/{args.subject_id}/ep", source="bangumi")],
        )


def build_discovery_tools(client: BangumiClient) -> list[Tool]:
    return [PredictMyRatingTool(client), SearchByTraitsTool(client), EpisodeBuzzRadarTool(client)]
