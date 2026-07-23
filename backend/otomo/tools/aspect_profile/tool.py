"""Build user aspect sentiment profiles from Bangumi collection comments.

Phase 6 upgrades recommendations from tag-only taste to aspect-level 好球区/雷区.
The LLM path extracts aspect sentiment in batch; the deterministic fallback keeps
the tool usable offline and testable.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ...agent._common import safe_json, strip_leak
from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from ...llm import get_llm
from ...memory import LongTermMemory
from ...memory.consolidate import now_iso
from ...memory.models import AspectKey, AspectPreference, UserAspectProfile, memory_summary
from ..bangumi.client import SUBJECT_TYPE, BangumiClient
from ..review.tool import (
    CommentEvidence,
    _ASPECT_LABELS,
    _extract_aspect_opinions,
)

_MAX_ITEMS = 1000
_MAX_LLM_SAMPLES = 80
_MAX_SAMPLE_CHARS = 180
_ASPECT_KEYS = set(_ASPECT_LABELS)


class BuildAspectProfileArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则用当前 token 账号")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    limit: int = Field(300, ge=20, le=1000, description="最多分析多少条看过收藏")
    max_samples: int = Field(80, ge=10, le=160, description="最多送入 ABSA 的私评样本数")
    force_refresh: bool = Field(False, description="为 true 时无视已有 memory，重新抽取")
    use_llm: bool = Field(True, description="是否尝试 LLM 批量 ABSA；失败自动降级关键词路径")


class AspectExtraction(BaseModel):
    subject: str = ""
    aspect: AspectKey = "general"
    polarity: Literal["positive", "negative", "mixed"] = "mixed"
    snippet: str = ""
    confidence: float = Field(0.55, ge=0.0, le=1.0)


class BuildAspectProfileResult(BaseModel):
    username: str
    subject_type: str
    profile: UserAspectProfile
    samples_seen: int
    extraction_source: Literal["llm", "fallback", "none"]
    caveats: list[str] = Field(default_factory=list)
    memory: dict | None = None


_NO_USER_ERR = "未提供 username 且无法获取当前账号（需要有效 BANGUMI_TOKEN）；请改用 username 指定要分析的用户。"


async def _username(client: BangumiClient, username: str | None) -> str | None:
    if username:
        return username
    try:
        me = await client.get_me()
    except Exception:  # noqa: BLE001
        return None
    return me.get("username") or str(me.get("id")) or None


def _comment_of(item: dict) -> str:
    value = item.get("comment")
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _subject_name(item: dict) -> str:
    subj = item.get("subject") or {}
    return subj.get("name_cn") or subj.get("name") or f"subject {subj.get('id')}"


def _rating_polarity(item: dict) -> Literal["positive", "negative", "mixed"]:
    rate = int(item.get("rate") or 0)
    if rate >= 8:
        return "positive"
    if rate and rate <= 5:
        return "negative"
    return "mixed"


def collection_comment_samples(items: list[dict], limit: int) -> list[dict]:
    samples: list[dict] = []
    for item in items:
        comment = _comment_of(item)
        if not comment:
            continue
        samples.append({
            "subject": _subject_name(item),
            "rate": item.get("rate") or 0,
            "polarity_hint": _rating_polarity(item),
            "text": comment[:_MAX_SAMPLE_CHARS],
        })
        if len(samples) >= limit:
            break
    return samples


def fallback_extract(samples: list[dict]) -> list[AspectExtraction]:
    comments = [
        f"{s.get('subject') or ''}：{s.get('text') or ''}"
        for s in samples
        if str(s.get("text") or "").strip()
    ]
    opinions = _extract_aspect_opinions([CommentEvidence(source="Bangumi 用户私评", samples=comments)])
    out: list[AspectExtraction] = []
    for op in opinions:
        polarity = "positive" if op.sentiment == "positive" else ("negative" if op.sentiment == "negative" else "mixed")
        out.append(
            AspectExtraction(
                subject="",
                aspect=op.aspect,
                polarity=polarity,
                snippet=op.evidence_snippet,
                confidence={"high": 0.78, "medium": 0.62, "low": 0.45}.get(op.confidence, 0.5),
            )
        )
    return out


def _parse_llm_json(text: str) -> list[dict]:
    text = strip_leak(text)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        parsed = safe_json(text)
        if parsed:
            raw = parsed.get("items") or parsed.get("data") or parsed
        else:
            i, j = text.find("["), text.rfind("]")
            if not (0 <= i < j):
                return []
            try:
                raw = json.loads(text[i : j + 1])
            except json.JSONDecodeError:
                return []
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("data") or []
    return raw if isinstance(raw, list) else []


async def llm_extract(samples: list[dict]) -> list[AspectExtraction]:
    payload = [
        {
            "subject": s["subject"],
            "rate": s["rate"],
            "polarity_hint": s["polarity_hint"],
            "text": s["text"],
        }
        for s in samples[:_MAX_LLM_SAMPLES]
    ]
    prompt = (
        "你是 ACGN 推荐系统的 ABSA 标注器。请从用户 Bangumi 私评中抽取 aspect 情感，"
        "只输出 JSON 数组，不要解释。\n"
        "aspect 只能取 story, character, pacing, visual, music, direction, text, system, voice, general。\n"
        "polarity 只能取 positive, negative, mixed。\n"
        "关键规则：一条评价可以同时有多个 aspect；例如'作画神但剧情拖'要输出 visual positive 和 story/pacing negative。"
        "不要把作品标签当情感；只抽用户在评论中表达的好球区/雷区。\n"
        "字段：subject, aspect, polarity, snippet, confidence(0-1)。\n"
        f"样本：{json.dumps(payload, ensure_ascii=False)}"
    )
    resp = await get_llm().chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": "只输出合法 JSON 数组。"},
            {"role": "user", "content": prompt},
        ],
    )
    rows = _parse_llm_json(resp.choices[0].message.content or "")
    out: list[AspectExtraction] = []
    for row in rows[:160]:
        if not isinstance(row, dict):
            continue
        if row.get("aspect") not in _ASPECT_KEYS:
            row["aspect"] = "general"
        if row.get("polarity") not in {"positive", "negative", "mixed"}:
            continue
        try:
            out.append(AspectExtraction.model_validate(row))
        except ValidationError:
            continue
    return out


def aggregate_aspect_profile(
    username: str,
    subject_type: str,
    extractions: list[AspectExtraction],
    sample_count: int,
    extraction_source: Literal["llm", "fallback", "none"],
) -> UserAspectProfile:
    grouped: dict[tuple[str, str], list[AspectExtraction]] = defaultdict(list)
    for item in extractions:
        if item.polarity == "mixed":
            continue
        polarity = "like" if item.polarity == "positive" else "dislike"
        grouped[(item.aspect, polarity)].append(item)

    max_count = max((len(v) for v in grouped.values()), default=1)

    def build(polarity: Literal["like", "dislike"]) -> list[AspectPreference]:
        prefs: list[AspectPreference] = []
        for (aspect, pol), rows in grouped.items():
            if pol != polarity:
                continue
            confidence = min(0.95, 0.35 + 0.12 * len(rows) + 0.12 * min(sample_count, 30) / 30)
            if extraction_source == "fallback":
                confidence = min(confidence, 0.72)
            prefs.append(
                AspectPreference(
                    aspect=aspect,  # type: ignore[arg-type]
                    label=_ASPECT_LABELS.get(aspect, aspect),
                    polarity=pol,
                    weight=round(len(rows) / max_count, 4),
                    evidence_count=len(rows),
                    sample=next((r.snippet for r in rows if r.snippet), "")[:160],
                    source="derived_from_feedback",
                    confidence=round(confidence, 4),
                )
            )
        prefs.sort(key=lambda x: (-x.weight, -x.evidence_count, x.label))
        return prefs[:8]

    return UserAspectProfile(
        username=username,
        subject_type=subject_type,
        likes=build("like"),
        dislikes=build("dislike"),
        sample_count=sample_count,
        extraction_source=extraction_source,
        updated_at=now_iso(),
    )


def profile_from_samples(
    username: str,
    subject_type: str,
    samples: list[dict],
    llm_extractions: list[AspectExtraction] | None = None,
) -> UserAspectProfile:
    if llm_extractions:
        return aggregate_aspect_profile(username, subject_type, llm_extractions, len(samples), "llm")
    fallback = fallback_extract(samples)
    source: Literal["fallback", "none"] = "fallback" if fallback else "none"
    return aggregate_aspect_profile(username, subject_type, fallback, len(samples), source)


class BuildAspectProfileTool(Tool):
    name = "build_aspect_profile"
    description = (
        "从用户 Bangumi 看过作品的可见私评中批量抽取 aspect 情感，生成好球区/雷区并写入长期记忆。"
        "推荐、为什么适合我、避雷分析前可用；没有私评会降级并返回低置信说明。"
    )
    args_model = BuildAspectProfileArgs
    result_model = BuildAspectProfileResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: BuildAspectProfileArgs) -> ToolResult[BuildAspectProfileResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error=_NO_USER_ERR)
        mem = self.ltm.load_user(username)
        if not args.force_refresh and args.subject_type in mem.aspect_profiles:
            profile = mem.aspect_profiles[args.subject_type]
            return ToolResult(
                ok=True,
                data=BuildAspectProfileResult(
                    username=username,
                    subject_type=args.subject_type,
                    profile=profile,
                    samples_seen=profile.sample_count,
                    extraction_source=profile.extraction_source,
                    caveats=["已命中长期记忆中的 aspect profile；force_refresh=true 可重新抽取。"],
                    memory=memory_summary(mem).model_dump(mode="json", exclude_none=True),
                ),
            )

        items = await self.client.get_all_user_collections(
            username,
            SUBJECT_TYPE[args.subject_type],
            collection_type=2,
            max_items=min(args.limit, _MAX_ITEMS),
        )
        samples = collection_comment_samples(items, args.max_samples)
        caveats: list[str] = []
        extractions: list[AspectExtraction] = []
        source: Literal["llm", "fallback", "none"] = "none"
        if samples and args.use_llm:
            try:
                extractions = await llm_extract(samples)
            except Exception as e:  # noqa: BLE001
                caveats.append(f"LLM ABSA 失败，已降级关键词抽取：{type(e).__name__}")
            if extractions:
                source = "llm"
        if not extractions:
            extractions = fallback_extract(samples)
            source = "fallback" if extractions else "none"
        profile = aggregate_aspect_profile(username, args.subject_type, extractions, len(samples), source)
        mem.aspect_profiles[args.subject_type] = profile
        self.ltm.save_user(mem)

        if not samples:
            caveats.append("没有可见用户私评，无法建立可靠 aspect 情感画像。")
        elif source == "fallback":
            caveats.append("当前使用关键词级降级抽取；置信度低于 LLM ABSA。")
        caveats.append("aspect profile 是 derived_from_feedback 弱信号，用户显式偏好和本轮要求优先。")
        return ToolResult(
            ok=True,
            data=BuildAspectProfileResult(
                username=username,
                subject_type=args.subject_type,
                profile=profile,
                samples_seen=len(samples),
                extraction_source=source,
                caveats=caveats,
                memory=memory_summary(mem).model_dump(mode="json", exclude_none=True),
            ),
            sources=[Citation(title=f"Bangumi @{username}", url=f"https://bgm.tv/user/{username}", source="bangumi")],
        )


def build_aspect_profile_tools(client: BangumiClient, ltm: LongTermMemory) -> list[Tool]:
    return [BuildAspectProfileTool(client, ltm)]
