"""Multimodal ACGN screenshot entrypoint.

The tool uses an external VLM API only when configured, then anchors candidates
back to Bangumi subjects. Identification is treated as a weak entry signal;
canonical facts still come from Bangumi tools.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from typing import Any
from typing import Literal

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from ...uploads import upload_store
from .._cache import TTLCache
from ..bangumi.client import SUBJECT_TYPE, BangumiClient


_TRACE_MOE_API = "https://api.trace.moe/search"
_trace_cache = TTLCache(settings.cache_ttl * 6)


class IdentifyScreenshotArgs(BaseModel):
    image_url: str = Field("", description="单张截图 URL / data URL / upload://...；兼容旧调用")
    image_urls: list[str] = Field(default_factory=list, description="多张截图 URL / data URL / upload://...；最多处理 4 张")
    question: str = Field("识别这张 ACGN 截图可能来自哪部作品/哪个角色/哪一集。", description="可选关注点")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    limit: int = Field(5, ge=1, le=10)
    use_trace_moe: bool = Field(True, description="动画截图优先调用 trace.moe 反查集数/时间戳；非动画图会自动低置信降级")


class VisualCandidate(BaseModel):
    title: str
    reason: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    source: Literal["image", "image_match", "trace.moe", "bangumi"] = "image"
    image_index: int = 0
    bangumi_id: int | None = None
    bangumi_name: str = ""
    bangumi_score: float | None = None
    image: str | None = None
    match_note: str = ""
    anilist_id: int | None = None
    episode: str | int | None = None
    from_seconds: float | None = None
    to_seconds: float | None = None
    timestamp: str = ""


class CharacterCandidate(BaseModel):
    name: str
    reason: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    image_index: int = 0
    bangumi_id: int | None = None
    bangumi_name: str = ""
    match_note: str = ""


class IdentifyScreenshotResult(BaseModel):
    question: str
    raw_vlm_answer: str = ""
    candidates: list[VisualCandidate] = Field(default_factory=list)
    character_candidates: list[CharacterCandidate] = Field(default_factory=list)
    visual_tags: list[str] = Field(default_factory=list)
    ocr_text: str = ""
    caveats: list[str] = Field(default_factory=list)


_PROMPT = """你是 ACGN 截图识别助手。请根据图片识别可能的作品、角色或集数线索。
只输出 JSON：
{"candidates":[{"title":"作品名","reason":"视觉线索","confidence":0.0到1.0}],
"characters":[{"name":"角色名","reason":"视觉线索","confidence":0.0到1.0}],
"visual_tags":["画风/题材/色调标签"],"ocr_text":"能读出的字幕/台词/榜单文字","notes":["..."]}
不知道就返回空 candidates，不要编造确定结论。"""


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(text[i : j + 1])
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_titles(text: str) -> list[str]:
    titles = re.findall(r"《([^》]{2,50})》", text)
    if not titles:
        titles = re.findall(r"[\u4e00-\u9fffA-Za-z0-9 !?:：._-]{3,40}", text)
    out = []
    for title in titles:
        title = title.strip(" -:：，。[]()（）")
        if title and title not in out and not any(k in title for k in ("可能", "截图", "角色", "作品")):
            out.append(title)
    return out[:6]


def _format_seconds(value: float | None) -> str:
    if value is None:
        return ""
    total = max(0, int(round(value)))
    return f"{total // 60:02d}:{total % 60:02d}"


def _image_inputs(args: IdentifyScreenshotArgs) -> list[str]:
    items = [x.strip() for x in args.image_urls if str(x).strip()]
    if args.image_url.strip():
        items.insert(0, args.image_url.strip())
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out[:4]


def _resolve_image_bytes(value: str) -> tuple[bytes, str] | None:
    if value.startswith("upload://"):
        return upload_store.read_bytes(value.removeprefix("upload://"))
    if value.startswith("data:image/"):
        header, _, payload = value.partition(",")
        mime = header.removeprefix("data:").split(";")[0] or "image/png"
        return base64.b64decode(payload), mime
    return None


def _cache_key(prefix: str, value: str) -> str:
    if value.startswith("upload://") or value.startswith("data:image/"):
        try:
            payload = _resolve_image_bytes(value)
            if payload:
                return f"{prefix}:bytes:{hashlib.sha256(payload[0]).hexdigest()}"
        except Exception:  # noqa: BLE001
            pass
    return f"{prefix}:url:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


async def _trace_moe_search(image_url: str) -> list[dict[str, Any]]:
    key = _cache_key("trace_moe", image_url)
    if (hit := _trace_cache.get(key)) is not None:
        return hit
    params = {"anilistInfo": "1", "cutBorders": "1"}
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        payload = _resolve_image_bytes(image_url)
        if payload:
            data, mime = payload
            resp = await client.post(_TRACE_MOE_API, params=params, files={"image": ("image", data, mime)})
        else:
            resp = await client.get(_TRACE_MOE_API, params={**params, "url": upload_store.resolve_image_url(image_url)})
        resp.raise_for_status()
        result = (resp.json() or {}).get("result") or []
    _trace_cache.set(key, result)
    return result


def _anilist_titles(raw: dict[str, Any]) -> list[str]:
    anilist = raw.get("anilist")
    if isinstance(anilist, dict):
        title = anilist.get("title") or {}
        values = [
            title.get("native"),
            title.get("romaji"),
            title.get("english"),
            *(anilist.get("synonyms") or [])[:3],
        ]
    else:
        values = [raw.get("filename")]
    out: list[str] = []
    for value in values:
        name = str(value or "").strip()
        if name and name not in out:
            out.append(name)
    return out


async def _call_vlm(image_url: str, question: str) -> str:
    if not settings.vlm_model:
        raise RuntimeError("未配置 VLM_MODEL；截图识别需要现成 VLM API")
    resolved_url = upload_store.resolve_image_url(image_url)
    text_prompt = question
    if settings.vlm_ocr_hint:
        text_prompt = f"{question}\n\nOCR/视觉提示：{settings.vlm_ocr_hint}"
    client = AsyncOpenAI(
        base_url=settings.vlm_base_url or settings.llm_base_url,
        api_key=settings.vlm_api_key or settings.llm_api_key or "EMPTY",
    )
    resp = await client.chat.completions.create(
        model=settings.vlm_model,
        messages=[
            {"role": "system", "content": _PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text_prompt},
                    {"type": "image_url", "image_url": {"url": resolved_url}},
                ],
            },
        ],
    )
    return resp.choices[0].message.content or ""


async def _safe_call_vlm(image_url: str, question: str) -> str:
    try:
        return await _call_vlm(image_url, question)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"candidates": [], "characters": [], "notes": [f"VLM unavailable: {type(e).__name__}: {e}"]}, ensure_ascii=False)


class IdentifyScreenshotTool(Tool):
    name = "identify_acgn_screenshot"
    description = (
        "用已配置的现成 VLM API 识别 ACGN 截图可能来自哪部作品/角色/集数线索，并回锚 Bangumi 候选。"
        "识别结果只是弱入口，不是 canonical 事实。"
    )
    args_model = IdentifyScreenshotArgs
    result_model = IdentifyScreenshotResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def _anchor_subject(self, cand: VisualCandidate, subject_type: str) -> None:
        stype = SUBJECT_TYPE.get(subject_type, 2)
        titles = [cand.title]
        for title in titles:
            try:
                res = await self.client.search_subjects(title, stype, limit=3)
            except Exception:  # noqa: BLE001
                continue
            rows = res.get("data") or []
            if not rows:
                continue
            row = rows[0]
            cand.bangumi_id = row.get("id")
            cand.bangumi_name = row.get("name_cn") or row.get("name") or ""
            cand.bangumi_score = row.get("score") or ((row.get("rating") or {}).get("score"))
            img = row.get("images") or {}
            cand.image = img.get("common") or img.get("medium") or img.get("grid") or cand.image
            if cand.source == "trace.moe":
                cand.confidence = max(cand.confidence, min(0.98, cand.confidence + 0.06))
                cand.match_note = "trace.moe 命中后已回锚 Bangumi；集数/时间戳仍以 trace.moe 相似度为准。"
            else:
                cand.confidence = max(cand.confidence, min(0.82, cand.confidence + 0.12))
                cand.match_note = "VLM 候选已用 Bangumi search 回锚，仍需用户确认截图是否匹配。"
            return
        cand.match_note = "Bangumi 未对齐"

    async def _anchor_character(self, cand: CharacterCandidate) -> None:
        try:
            res = await self.client.search_characters(cand.name, limit=3)
        except Exception:  # noqa: BLE001
            return
        rows = res.get("data") or []
        if not rows:
            cand.match_note = "Bangumi 角色未对齐"
            return
        row = rows[0]
        cand.bangumi_id = row.get("id")
        cand.bangumi_name = row.get("name") or cand.name
        cand.confidence = max(cand.confidence, min(0.8, cand.confidence + 0.1))
        cand.match_note = "已用 Bangumi character search 回锚，可继续接 explore_voice_network。"

    async def _trace_candidates(self, image_url: str, image_index: int, limit: int) -> list[VisualCandidate]:
        try:
            rows = await _trace_moe_search(image_url)
        except Exception:  # noqa: BLE001
            return []
        out: list[VisualCandidate] = []
        for row in rows[:limit]:
            similarity = float(row.get("similarity") or 0.0)
            if similarity < 0.72:
                continue
            anilist = row.get("anilist")
            anilist_id = anilist.get("id") if isinstance(anilist, dict) else anilist
            titles = _anilist_titles(row)
            if not titles:
                continue
            start = row.get("from")
            end = row.get("to")
            timestamp = _format_seconds(float(start)) if isinstance(start, (int, float)) else ""
            episode = row.get("episode")
            out.append(
                VisualCandidate(
                    title=titles[0],
                    reason=(
                        "trace.moe 动画截图反查"
                        + (f" · 第 {episode} 集" if episode is not None else "")
                        + (f" · {timestamp}" if timestamp else "")
                    ),
                    confidence=min(max(similarity, 0.0), 0.99),
                    source="trace.moe",
                    image_index=image_index,
                    image=row.get("image"),
                    anilist_id=int(anilist_id) if isinstance(anilist_id, int) else None,
                    episode=episode,
                    from_seconds=float(start) if isinstance(start, (int, float)) else None,
                    to_seconds=float(end) if isinstance(end, (int, float)) else None,
                    timestamp=timestamp,
                )
            )
        return out

    def _vlm_parse(self, raw: str, image_index: int, limit: int) -> tuple[list[VisualCandidate], list[CharacterCandidate], list[str], str]:
        payload = _extract_json(raw)
        candidates: list[VisualCandidate] = []
        characters: list[CharacterCandidate] = []
        tags: list[str] = []
        ocr_text = ""
        raw_candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if isinstance(raw_candidates, list):
            for item in raw_candidates[:limit]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                if title:
                    candidates.append(
                        VisualCandidate(
                            title=title,
                            reason=str(item.get("reason") or "")[:180],
                            confidence=max(0.0, min(float(item.get("confidence") or 0.0), 1.0)),
                            source="image",
                            image_index=image_index,
                        )
                    )
        if not candidates:
            candidates = [
                VisualCandidate(title=t, confidence=0.35, reason="从 VLM 自然语言回答中抽取", image_index=image_index)
                for t in _extract_titles(raw)
            ]
        raw_characters = payload.get("characters") if isinstance(payload, dict) else None
        if isinstance(raw_characters, list):
            for item in raw_characters[:limit]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if name:
                    characters.append(
                        CharacterCandidate(
                            name=name,
                            reason=str(item.get("reason") or "")[:180],
                            confidence=max(0.0, min(float(item.get("confidence") or 0.0), 1.0)),
                            image_index=image_index,
                        )
                    )
        if isinstance(payload, dict):
            tags = [str(x).strip() for x in (payload.get("visual_tags") or []) if str(x).strip()][:10]
            ocr_text = str(payload.get("ocr_text") or "").strip()[:2000]
        return candidates, characters, tags, ocr_text

    async def run(self, args: IdentifyScreenshotArgs) -> ToolResult[IdentifyScreenshotResult]:
        images = _image_inputs(args)
        if not images:
            return ToolResult(ok=False, error="需要 image_url 或 image_urls")

        candidates: list[VisualCandidate] = []
        character_candidates: list[CharacterCandidate] = []
        visual_tags: list[str] = []
        ocr_chunks: list[str] = []
        raw_parts: list[str] = []
        caveats: list[str] = [
            "视觉识别是弱入口；作品事实、staff、评分仍必须回到 Bangumi canonical 工具。",
            "trace.moe 仅适合动画截图；galgame CG、漫画页、同人图会自动依赖 VLM/其它溯源工具。",
        ]

        if args.use_trace_moe and args.subject_type == "anime":
            trace_batches = await asyncio.gather(
                *[self._trace_candidates(url, idx, args.limit) for idx, url in enumerate(images)],
                return_exceptions=True,
            )
            for batch in trace_batches:
                if isinstance(batch, list):
                    candidates.extend(batch)

        if settings.vlm_model:
            vlm_raws = await asyncio.gather(
                *[_safe_call_vlm(url, args.question) for url in images],
                return_exceptions=True,
            )
            for idx, raw_item in enumerate(vlm_raws):
                raw = "" if isinstance(raw_item, Exception) else str(raw_item)
                raw_parts.append(f"[image {idx + 1}]\n{raw}")
                vlm_candidates, chars, tags, ocr_text = self._vlm_parse(raw, idx, args.limit)
                candidates.extend(vlm_candidates)
                character_candidates.extend(chars)
                visual_tags.extend([x for x in tags if x not in visual_tags])
                if ocr_text:
                    ocr_chunks.append(ocr_text)
        else:
            caveats.append("未配置 VLM_MODEL，本轮只使用 trace.moe / Bangumi 可用链路；无法读取角色、OCR 或画风语义。")

        if not candidates and not character_candidates:
            return ToolResult(ok=False, error="没有得到可回锚的视觉候选；可配置 VLM_MODEL 或换更清晰截图重试。")

        # 先锚定高置信 trace，再锚定 VLM 候选；重复 Bangumi 条目合并到高置信来源。
        await asyncio.gather(*[self._anchor_subject(c, args.subject_type) for c in candidates])
        merged: dict[str, VisualCandidate] = {}
        for cand in sorted(candidates, key=lambda x: x.confidence, reverse=True):
            key = str(cand.bangumi_id or "").strip() or cand.title.lower()
            if key in merged:
                old = merged[key]
                old.confidence = max(old.confidence, cand.confidence)
                if cand.reason and cand.reason not in old.reason:
                    old.reason = (old.reason + "；" + cand.reason).strip("；")
                if old.source != "trace.moe" and cand.source == "trace.moe":
                    merged[key] = cand
                continue
            merged[key] = cand
        candidates = list(merged.values())[: args.limit]
        await asyncio.gather(*[self._anchor_character(c) for c in character_candidates])
        dedup_chars: dict[str, CharacterCandidate] = {}
        for cand in sorted(character_candidates, key=lambda x: x.confidence, reverse=True):
            key = str(cand.bangumi_id or "").strip() or cand.name.lower()
            dedup_chars.setdefault(key, cand)
        character_candidates = list(dedup_chars.values())[: args.limit]

        if not any(c.source == "trace.moe" for c in candidates) and args.subject_type == "anime":
            caveats.append("trace.moe 未返回高相似结果；这可能是非动画图、裁切过重、字幕遮挡或库内未覆盖。")

        return ToolResult(
            ok=True,
            data=IdentifyScreenshotResult(
                question=args.question,
                raw_vlm_answer="\n\n".join(raw_parts)[:1600],
                candidates=candidates[: args.limit],
                character_candidates=character_candidates,
                visual_tags=visual_tags[:12],
                ocr_text="\n".join(ocr_chunks)[:2000],
                caveats=caveats,
            ),
            sources=[
                Citation(
                    title=c.bangumi_name or c.title,
                    url=f"https://bgm.tv/subject/{c.bangumi_id}" if c.bangumi_id else images[min(c.image_index, len(images) - 1)],
                    source="bangumi" if c.bangumi_id else c.source,
                    image=c.image,
                )
                for c in candidates[:5]
            ]
            + [
                Citation(
                    title=c.bangumi_name or c.name,
                    url=f"https://bgm.tv/character/{c.bangumi_id}" if c.bangumi_id else images[min(c.image_index, len(images) - 1)],
                    source="bangumi" if c.bangumi_id else "image",
                )
                for c in character_candidates[:3]
            ],
        )


def build_multimodal_tools(client: BangumiClient) -> list[Tool]:
    return [IdentifyScreenshotTool(client)]
