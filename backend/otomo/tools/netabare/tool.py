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

    return SubjectTrendResult(
        subject_id=subject_id,
        title=title,
        points=downsample(points),
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
        return ToolResult(
            ok=True,
            data=data,
            sources=[Citation(title=f"netaba.re · {data.title}", url=data.netabare_url, source="netabare")],
        )


def build_netabare_tools() -> list[Tool]:
    return [SubjectTrendTool()]
