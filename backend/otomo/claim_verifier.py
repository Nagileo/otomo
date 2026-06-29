"""Claim-level answer verifier.

This verifier is intentionally evidence-local: it does not call external APIs.
It aligns final-answer claims against observations already produced in the same
agent run. Unsupported claims are a reward signal, not a hard runtime failure.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

ClaimKind = Literal[
    "canonical_fact",
    "discourse_summary",
    "preference_inference",
    "spoiler_sensitive",
    "unknown",
]


class ClaimEvidence(BaseModel):
    source: str
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    text: str = ""


class VerifiedClaim(BaseModel):
    text: str
    kind: ClaimKind
    supported: bool
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    evidence: list[ClaimEvidence] = Field(default_factory=list)
    note: str = ""


class ClaimCheckResult(BaseModel):
    claims: list[VerifiedClaim] = Field(default_factory=list)
    support_rate: float = Field(0.0, ge=0.0, le=1.0)
    supported_count: int = 0
    unsupported_count: int = 0
    unverifiable_count: int = 0
    caveats: list[str] = Field(default_factory=list)


_SOURCE_TERMS = ("Bangumi", "批判空间", "ErogameScape", "VNDB", "AniList", "MusicBrainz", "B站", "yuc", "萌娘", "维基")
_FACT_TERMS = (
    "制作", "公司", "监督", "导演", "脚本", "原作", "声优", "CV", "配音", "播出",
    "发售", "年份", "评分", "排名", "rank", "集", "话", "staff", "角色",
)
_DISCOURSE_TERMS = ("口碑", "短评", "评论", "讨论", "观众", "大家", "圈", "评价", "争议", "氛围")
_INFERENCE_TERMS = ("推荐", "适合你", "你可能", "我觉得", "可以试", "口味", "画像", "偏好", "雷区", "好球")
_SPOILER_TERMS = ("结局", "反转", "真相", "后续", "剧情", "死", "黑幕", "最终")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _flatten(value: Any, limit: int = 14000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = str(value)
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def observation_documents(observations: list[dict[str, Any]]) -> list[dict[str, str]]:
    docs: list[dict[str, str]] = []
    for obs in observations:
        name = str(obs.get("name") or "observation")
        chunks = [str(obs.get("summary") or "")]
        if obs.get("data") is not None:
            chunks.append(_flatten(obs.get("data")))
        for source in obs.get("sources") or []:
            if isinstance(source, dict):
                chunks.append(" ".join(str(source.get(k) or "") for k in ("source", "title", "url")))
        for ent in obs.get("entities") or []:
            if isinstance(ent, dict):
                chunks.append(" ".join(str(ent.get(k) or "") for k in ("type", "id", "name")))
                aliases = ent.get("aliases") or []
                if isinstance(aliases, list):
                    chunks.extend(str(x) for x in aliases[:8])
        docs.append({"source": name, "text": "\n".join(chunks)})
    return docs


def split_claims(answer: str, limit: int = 18) -> list[str]:
    lines = []
    for raw in re.split(r"[\n。！？!?；;]+", answer):
        text = raw.strip(" \t\r-*•0123456789.、：:")
        if len(text) < 8:
            continue
        if text.startswith(("来源", "以上", "总结一句", "一句话")) and len(text) < 28:
            continue
        lines.append(text[:180])
    deduped: list[str] = []
    seen = set()
    for line in lines:
        key = _norm(line)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(line)
        if len(deduped) >= limit:
            break
    return deduped


def classify_claim(text: str) -> ClaimKind:
    if any(t in text for t in _SPOILER_TERMS):
        return "spoiler_sensitive"
    if any(t in text for t in _DISCOURSE_TERMS):
        return "discourse_summary"
    if any(t in text for t in _INFERENCE_TERMS):
        return "preference_inference"
    if any(t in text for t in _FACT_TERMS) or re.search(r"\d", text):
        return "canonical_fact"
    return "unknown"


def _anchors(text: str) -> list[str]:
    anchors: list[str] = []
    anchors.extend(re.findall(r"《([^》]{2,40})》", text))
    anchors.extend(re.findall(r"[A-Za-z][A-Za-z0-9_ .:'-]{2,40}", text))
    anchors.extend(re.findall(r"\d+(?:\.\d+)?", text))
    for term in _SOURCE_TERMS + _FACT_TERMS:
        if term in text:
            anchors.append(term)
    # Chinese noun-ish chunks. Keep conservative to avoid every sentence matching.
    anchors.extend(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{3,12}", text)[:6])
    out = []
    for anchor in anchors:
        anchor = anchor.strip()
        if len(anchor) >= 2 and anchor not in out:
            out.append(anchor)
    return out[:10]


def _evidence_for(claim: str, docs: list[dict[str, str]]) -> list[ClaimEvidence]:
    anchors = _anchors(claim)
    if not anchors:
        return []
    evidence: list[ClaimEvidence] = []
    for doc in docs:
        hay = _norm(doc["text"])
        if not hay:
            continue
        hits = [a for a in anchors if _norm(a) and _norm(a) in hay]
        if not hits:
            continue
        confidence = min(1.0, 0.25 + 0.18 * len(hits))
        evidence.append(
            ClaimEvidence(
                source=doc["source"],
                confidence=confidence,
                text="命中：" + "、".join(hits[:5]),
            )
        )
    evidence.sort(key=lambda x: x.confidence, reverse=True)
    return evidence[:4]


def verify_answer_claims(answer: str, observations: list[dict[str, Any]]) -> ClaimCheckResult:
    docs = observation_documents(observations)
    claims: list[VerifiedClaim] = []
    for text in split_claims(answer):
        kind = classify_claim(text)
        evidence = _evidence_for(text, docs)
        if kind == "preference_inference" and evidence:
            supported = True
            note = "偏好推断已对齐到本轮画像/推荐证据。"
        elif kind == "preference_inference":
            supported = False
            note = "偏好推断没有命中本轮画像或推荐证据。"
        elif kind == "unknown":
            supported = bool(evidence)
            note = "一般陈述；证据命中较弱。" if evidence else "一般陈述，未找到本轮证据。"
        else:
            supported = bool(evidence)
            note = "已命中本轮 observation。" if evidence else "未在本轮 observation 中找到直接证据。"
        confidence = max([x.confidence for x in evidence], default=0.0)
        claims.append(
            VerifiedClaim(
                text=text,
                kind=kind,
                supported=supported,
                confidence=confidence,
                evidence=evidence,
                note=note,
            )
        )
    supported_count = sum(1 for x in claims if x.supported)
    unsupported_count = sum(1 for x in claims if not x.supported and x.kind != "unknown")
    unverifiable_count = sum(1 for x in claims if not x.supported and x.kind == "unknown")
    denom = max(len([x for x in claims if x.kind != "unknown"]), 1)
    return ClaimCheckResult(
        claims=claims,
        support_rate=round(supported_count / denom, 4),
        supported_count=supported_count,
        unsupported_count=unsupported_count,
        unverifiable_count=unverifiable_count,
        caveats=[
            "claim verifier 只对齐本轮已有 observation，不额外查证。",
            "偏好推断和口碑总结是弱验证；canonical fact 未命中时应降级或补工具。",
        ],
    )
