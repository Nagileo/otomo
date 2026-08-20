"""Build user-facing recommendation explanations from the final ranked item.

The recommender used to create prose before review enrichment and series-entry
resolution.  Keeping this small module separate makes one rule explicit: the
title, ranking score, explanation and supporting evidence are all derived from
the same final item.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


RecommendationSupportKind = Literal[
    "explicit_preference",
    "scenario_preference",
    "subject_tag",
    "profile_preference",
    "profile_aspect",
    "feedback_preference",
    "review_aspect",
    "review_summary",
    "constraint",
    "recall",
]


class RecommendationSupport(BaseModel):
    """A machine-checkable fact behind one user-facing recommendation claim.

    ``label`` is presentation-only.  The other fields are deliberately small
    and must be reconcilable with the final ``RecItem`` by
    :func:`audit_item_explanation`; a source name by itself is not evidence.
    """

    kind: RecommendationSupportKind
    value: str
    source: str = ""
    field: str = ""
    subject_id: int | None = None
    label: str = ""


class RecommendationClaim(BaseModel):
    kind: Literal["fit", "risk", "quality", "provenance"]
    text: str
    support: list[str] = Field(default_factory=list)
    evidence: list[RecommendationSupport] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"


_SCENARIO_LABELS = {
    "tonight": "符合“今晚看”的低启动成本目标",
    "season": "符合本轮当季追番方向",
    "backlog": "来自你的想看列表，适合本轮清理库存",
    "gal_intro": "符合 Galgame 入门方向",
    "cross_media": "符合本轮跨媒体延伸方向",
}

UNVERIFIED_EXPLANATION = "个性化理由未通过证据一致性校验；这部作品只作为候选保留，请先查看作品资料。"


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _evidence_sources(item: Any) -> list[str]:
    return _unique([
        *[str(getattr(ev, "source", "")) for ev in getattr(item, "evidence", [])],
        *[str(source) for source in (getattr(item, "review_sources", []) or [])],
    ])[:4]


def _fact(
    kind: RecommendationSupportKind,
    value: str,
    *,
    item: Any,
    label: str,
    source: str = "",
    field: str = "",
) -> RecommendationSupport:
    return RecommendationSupport(
        kind=kind,
        value=value,
        source=source,
        field=field,
        subject_id=int(getattr(item, "id", 0) or 0) or None,
        label=label,
    )


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
            evidence=[
                *[
                    _fact("explicit_preference", tag, item=item, label=f"本轮明确偏好：{tag}")
                    for tag in explicit[:4]
                ],
                *[
                    _fact(
                        "subject_tag", tag, item=item, source="Bangumi", field="tags",
                        label=f"Bangumi 条目标签：{tag}",
                    )
                    for tag in explicit[:4]
                ],
            ],
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
            evidence=[
                *[
                    _fact("scenario_preference", tag, item=item, label=f"本轮场景偏好：{tag}")
                    for tag in scenario_hits[:3]
                ],
                *[
                    _fact(
                        "subject_tag", tag, item=item, source="Bangumi", field="tags",
                        label=f"Bangumi 条目标签：{tag}",
                    )
                    for tag in scenario_hits[:3]
                ],
            ],
            confidence="medium",
        ))
    if aspect_hits:
        text = aspect_hits[0]
        fit.append(text)
        review_grounded = text.startswith("评价证据支持")
        sources = _evidence_sources(item) if review_grounded else []
        claims.append(RecommendationClaim(
            kind="fit",
            text=text,
            support=sources or ["长期口味画像（由条目标签映射）"],
            evidence=[
                _fact(
                    "review_aspect", text, item=item, source=source, field="aspect_summary",
                    label=f"{source} 评价维度：{text}",
                )
                for source in sources
            ] if review_grounded else [
                _fact(
                    "profile_aspect", text, item=item, source="长期口味画像",
                    field="candidate_aspects", label=f"长期画像维度：{text}",
                )
            ],
            confidence="medium" if sources else "low",
        ))
    if profile_hits:
        text = "与你的长期口味相近：" + "、".join(profile_hits[:3])
        fit.append(text)
        claims.append(RecommendationClaim(
            kind="fit",
            text=text,
            support=["长期收藏画像", "Bangumi 条目标签：" + "、".join(profile_hits[:3])],
            evidence=[
                *[
                    _fact("profile_preference", tag, item=item, label=f"长期收藏画像：{tag}")
                    for tag in profile_hits[:3]
                ],
                *[
                    _fact(
                        "subject_tag", tag, item=item, source="Bangumi", field="tags",
                        label=f"Bangumi 条目标签：{tag}",
                    )
                    for tag in profile_hits[:3]
                ],
            ],
            confidence="medium",
        ))
    if feedback_hits:
        text = "近期反馈提供了轻量加分：" + "、".join(feedback_hits[:3])
        fit.append(text)
        claims.append(RecommendationClaim(
            kind="fit",
            text=text,
            support=["近期推荐反馈（弱信号）"],
            evidence=[
                _fact("feedback_preference", tag, item=item, label=f"近期反馈：{tag}")
                for tag in feedback_hits[:3]
            ],
            confidence="low",
        ))
    if not fit:
        fit.append("这是口味邻近候选，当前没有足够强的个性化命中，建议先看简介或第一集确认。")

    risks = _unique([
        *(getattr(item, "constraint_warnings", []) or []),
        *(getattr(item, "aspect_warnings", []) or []),
    ])[:5]
    for text in risks:
        review_grounded = text.startswith("评价证据触及")
        sources = _evidence_sources(item) if review_grounded else []
        evidence_kind = (
            "review_aspect" if review_grounded else
            "profile_aspect" if text in (getattr(item, "aspect_warnings", []) or []) else
            "constraint"
        )
        claims.append(RecommendationClaim(
            kind="risk",
            text=text,
            support=sources or ["本轮约束/长期避雷"],
            evidence=[
                _fact(
                    evidence_kind, text, item=item,
                    source="长期口味画像" if evidence_kind == "profile_aspect" else source,
                    field=(
                        "aspect_summary" if evidence_kind == "review_aspect" else
                        "candidate_aspects" if evidence_kind == "profile_aspect" else
                        "constraints"
                    ),
                    label=f"{source}：{text}" if source else f"约束：{text}",
                )
                for source in (sources or [""])
            ],
            confidence="medium",
        ))

    consensus = str(getattr(item, "review_consensus", "") or "").strip()
    if consensus:
        claims.append(RecommendationClaim(
            kind="quality",
            text=consensus,
            support=_evidence_sources(item),
            evidence=[
                _fact(
                    "review_summary", consensus, item=item, source=source, field="review_consensus",
                    label=f"{source} 评价汇总",
                )
                for source in _evidence_sources(item)
            ],
            confidence="medium" if _evidence_sources(item) else "low",
        ))

    recalled = _unique(list(getattr(item, "recall_signals", []) or []))[:5]
    for text in recalled:
        claims.append(RecommendationClaim(
            kind="provenance",
            text=text,
            support=["候选召回路径"],
            evidence=[_fact("recall", text, item=item, label=f"召回路径：{text}")],
            confidence="low",
        ))

    item.fit_points = fit[:5]
    item.risks = risks
    item.why_recalled = recalled
    item.claims = claims[:12]


def _fact_is_grounded(item: Any, fact: RecommendationSupport) -> bool:
    """Reconcile a typed support fact against the final item, not its label."""
    if fact.subject_id is not None and fact.subject_id != int(getattr(item, "id", 0) or 0):
        return False
    value = fact.value.strip()
    sources = set(_evidence_sources(item))
    if fact.kind == "explicit_preference":
        return value in (getattr(item, "explicit_tag_matches", []) or [])
    if fact.kind == "scenario_preference":
        return value in (getattr(item, "scenario_tag_matches", []) or [])
    if fact.kind == "subject_tag":
        tags = {
            *(getattr(item, "explicit_tag_matches", []) or []),
            *(getattr(item, "scenario_tag_matches", []) or []),
            *(getattr(item, "profile_tag_matches", []) or []),
            *(getattr(item, "diversity_tags", []) or []),
        }
        return fact.source == "Bangumi" and fact.field == "tags" and value in tags
    if fact.kind == "profile_preference":
        return value in (getattr(item, "profile_tag_matches", []) or [])
    if fact.kind == "profile_aspect":
        values = {
            *(getattr(item, "aspect_matches", []) or []),
            *(getattr(item, "aspect_warnings", []) or []),
        }
        return value in values and fact.source == "长期口味画像" and fact.field == "candidate_aspects"
    if fact.kind == "feedback_preference":
        return value in (getattr(item, "feedback_tag_matches", []) or [])
    if fact.kind == "review_aspect":
        values = {
            *(getattr(item, "aspect_matches", []) or []),
            *(getattr(item, "aspect_warnings", []) or []),
        }
        return value in values and bool(fact.source) and fact.source in sources
    if fact.kind == "review_summary":
        return (
            value == str(getattr(item, "review_consensus", "") or "").strip()
            and bool(fact.source)
            and fact.source in sources
        )
    if fact.kind == "constraint":
        values = {
            *(getattr(item, "constraint_warnings", []) or []),
            *(getattr(item, "risks", []) or []),
        }
        return value in values
    if fact.kind == "recall":
        return value in (getattr(item, "recall_signals", []) or [])
    return False


def audit_item_explanation(item: Any) -> list[str]:
    """Verify that visible prose is derived from the final item's claims.

    Every visible sentence must have a matching claim and every claim must
    carry typed facts that reconcile against the *final* item.  Human-readable
    source labels alone are explicitly insufficient.
    """
    issues: list[str] = []
    claims = list(getattr(item, "claims", []) or [])
    claim_by_text = {str(getattr(claim, "text", "")): claim for claim in claims}
    fallback = "这是口味邻近候选，当前没有足够强的个性化命中，建议先看简介或第一集确认。"
    for sentence in list(getattr(item, "fit_points", []) or []):
        if sentence == fallback:
            continue
        claim = claim_by_text.get(str(sentence))
        if claim is None or getattr(claim, "kind", "") != "fit":
            issues.append(f"适配理由没有对应证据声明：{sentence}")
        elif not list(getattr(claim, "support", []) or []):
            issues.append(f"适配理由缺少支撑来源：{sentence}")
    for sentence in list(getattr(item, "risks", []) or []):
        claim = claim_by_text.get(str(sentence))
        if claim is None or getattr(claim, "kind", "") != "risk":
            issues.append(f"风险提示没有对应证据声明：{sentence}")
        elif not list(getattr(claim, "support", []) or []):
            issues.append(f"风险提示缺少支撑来源：{sentence}")
    for claim in claims:
        if not list(getattr(claim, "support", []) or []):
            issues.append(f"{getattr(claim, 'kind', 'unknown')} 声明缺少支撑来源：{getattr(claim, 'text', '')}")
        facts = [RecommendationSupport.model_validate(row) for row in (getattr(claim, "evidence", []) or [])]
        if not facts:
            issues.append(f"{getattr(claim, 'kind', 'unknown')} 声明缺少可核对证据：{getattr(claim, 'text', '')}")
            continue
        ungrounded = [fact for fact in facts if not _fact_is_grounded(item, fact)]
        if ungrounded:
            labels = "、".join((fact.label or f"{fact.kind}:{fact.value}") for fact in ungrounded[:3])
            issues.append(f"{getattr(claim, 'kind', 'unknown')} 声明存在未对齐证据：{labels}")
    breakdown = dict(getattr(item, "score_breakdown", {}) or {})
    if breakdown and abs(sum(float(value) for value in breakdown.values()) - float(getattr(item, "score", 0))) > 0.02:
        issues.append("最终分数与分项加总不一致")
    return list(dict.fromkeys(issues))[:12]


def suppress_unverified_explanation(item: Any, issues: list[str]) -> None:
    """Hide questionable prose from every presentation surface.

    The original integrity issues remain on the item for evaluation and
    operator diagnostics.  Web, Discord, and the answer model only receive a
    conservative fallback instead of a claim whose typed facts did not match.
    """
    if not issues:
        return
    item.fit_points = [UNVERIFIED_EXPLANATION]
    item.risks = []
    item.why_recalled = []
    item.claims = []
