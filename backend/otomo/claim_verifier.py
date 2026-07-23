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
ClaimSeverity = Literal["info", "warn", "block"]
EvidenceRole = Literal["canonical", "discourse", "preference", "web", "unknown"]


class ClaimEvidence(BaseModel):
    source: str
    role: str = "unknown"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    text: str = ""
    snippet: str = ""
    evidence_turn: int | None = None


class VerifiedClaim(BaseModel):
    text: str
    kind: ClaimKind
    supported: bool
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    severity: ClaimSeverity = "info"
    evidence: list[ClaimEvidence] = Field(default_factory=list)
    note: str = ""
    suggestion: str = ""


class ClaimCheckResult(BaseModel):
    claims: list[VerifiedClaim] = Field(default_factory=list)
    support_rate: float = Field(0.0, ge=0.0, le=1.0)
    supported_count: int = 0
    unsupported_count: int = 0
    unverifiable_count: int = 0
    needs_revision: bool = False
    revision_hints: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class EvidenceDoc(BaseModel):
    source: str
    role: EvidenceRole = "unknown"
    text: str
    facts: dict[str, Any] = Field(default_factory=dict)
    entities: list[str] = Field(default_factory=list)
    turn: int | None = None


_SOURCE_TERMS = ("Bangumi", "批判空间", "ErogameScape", "VNDB", "AniList", "MusicBrainz", "B站", "yuc", "萌娘", "维基")
_FACT_TERMS = (
    "制作", "公司", "监督", "导演", "脚本", "原作", "声优", "CV", "配音", "播出",
    "发售", "年份", "评分", "排名", "rank", "staff", "角色",
)
_HARD_CANONICAL_TERMS = (
    "制作公司", "动画制作", "制作方", "监督", "导演", "脚本", "系列构成", "原作",
    "声优", "CV", "配音", "播出", "发售", "年份", "评分", "排名", "rank", "staff",
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


def _source_role(name: str) -> EvidenceRole:
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


def _entity_names(value: Any, limit: int = 40) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        for key in ("title", "name", "name_cn", "subject_name", "original_name"):
            item = value.get(key)
            if isinstance(item, str) and 1 < len(item.strip()) <= 100 and item.strip() not in names:
                names.append(item.strip())
        for child in value.values():
            if len(names) >= limit:
                break
            if isinstance(child, (dict, list)):
                for item in _entity_names(child, limit - len(names)):
                    if item not in names:
                        names.append(item)
    elif isinstance(value, list):
        for child in value[:30]:
            for item in _entity_names(child, limit - len(names)):
                if item not in names:
                    names.append(item)
    return names[:limit]


def observation_documents(observations: list[dict[str, Any]]) -> list[EvidenceDoc]:
    docs: list[EvidenceDoc] = []
    for obs in observations:
        name = str(obs.get("name") or "observation")
        summary = str(obs.get("summary") or "")
        chunks = [summary]
        facts: dict[str, Any] = {}
        data = obs.get("data")
        scalar_data: Any = data
        item_docs: list[tuple[str, dict[str, Any]]] = []
        if isinstance(data, dict):
            scalar_data = {}
            for key, value in data.items():
                if isinstance(value, list) and value and all(isinstance(x, dict) for x in value[:20]):
                    item_docs.extend((f"{key}[{idx}]", item) for idx, item in enumerate(value[:30]))
                else:
                    scalar_data[key] = value
        if scalar_data is not None:
            chunks.append(_flatten(scalar_data))
            facts.update(_collect_facts(scalar_data))
        for source in obs.get("sources") or []:
            if isinstance(source, dict):
                chunks.append(" ".join(str(source.get(k) or "") for k in ("source", "title", "url")))
        for ent in obs.get("entities") or []:
            if isinstance(ent, dict):
                chunks.append(" ".join(str(ent.get(k) or "") for k in ("type", "id", "name")))
                aliases = ent.get("aliases") or []
                if isinstance(aliases, list):
                    chunks.extend(str(x) for x in aliases[:8])
        turn = obs.get("turn")
        try:
            turn_value = int(turn) if turn is not None else None
        except (TypeError, ValueError):
            turn_value = None
        parent_entities = _entity_names(scalar_data)
        docs.append(
            EvidenceDoc(
                source=name,
                role=_source_role(name),
                text="\n".join(chunks),
                facts=facts,
                entities=parent_entities,
                turn=turn_value,
            )
        )
        for label, item in item_docs:
            docs.append(
                EvidenceDoc(
                    source=f"{name}:{label}",
                    role=_source_role(name),
                    text=" ".join(parent_entities[:4]) + "\n" + _flatten(item),
                    facts=_collect_facts(item),
                    entities=list(dict.fromkeys([*parent_entities, *_entity_names(item)])),
                    turn=turn_value,
                )
            )
    return docs


def split_claims(answer: str, limit: int = 18) -> list[str]:
    lines = []
    for raw in re.split(r"[\n。！？!?；;，,]+", answer):
        # 去句首列表序号（1. / 2) / - / •），但保留句尾数字——评分/排名/年份是最该校验的事实
        cleaned = re.sub(r"^\s*(?:[-*•·]+|\d{1,2}[.、)）])\s+", "", raw)
        text = cleaned.strip(" \t\r-*•、：:.")
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
    # Claim verifier 只做 evidence-local factuality，不承担剧透策略。
    # 剧情/反转/神回/氛围一类解读默认不是可机器强校验事实，不能触发答案回退。
    if any(t in text for t in _SPOILER_TERMS) and not any(t in text for t in _HARD_CANONICAL_TERMS):
        return "spoiler_sensitive"
    if any(t in text for t in _DISCOURSE_TERMS):
        return "discourse_summary"
    if any(t in text for t in _INFERENCE_TERMS):
        return "preference_inference"
    if any(t in text for t in _FACT_TERMS) or (re.search(r"\d", text) and any(t in text for t in _HARD_CANONICAL_TERMS)):
        return "canonical_fact"
    return "unknown"


def _is_hard_canonical_claim(text: str) -> bool:
    return any(term in text for term in _HARD_CANONICAL_TERMS)


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
    # 同时覆盖 ASCII 公司名（A-1 Pictures / 8-bit）与中文公司名（京都动画 / 扳机社）
    patterns = [
        # 「(由/是/为) <公司> 制作/出品」——公司在“制作”前，非贪婪定界
        r"(?:由|是|为)\s*((?:《[^》]{2,30}》)|[一-鿿A-Za-z0-9][一-鿿A-Za-z0-9 ._·-]{1,40}?)\s*(?:制作|出品|负责动画|负责制作)",
        # 「制作公司/动画制作 (是/为/由) <公司>」——公司在末尾，贪婪到标点
        r"(?:制作公司|动画制作|制作方|制作)\s*(?:是|为|=|：|:|由)\s*((?:《[^》]{2,30}》)|[一-鿿A-Za-z0-9][一-鿿A-Za-z0-9 ._·-]{1,40})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m and m.group(1):
            return m.group(1).strip(" ，。；;:：《》")
    return ""


def _subject_anchors(text: str) -> list[str]:
    return [x.strip() for x in re.findall(r"《([^》]{2,80})》", text) if x.strip()]


def _entity_key(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", value.casefold())


def _docs_for_claim(claim: str, docs: list[EvidenceDoc]) -> list[EvidenceDoc]:
    subjects = _subject_anchors(claim)
    if not subjects:
        return docs
    matched = [
        doc for doc in docs
        if any(
            _entity_key(subject) == _entity_key(entity)
            for subject in subjects
            for entity in doc.entities
            if len(_entity_key(entity)) >= 2
        )
    ]
    # No entity-bound evidence means "unverified", never a contradiction.
    return matched


def _canonical_contradiction(claim: str, docs: list[EvidenceDoc]) -> str:
    # 矛盾（→ block → auto revision）只看当轮证据（turn is None）：
    # 历史池的旧评分/旧数值只作支持性弱证据，不能反过来"纠正"本轮刚查到的新数据。
    docs = _docs_for_claim(claim, [d for d in docs if d.turn is None])
    if _subject_anchors(claim) and not docs:
        return ""
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


def _canonical_value_match(claim: str, docs: list[EvidenceDoc]) -> bool | None:
    """Check entity-bound predicate values instead of title co-occurrence."""
    canonical = [doc for doc in _docs_for_claim(claim, docs) if doc.role == "canonical"]
    if not canonical:
        return False
    target = _staff_target(claim)
    if target:
        target_key = _norm(target)
        return any(
            target_key in _norm(doc.text)
            and any(term in doc.text for term in ("制作", "动画制作", "出品", "负责动画"))
            for doc in canonical
        )
    numeric_terms = re.findall(r"\d+(?:\.\d+)?", claim)
    predicate_terms = [
        term for term in ("评分", "rank", "排名", "播出", "发售", "年份") if term in claim
    ]
    if numeric_terms and predicate_terms:
        return any(
            all(number in doc.text for number in numeric_terms)
            and any(term in doc.text or term == "评分" and "score" in doc.text for term in predicate_terms)
            for doc in canonical
        )
    return None


def _evidence_for(claim: str, kind: ClaimKind, docs: list[EvidenceDoc]) -> list[ClaimEvidence]:
    docs = _docs_for_claim(claim, docs)
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
                evidence_turn=doc.turn,
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
        hard_canonical = kind == "canonical_fact" and _is_hard_canonical_claim(text)
        value_match = _canonical_value_match(text, docs) if hard_canonical else None
        if kind in {"discourse_summary", "spoiler_sensitive"}:
            supported = any(ev.role in {"discourse", "web", "canonical"} and ev.confidence >= 0.45 for ev in evidence)
            note = (
                "叙事/口碑类表述已命中本轮证据，但只作弱支持。"
                if supported else
                "叙事/口碑/剧透类表述不做自动事实回退；如需强校验，应接分集评论/剧情来源。"
            )
        elif kind == "preference_inference" and evidence:
            supported = True
            note = "偏好推断已对齐到本轮画像/推荐证据。"
        elif kind == "preference_inference":
            supported = False
            note = "偏好推断没有命中本轮画像或推荐证据；仅记录为弱信号，不自动回退。"
        elif contradiction:
            supported = False
            note = contradiction
            evidence = []
        elif hard_canonical and value_match is not None:
            supported = value_match
            note = (
                "已在同一作品的 canonical 证据中匹配到谓词和值。"
                if supported else
                "同一作品的 canonical 证据没有同时匹配该谓词和值。"
            )
        elif kind == "unknown":
            supported = bool(evidence)
            note = "一般陈述；证据命中较弱。" if evidence else "一般陈述，不进入强事实校验。"
        else:
            required = _claim_required_roles(kind)
            supported = any(
                ev.role in required
                and ev.confidence >= 0.45
                and (not hard_canonical or any(term in ev.snippet for term in _HARD_CANONICAL_TERMS))
                for ev in evidence
            )
            if supported:
                note = "已命中本轮同类型 evidence graph。"
            elif evidence:
                note = "有弱命中，但来源类型或锚点不足，不足以支持该 claim。"
            elif hard_canonical:
                note = "本轮没有直接 canonical 证据；记录为未确认，不自动删除。"
            else:
                note = "不是强 canonical 断言，或本轮无相关证据；不自动回退。"
        confidence = max([x.confidence for x in evidence], default=0.0)
        if supported:
            severity: ClaimSeverity = "info"
            suggestion = ""
        elif contradiction:
            severity = "block"
            suggestion = "本轮 canonical 证据与该断言冲突；删除或改写为证据支持的版本。"
        elif hard_canonical and kind == "canonical_fact":
            severity = "warn"
            suggestion = "如需严谨回答，应补 canonical 工具；当前只能视为未确认。"
        else:
            severity = "info"
            suggestion = "不作为强事实校验对象；可保留为解释/口碑/剧情理解。"
        claims.append(
            VerifiedClaim(
                text=text,
                kind=kind,
                supported=supported,
                confidence=confidence,
                severity=severity,
                evidence=evidence,
                note=note,
                suggestion=suggestion,
            )
        )
    verifiable = [x for x in claims if x.kind == "canonical_fact" and (x.supported or x.severity in {"warn", "block"})]
    supported_count = sum(1 for x in verifiable if x.supported)
    unsupported_count = sum(1 for x in verifiable if not x.supported and x.severity in {"warn", "block"})
    unverifiable_count = len([x for x in claims if x not in verifiable])
    denom = max(len(verifiable), 1)
    revision_hints = [
        f"{x.severity}: {x.text} -> {x.suggestion or x.note}"
        for x in claims
        if not x.supported and x.severity == "block"
    ][:8]
    return ClaimCheckResult(
        claims=claims,
        support_rate=round(supported_count / denom, 4),
        supported_count=supported_count,
        unsupported_count=unsupported_count,
        unverifiable_count=unverifiable_count,
        needs_revision=any(x.severity == "block" and x.kind == "canonical_fact" for x in claims),
        revision_hints=revision_hints,
        caveats=[
            "claim verifier v4 只对强 canonical 硬事实做自动修正；剧情/口碑/偏好不触发答案回退。",
            "只有本轮 canonical 证据与断言冲突时才 needs_revision；未命中证据只是未确认。",
            "多轮会话会合并最近证据池；历史证据命中会在 evidence_turn 标出查询轮次。",
        ],
    )
