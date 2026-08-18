"""Build user-facing recommendation explanations from the final ranked item.

The recommender used to create prose before review enrichment and series-entry
resolution.  Keeping this small module separate makes one rule explicit: the
title, ranking score, explanation and supporting evidence are all derived from
the same final item.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RecommendationClaim(BaseModel):
    kind: Literal["fit", "risk", "quality", "provenance"]
    text: str
    support: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"


_SCENARIO_LABELS = {
    "tonight": "符合“今晚看”的低启动成本目标",
    "season": "符合本轮当季追番方向",
    "backlog": "来自你的想看列表，适合本轮清理库存",
    "gal_intro": "符合 Galgame 入门方向",
    "cross_media": "符合本轮跨媒体延伸方向",
}


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _evidence_sources(item: Any) -> list[str]:
    return _unique([
        *[str(getattr(ev, "source", "")) for ev in getattr(item, "evidence", [])],
        *[str(source) for source in (getattr(item, "review_sources", []) or [])],
    ])[:4]


def refresh_item_explanation(item: Any, scenario: str) -> None:
    """Refresh explanation fields after all ranking/evidence mutations finish."""
    fit: list[str] = []
    claims: list[RecommendationClaim] = []

    explicit = list(getattr(item, "explicit_tag_matches", []) or [])
    scenario_hits = list(getattr(item, "scenario_tag_matches", []) or [])
    feedback_hits = list(getattr(item, "feedback_tag_matches", []) or [])
    profile_hits = list(getattr(item, "profile_tag_matches", []) or [])
    aspect_hits = list(getattr(item, "aspect_matches", []) or [])

    if explicit:
        text = "命中你这轮明确想看的：" + "、".join(explicit[:4])
        fit.append(text)
        claims.append(RecommendationClaim(
            kind="fit",
            text=text,
            support=["本轮明确偏好", "Bangumi 条目标签：" + "、".join(explicit[:4])],
            confidence="high",
        ))
    if scenario_hits:
        label = _SCENARIO_LABELS.get(scenario, "符合本轮场景方向")
        text = f"{label}：" + "、".join(scenario_hits[:3])
        fit.append(text)
        claims.append(RecommendationClaim(
            kind="fit",
            text=text,
            support=["本轮场景偏好", "Bangumi 条目标签：" + "、".join(scenario_hits[:3])],
            confidence="medium",
        ))
    if aspect_hits:
        text = aspect_hits[0]
        fit.append(text)
        claims.append(RecommendationClaim(
            kind="fit",
            text=text,
            support=_evidence_sources(item) or ["长期口味画像"],
            confidence="medium" if _evidence_sources(item) else "low",
        ))
    if profile_hits:
        text = "与你的长期口味相近：" + "、".join(profile_hits[:3])
        fit.append(text)
        claims.append(RecommendationClaim(
            kind="fit",
            text=text,
            support=["长期收藏画像", "Bangumi 条目标签：" + "、".join(profile_hits[:3])],
            confidence="medium",
        ))
    if feedback_hits:
        text = "近期反馈提供了轻量加分：" + "、".join(feedback_hits[:3])
        fit.append(text)
        claims.append(RecommendationClaim(
            kind="fit",
            text=text,
            support=["近期推荐反馈（弱信号）"],
            confidence="low",
        ))
    if not fit:
        fit.append("这是口味邻近候选，当前没有足够强的个性化命中，建议先看简介或第一集确认。")

    risks = _unique([
        *(getattr(item, "constraint_warnings", []) or []),
        *(getattr(item, "aspect_warnings", []) or []),
    ])[:5]
    for text in risks:
        claims.append(RecommendationClaim(
            kind="risk",
            text=text,
            support=_evidence_sources(item) or ["本轮约束/长期避雷"],
            confidence="medium",
        ))

    consensus = str(getattr(item, "review_consensus", "") or "").strip()
    if consensus:
        claims.append(RecommendationClaim(
            kind="quality",
            text=consensus,
            support=_evidence_sources(item),
            confidence="medium" if _evidence_sources(item) else "low",
        ))

    recalled = _unique(list(getattr(item, "recall_signals", []) or []))[:5]
    for text in recalled:
        claims.append(RecommendationClaim(
            kind="provenance",
            text=text,
            support=["候选召回路径"],
            confidence="low",
        ))

    item.fit_points = fit[:5]
    item.risks = risks
    item.why_recalled = recalled
    item.claims = claims[:12]
