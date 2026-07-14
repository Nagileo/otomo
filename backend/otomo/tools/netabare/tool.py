"""Netaba.re 口碑走势源（用户提名接入，2026-07-14 实测）。

api.netaba.re/subject/{id} 返回自 2021 年起的**每日快照时间序列**（收藏五态人数 +
1-10 评分分布），孤独摇滚有 1900+ 条——这是 Bangumi 官方 API 完全没有的维度：
「这部番的口碑是怎么涨/崩的」「开播前期待度多少」「最近还有没有人在看」。

克制接入：只做条目走势一个工具；原始序列降采样到 ≤60 点供前端画折线；
数值都标 source=netaba.re（第三方快照站，非官方数据）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ...config import settings
from ...agent.contracts import Citation, Tool, ToolResult
from .._cache import TTLCache

_API = "https://api.netaba.re"
_CACHE = TTLCache(ttl=60 * 60 * 6)  # 快照按天更新，6h 缓存足够


class SubjectTrendArgs(BaseModel):
    subject_id: int = Field(..., description="Bangumi subject_id")
    title: str = Field("", description="作品名（仅用于展示，不参与查询）")
    days: int = Field(0, ge=0, le=3650, description="只看最近 N 天；0=全历史")


class TrendPoint(BaseModel):
    date: str
    score: float | None = None       # 由评分分布计算的加权均分
    rating_total: int = 0
    collect_total: int = 0           # 五态收藏总人数
    doing: int = 0                   # 在看人数（热度活性）
    wish: int = 0


class SubjectTrendResult(BaseModel):
    subject_id: int
    title: str
    points: list[TrendPoint] = Field(default_factory=list)   # 降采样 ≤60 点，画折线用
    current_score: float | None = None
    score_change_30d: float | None = None
    score_change_90d: float | None = None
    collect_change_30d: int | None = None
    pre_air_wish: int | None = None       # 开播前最后一个快照的想看数（期待度）
    rating_distribution: dict[str, int] = Field(default_factory=dict)  # 1-10 分布（柱状图用）
    rating_std: float | None = None       # 分布标准差
    controversy: str = ""                 # 争议度标签（按标准差分档的启发式，非官方口径）
    distribution_source: str = "netabare"  # bangumi=官方实时 / netabare=快照(滞后~1天)。实测两者仅差滞后
    first_recorded: str = ""
    last_recorded: str = ""
    summary: str = ""
    netabare_url: str = ""
    caveats: list[str] = Field(default_factory=list)


def _score_of(rating: dict[str, Any] | None) -> tuple[float | None, int]:
    if not rating or not rating.get("count"):
        return None, 0
    counts = {int(k): int(v) for k, v in rating["count"].items()}
    total = sum(counts.values())
    if total <= 0:
        return None, 0
    return round(sum(r * c for r, c in counts.items()) / total, 2), total


def _controversy_label(std: float | None) -> str:
    if std is None:
        return ""
    if std < 1.0:
        return "口碑集中"
    if std < 1.3:
        return "基本一致"
    if std < 1.6:
        return "略有分歧"
    if std < 2.0:
        return "莫衷一是"
    return "两极分化"


def _distribution_stats(rating: dict[str, Any] | None) -> tuple[dict[str, int], float | None]:
    if not rating or not rating.get("count"):
        return {}, None
    counts = {str(k): int(v) for k, v in rating["count"].items()}
    total = sum(counts.values())
    if total <= 1:
        return counts, None
    mean = sum(int(r) * c for r, c in counts.items()) / total
    var = sum(c * (int(r) - mean) ** 2 for r, c in counts.items()) / total
    return counts, round(var ** 0.5, 4)


def _point(rec: dict[str, Any]) -> TrendPoint:
    collect = rec.get("collect") or {}
    score, total = _score_of(rec.get("rating"))
    return TrendPoint(
        date=str(rec.get("recordedAt", ""))[:10],
        score=score,
        rating_total=total,
        collect_total=sum(int(v or 0) for v in collect.values()),
        doing=int(collect.get("doing") or 0),
        wish=int(collect.get("wish") or 0),
    )


def downsample(points: list[TrendPoint], max_points: int = 60) -> list[TrendPoint]:
    """均匀降采样但保住首尾（走势图两端最重要）。"""
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    idx = sorted({round(i * step) for i in range(max_points)} | {0, len(points) - 1})
    return [points[i] for i in idx]


def _nearest_before(points: list[TrendPoint], cutoff: str) -> TrendPoint | None:
    prev = None
    for p in points:
        if p.date > cutoff:
            break
        prev = p
    return prev


def build_trend(subject_id: int, payload: dict[str, Any], days: int = 0) -> SubjectTrendResult:
    subject = payload.get("subject") or {}
    title = subject.get("name_cn") or subject.get("name") or f"subject {subject_id}"
    history = payload.get("history") or []
    all_points = [_point(rec) for rec in history if rec.get("recordedAt")]
    all_points.sort(key=lambda p: p.date)

    air_date = str(subject.get("air_date", ""))[:10]
    pre_air = _nearest_before(all_points, air_date) if air_date else None

    points = all_points
    if days and all_points:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        points = [p for p in all_points if p.date >= cutoff] or all_points[-1:]

    last = points[-1] if points else None
    now = datetime.now(timezone.utc)
    p30 = _nearest_before(all_points, (now - timedelta(days=30)).strftime("%Y-%m-%d"))
    p90 = _nearest_before(all_points, (now - timedelta(days=90)).strftime("%Y-%m-%d"))

    score_30 = round(last.score - p30.score, 2) if last and last.score is not None and p30 and p30.score is not None else None
    score_90 = round(last.score - p90.score, 2) if last and last.score is not None and p90 and p90.score is not None else None
    collect_30 = (last.collect_total - p30.collect_total) if last and p30 else None

    bits: list[str] = []
    if last and last.score is not None:
        bits.append(f"当前均分 {last.score}（{last.rating_total} 人评分）")
    if score_30 is not None:
        bits.append(f"近30天 {'+' if score_30 >= 0 else ''}{score_30}")
    if score_90 is not None:
        bits.append(f"近90天 {'+' if score_90 >= 0 else ''}{score_90}")
    if collect_30 is not None:
        bits.append(f"30天新增收藏 {collect_30}")
    if pre_air and pre_air.wish:
        bits.append(f"开播前想看 {pre_air.wish} 人")

    last_raw = next((rec for rec in reversed(history) if rec.get("rating")), None)
    distribution, std = _distribution_stats((last_raw or {}).get("rating"))
    return SubjectTrendResult(
        subject_id=subject_id,
        title=title,
        points=downsample(points),
        rating_distribution=distribution,
        rating_std=std,
        controversy=_controversy_label(std),
        current_score=last.score if last else None,
        score_change_30d=score_30,
        score_change_90d=score_90,
        collect_change_30d=collect_30,
        pre_air_wish=pre_air.wish if pre_air else None,
        first_recorded=all_points[0].date if all_points else "",
        last_recorded=all_points[-1].date if all_points else "",
        summary="；".join(bits) or "暂无走势数据",
        netabare_url=f"https://netaba.re/subject/{subject_id}",
        caveats=[
            "数据来自 netaba.re（第三方每日快照站，2021 年起），非 Bangumi 官方；均分由评分分布加权计算。",
        ],
    )


class SubjectTrendTool(Tool):
    name = "get_subject_trend"
    description = (
        "查询作品的口碑/热度走势（netaba.re 每日快照）：评分均分随时间变化（涨/崩）、"
        "30/90 天分数变动、收藏增长、在看人数、开播前期待度（想看数）。"
        "回答'这番口碑崩了吗/评分是涨是跌/当年多少人期待'类问题的 canonical 走势源。"
    )
    args_model = SubjectTrendArgs
    result_model = SubjectTrendResult

    def __init__(self, bangumi_client: Any | None = None) -> None:
        # 分布图的 canonical 源：官方实时 rating.count（netaba 快照滞后 1-2 天，且新番可能未建档）
        self._bangumi = bangumi_client

    async def run(self, args: SubjectTrendArgs) -> ToolResult[SubjectTrendResult]:
        cache_key = f"trend:{args.subject_id}"
        payload = _CACHE.get(cache_key)
        if payload is None:
            try:
                async with httpx.AsyncClient(
                    timeout=settings.http_timeout,
                    headers={"User-Agent": settings.bangumi_user_agent},
                ) as client:
                    resp = await client.get(f"{_API}/subject/{args.subject_id}")
                    resp.raise_for_status()
                    payload = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                return ToolResult(ok=False, error=f"netaba.re 走势获取失败：{type(e).__name__}")
            _CACHE.set(cache_key, payload)
        data = build_trend(args.subject_id, payload, days=args.days)
        if args.title and (not data.title or data.title.startswith("subject ")):
            data.title = args.title
        # 分布优先官方实时（实测 netaba 快照与官方仅差 1-2 天滞后；新番官方先有数据）
        if self._bangumi is not None:
            try:
                subject = await self._bangumi.get_subject(args.subject_id)
                counts = (subject.get("rating") or {}).get("count") or {}
                if counts:
                    dist, std = _distribution_stats({"count": counts})
                    data.rating_distribution = dist
                    data.rating_std = std
                    data.controversy = _controversy_label(std)
                    data.distribution_source = "bangumi"
                    if not data.title or data.title.startswith("subject "):
                        data.title = subject.get("name_cn") or subject.get("name") or data.title
            except Exception:  # noqa: BLE001 — 官方源失败退回快照分布
                pass
        return ToolResult(
            ok=True,
            data=data,
            sources=[Citation(title=f"netaba.re · {data.title}", url=data.netabare_url, source="netabare")],
        )


class RatingMoversArgs(BaseModel):
    direction: str = Field("all", description="up=近30天口碑上涨最快 / down=下跌最快（崩） / done=近期完结表现 / all=三榜都给")
    limit: int = Field(8, ge=1, le=10, description="每榜条数")
    include_season_analysis: bool = Field(False, description="是否附带当季评分格局分析（netaba.re 的 AI 生成文本）")
    season_year: int | None = Field(None, description="季度分析年份；默认当前季")
    season_month: int | None = Field(None, ge=1, le=12, description="季度分析月份（1/4/7/10）")


class MoverItem(BaseModel):
    subject_id: int
    title: str
    delta_score: float           # 30 天均分变化（正=涨，负=崩）
    current_score: float | None = None
    rating_total: int = 0


class RatingMoversResult(BaseModel):
    up: list[MoverItem] = Field(default_factory=list)
    down: list[MoverItem] = Field(default_factory=list)
    done: list[MoverItem] = Field(default_factory=list)
    season_analysis: dict[str, str] = Field(default_factory=dict)  # score/rank/divisive/popularity 四篇
    caveats: list[str] = Field(default_factory=list)


def _mover(entry: dict[str, Any]) -> MoverItem:
    subject = entry.get("subject") or {}
    history = entry.get("history") or []
    last_score, last_total = (None, 0)
    for rec in reversed(history):
        sc, tot = _score_of(rec.get("rating"))
        if sc is not None:
            last_score, last_total = sc, tot
            break
    return MoverItem(
        subject_id=int(entry.get("bgmId") or 0),
        title=subject.get("name_cn") or subject.get("name") or f"subject {entry.get('bgmId')}",
        delta_score=round(float(entry.get("score") or 0), 2),
        current_score=last_score,
        rating_total=last_total,
    )


class RatingMoversTool(Tool):
    name = "get_rating_movers"
    description = (
        "近 30 天口碑异动榜（netaba.re）：评分上涨最快 / 下跌最快（崩了）/ 近期完结作品表现；"
        "可附带当季评分格局分析。回答'最近什么番口碑崩了/黑马是谁/这季评分格局'类问题。"
    )
    args_model = RatingMoversArgs
    result_model = RatingMoversResult

    async def run(self, args: RatingMoversArgs) -> ToolResult[RatingMoversResult]:
        payload = _CACHE.get("trending")
        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout, headers={"User-Agent": settings.bangumi_user_agent}
            ) as client:
                if payload is None:
                    resp = await client.get(f"{_API}/trending")
                    resp.raise_for_status()
                    payload = resp.json()
                    _CACHE.set("trending", payload)
                analysis: dict[str, str] = {}
                if args.include_season_analysis:
                    now = datetime.now(timezone.utc)
                    year = args.season_year or now.year
                    month = args.season_month or ((now.month - 1) // 3 * 3 + 1)
                    key = f"season:{year}:{month}"
                    analysis = _CACHE.get(key) or {}
                    if not analysis:
                        r2 = await client.get(f"{_API}/season/{year}/{month}/analysis")
                        if r2.status_code == 200:
                            analysis = {k: str(v) for k, v in (r2.json() or {}).items() if isinstance(v, str)}
                            _CACHE.set(key, analysis)
        except (httpx.HTTPError, ValueError) as e:
            return ToolResult(ok=False, error=f"netaba.re 榜单获取失败：{type(e).__name__}")

        def cut(key: str) -> list[MoverItem]:
            return [_mover(x) for x in (payload.get(key) or [])[: args.limit]]

        data = RatingMoversResult(
            up=cut("up") if args.direction in ("all", "up") else [],
            down=cut("down") if args.direction in ("all", "down") else [],
            done=cut("done") if args.direction in ("all", "done") else [],
            season_analysis=analysis,
            caveats=[
                "数据来自 netaba.re（第三方每日快照站）；delta 为近 30 天加权均分变化。",
                *(["季度分析为 netaba.re 的 AI 生成文本，属第三方观点，不作 canonical 事实。"] if analysis else []),
            ],
        )
        return ToolResult(
            ok=True,
            data=data,
            sources=[Citation(title="netaba.re 口碑异动榜", url="https://netaba.re/trending", source="netabare")],
        )


def build_netabare_tools(bangumi_client: Any | None = None) -> list[Tool]:
    return [SubjectTrendTool(bangumi_client), RatingMoversTool()]
