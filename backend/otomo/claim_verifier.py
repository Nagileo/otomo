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
    role: str = "unknown"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    text: str = ""
    snippet: str = ""


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


class EvidenceDoc(BaseModel):
    source: str
    role: Literal["canonical", "discourse", "preference", "web", "unknown"] = "unknown"
    text: str
    facts: dict[str, Any] = Field(default_factory=dict)


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


def _source_role(name: str) -> EvidenceDoc.model_fields["role"].annotation:
    if name.startswith(("get_subject", "search_subject", "check_subject", "list_season", "search_visual_novels", "search_erogamescape", "rank_erogamescape", "search_musicbrainz")):
        return "canonical"
    if any(k in name for k in ("comment", "review", "bilibili", "community", "browser_fetch", "fetch_url", "web_search")):
        return "discourse"
    if any(k in name for k in ("recommend", "profile", "memory", "taste", "watch_copilot", "weekly_digest")):
        return "preference"
    return "unknown"


def _collect_facts(value: Any, prefix: str = "", out: dict[str, Any] | None = None, limit: int = 220) -> dict[str, Any]:
    out = out or {}
    if len(out) >= limit:
        return out
    if isinstance(value, dict):
        for k, v in value.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                _collect_facts(v, key, out, limit)
            else:
                out[key] = v
    elif isinstance(value, list):
        for i, item in enumerate(value[:30]):
            _collect_facts(item, f"{prefix}[{i}]", out, limit)
    return out


def observation_documents(observations: list[dict[str, Any]]) -> list[EvidenceDoc]:
    docs: list[EvidenceDoc] = []
    for obs in observations:
        name = str(obs.get("name") or "observation")
        chunks = [str(obs.get("summary") or "")]
        facts: dict[str, Any] = {}
        if obs.get("data") is not None:
            chunks.append(_flatten(obs.get("data")))
            facts.update(_collect_facts(obs.get("data")))
        for source in obs.get("sources") or []:
            if isinstance(source, dict):
                chunks.append(" ".join(str(source.get(k) or "") for k in ("source", "title", "url")))
        for ent in obs.get("entities") or []:
            if isinstance(ent, dict):
                chunks.append(" ".join(str(ent.get(k) or "") for k in ("type", "id", "name")))
                aliases = ent.get("aliases") or []
                if isinstance(aliases, list):
                    chunks.extend(str(x) for x in aliases[:8])
        docs.append(EvidenceDoc(source=name, role=_source_role(name), text="\n".join(chunks), facts=facts))
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


def _claim_required_roles(kind: ClaimKind) -> set[str]:
    if kind == "canonical_fact":
        return {"canonical"}
    if kind == "discourse_summary":
        return {"discourse", "web"}
    if kind == "preference_inference":
        return {"preference", "canonical"}
    return {"canonical", "discourse", "preference", "web", "unknown"}


def _snippet(text: str, anchors: list[str], limit: int = 180) -> str:
    norm_text = text.replace("\n", " ")
    for anchor in anchors:
        idx = _norm(norm_text).find(_norm(anchor))
        if idx >= 0:
            start = max(idx - 50, 0)
            return norm_text[start:start + limit]
    return norm_text[:limit]


def _staff_target(text: str) -> str:
    patterns = [
        r"(?:由|是|为)?\s*([A-Za-z0-9][A-Za-z0-9 ._-]{1,40})\s*(?:制作|动画制作|出品)",
        r"(?:制作公司|动画制作)\s*(?:是|为|=|：|:)?\s*([A-Za-z0-9][A-Za-z0-9 ._-]{1,40})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip(" ，。；;:：")
    return ""


def _canonical_contradiction(claim: str, docs: list[EvidenceDoc]) -> str:
    target = _staff_target(claim)
    if target:
        staff_docs = [d for d in docs if d.role == "canonical" and "制作" in d.text]
        if staff_docs and all(_norm(target) not in _norm(d.text) for d in staff_docs):
            sample = _snippet(staff_docs[0].text, ["制作", "动画制作"], 160)
            return f"本轮 canonical staff 证据未出现“{target}”；命中样本：{sample}"
    nums = re.findall(r"\d+(?:\.\d+)?", claim)
    if nums and any(k in claim for k in ("评分", "rank", "排名")):
        canonical = [d for d in docs if d.role == "canonical" and any(k in d.text for k in ("rating", "score", "评分", "rank"))]
        if canonical and not any(n in d.text for n in nums for d in canonical):
            return "本轮 canonical 评分/排名证据存在，但未出现该数值。"
    return ""


def _evidence_for(claim: str, kind: ClaimKind, docs: list[EvidenceDoc]) -> list[ClaimEvidence]:
    anchors = _anchors(claim)
    if not anchors:
        return []
    required = _claim_required_roles(kind)
    evidence: list[ClaimEvidence] = []
    for doc in docs:
        hay = _norm(doc.text)
        if not hay:
            continue
        hits = [a for a in anchors if _norm(a) and _norm(a) in hay]
        if not hits:
            continue
        role_bonus = 0.22 if doc.role in required else -0.08
        confidence = max(0.05, min(1.0, 0.20 + role_bonus + 0.16 * len(hits)))
        evidence.append(
            ClaimEvidence(
                source=doc.source,
                role=doc.role,
                confidence=confidence,
                text="命中：" + "、".join(hits[:5]),
                snippet=_snippet(doc.text, hits),
            )
        )
    evidence.sort(key=lambda x: x.confidence, reverse=True)
    return evidence[:4]


def verify_answer_claims(answer: str, observations: list[dict[str, Any]]) -> ClaimCheckResult:
    docs = observation_documents(observations)
    claims: list[VerifiedClaim] = []
    for text in split_claims(answer):
        kind = classify_claim(text)
        evidence = _evidence_for(text, kind, docs)
        contradiction = _canonical_contradiction(text, docs) if kind == "canonical_fact" else ""
        if kind == "preference_inference" and evidence:
            supported = True
            note = "偏好推断已对齐到本轮画像/推荐证据。"
        elif kind == "preference_inference":
            supported = False
            note = "偏好推断没有命中本轮画像或推荐证据。"
        elif contradiction:
            supported = False
            note = contradiction
            evidence = []
        elif kind == "unknown":
            supported = bool(evidence)
            note = "一般陈述；证据命中较弱。" if evidence else "一般陈述，未找到本轮证据。"
        else:
            required = _claim_required_roles(kind)
            supported = any(ev.role in required and ev.confidence >= 0.45 for ev in evidence)
            if supported:
                note = "已命中本轮同类型 evidence graph。"
            elif evidence:
                note = "有弱命中，但来源类型或锚点不足，不足以支持该 claim。"
            else:
                note = "未在本轮 observation 中找到直接证据。"
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
            "claim verifier v2 只对齐本轮已有 observation evidence graph，不额外查证。",
            "canonical fact 需要 canonical 工具证据；口碑/偏好是弱验证；未命中时应降级或补工具。",
        ],
    )
