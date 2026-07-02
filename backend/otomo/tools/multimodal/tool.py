"""Multimodal ACGN image source routing.

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
import shutil
import tempfile
from typing import Any
from typing import Literal
from pathlib import Path
from urllib.parse import quote_plus

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from ...uploads import upload_store
from .._cache import TTLCache
from ..bangumi.client import SUBJECT_TYPE, BangumiClient


_TRACE_MOE_API = "https://api.trace.moe/search"
_SAUCENAO_API = "https://saucenao.com/search.php"
_GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
_OPEN_LIBRARY_SEARCH_API = "https://openlibrary.org/search.json"
_MANGADEX_MANGA_API = "https://api.mangadex.org/manga"
_SERPAPI_SEARCH_API = "https://serpapi.com/search.json"
_trace_cache = TTLCache(settings.cache_ttl * 6)
_saucenao_cache = TTLCache(settings.cache_ttl * 6)
_external_image_cache = TTLCache(settings.cache_ttl * 6)


class ImageInputArgs(BaseModel):
    image_url: str = Field("", description="单张图片 URL / data URL / upload://...")
    image_urls: list[str] = Field(default_factory=list, description="多张图片 URL / data URL / upload://...；最多处理 4 张")
    question: str = Field("识别这张 ACGN 图片可能来自哪部作品/哪个角色/哪一集。", description="可选关注点")
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


_PROMPT = """你是 ACGN 截图识别助手。请根据图片识别可能的作品、角色或集数线索。
只输出 JSON：
{"candidates":[{"title":"作品名","reason":"视觉线索","confidence":0.0到1.0}],
"characters":[{"name":"角色名","reason":"视觉线索","confidence":0.0到1.0}],
"visual_tags":["画风/题材/色调标签"],"ocr_text":"能读出的字幕/台词/榜单文字","notes":["..."]}
不知道就返回空 candidates，不要编造确定结论。"""

_OCR_PROMPT = """你是 ACGN 场景 OCR / 情报图结构化助手。请读取图片里的文字，并按用户指定任务结构化。
只输出 JSON：
{"markdown_text":"尽量保留层级/表格/换行的 Markdown 文本",
"structured_items":[{"type":"work|character|date|score|staff|quote|platform|other","name":"实体名或项目名","value":"数值/时间/台词/说明","note":"上下文"}],
"entities":["可回锚到 Bangumi 的作品/角色/音乐/游戏名"],
"visual_tags":["截图类型/题材/画面标签"],
"confidence":0.0到1.0,
"notes":["不确定点/遮挡/低清晰度说明"]}
不要臆造看不清的文字；看不清就写不确定。"""


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


def _image_inputs(args: ImageInputArgs) -> list[str]:
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


def _data_url_from_bytes(payload: bytes, mime_type: str = "image/jpeg") -> str:
    return f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"


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
    return await _call_vlm_with_prompt(image_url, _PROMPT, question)


async def _call_vlm_with_prompt(image_url: str, system_prompt: str, question: str) -> str:
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
            {"role": "system", "content": system_prompt},
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


class VisualTextItem(BaseModel):
    type: str = "other"
    name: str = ""
    value: str = ""
    note: str = ""


class AnchoredVisualEntity(BaseModel):
    name: str
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    bangumi_id: int | None = None
    bangumi_name: str = ""
    bangumi_score: float | None = None
    image: str | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class ExtractVisualTextArgs(BaseModel):
    image_url: str = Field("", description="单张图片 URL / data URL / upload://...；兼容旧调用")
    image_urls: list[str] = Field(default_factory=list, description="多张图片 URL / data URL / upload://...；最多处理 4 张")
    mode: Literal["auto", "subtitle", "ranking", "magazine", "ppt", "table"] = Field(
        "auto", description="subtitle=台词/字幕，ranking=榜单，magazine=杂志情报页，ppt=B站导视PPT帧，table=表格"
    )
    question: str = Field("读取图片文字并结构化可检索信息。", description="额外关注点")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    anchor_entities: bool = Field(True, description="把抽出的作品名实体回锚 Bangumi")
    limit: int = Field(8, ge=1, le=20)


class ExtractVisualTextResult(BaseModel):
    mode: str
    image_count: int
    markdown_text: str = ""
    structured_items: list[VisualTextItem] = Field(default_factory=list)
    entities: list[AnchoredVisualEntity] = Field(default_factory=list)
    visual_tags: list[str] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    raw_vlm_answer: str = ""
    caveats: list[str] = Field(default_factory=list)


class ExtractVisualTextTool(Tool):
    name = "extract_visual_text"
    description = (
        "读取 ACGN 图片里的台词/字幕/榜单/杂志/PPT/表格文字，输出 Markdown 与结构化条目；"
        "可把作品实体回锚 Bangumi。用于截图 OCR、B站无字幕PPT帧、情报图整理。"
    )
    args_model = ExtractVisualTextArgs
    result_model = ExtractVisualTextResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def _anchor_entity(self, name: str, subject_type: str, confidence: float) -> AnchoredVisualEntity:
        ent = AnchoredVisualEntity(name=name, subject_type=subject_type, confidence=confidence)
        stype = SUBJECT_TYPE.get(subject_type, 2)
        try:
            res = await self.client.search_subjects(name, stype, limit=3)
        except Exception:  # noqa: BLE001
            return ent
        rows = res.get("data") or []
        if not rows:
            return ent
        row = rows[0]
        ent.bangumi_id = row.get("id")
        ent.bangumi_name = row.get("name_cn") or row.get("name") or ""
        ent.bangumi_score = row.get("score") or ((row.get("rating") or {}).get("score"))
        img = row.get("images") or {}
        ent.image = img.get("common") or img.get("medium") or img.get("grid")
        ent.confidence = min(0.9, max(confidence, 0.62))
        return ent

    def _parse_payload(self, raw: str) -> tuple[str, list[VisualTextItem], list[str], list[str], float, list[str]]:
        payload = _extract_json(raw)
        if not payload:
            return raw[:4000], [], _extract_titles(raw), [], 0.25, ["VLM 未返回 JSON，已降级为原文 OCR 摘要。"]
        markdown = str(payload.get("markdown_text") or payload.get("text") or "").strip()
        items: list[VisualTextItem] = []
        for item in payload.get("structured_items") or []:
            if isinstance(item, dict):
                items.append(
                    VisualTextItem(
                        type=str(item.get("type") or "other")[:40],
                        name=str(item.get("name") or "")[:120],
                        value=str(item.get("value") or "")[:500],
                        note=str(item.get("note") or "")[:240],
                    )
                )
        entities = [str(x).strip() for x in (payload.get("entities") or []) if str(x).strip()]
        if not entities and markdown:
            entities = [x for x in _extract_titles(markdown)]
        tags = [str(x).strip() for x in (payload.get("visual_tags") or []) if str(x).strip()]
        confidence = max(0.0, min(float(payload.get("confidence") or 0.0), 1.0))
        notes = [str(x).strip() for x in (payload.get("notes") or []) if str(x).strip()]
        return markdown, items, entities, tags, confidence, notes

    async def run(self, args: ExtractVisualTextArgs) -> ToolResult[ExtractVisualTextResult]:
        images = _image_inputs(ImageInputArgs(image_url=args.image_url, image_urls=args.image_urls))
        if not images:
            return ToolResult(ok=False, error="需要 image_url 或 image_urls")
        if not settings.vlm_model:
            return ToolResult(ok=False, error="extract_visual_text 需要配置 VLM_MODEL（建议 Qwen-VL / 百炼视觉模型）")

        mode_hint = {
            "auto": "自动判断图片类型，优先保留文字与可检索实体。",
            "subtitle": "重点读取字幕/台词/对白，不要补写看不清的句子。",
            "ranking": "重点抽取榜单名次、作品名、评分、日期、平台。",
            "magazine": "重点抽取杂志/情报页里的作品名、staff、日期、标题和注释。",
            "ppt": "重点抽取 PPT/导视帧里的标题、作品列表、分数、播放日期、UP主观点短语。",
            "table": "重点还原表格结构，尽量输出 Markdown 表格。",
        }[args.mode]
        question = f"{args.question}\n模式：{args.mode}。{mode_hint}"
        raws = await asyncio.gather(
            *[_call_vlm_with_prompt(url, _OCR_PROMPT, question) for url in images],
            return_exceptions=True,
        )
        markdown_parts: list[str] = []
        items: list[VisualTextItem] = []
        entity_names: list[str] = []
        tags: list[str] = []
        confidences: list[float] = []
        notes: list[str] = []
        raw_parts: list[str] = []
        for idx, raw_item in enumerate(raws):
            if isinstance(raw_item, Exception):
                notes.append(f"image {idx + 1}: {type(raw_item).__name__}: {raw_item}")
                continue
            raw = str(raw_item)
            raw_parts.append(f"[image {idx + 1}]\n{raw}")
            markdown, parsed_items, parsed_entities, parsed_tags, conf, parsed_notes = self._parse_payload(raw)
            if markdown:
                markdown_parts.append(f"## image {idx + 1}\n{markdown}")
            items.extend(parsed_items)
            for name in parsed_entities:
                if name not in entity_names:
                    entity_names.append(name)
            for tag in parsed_tags:
                if tag not in tags:
                    tags.append(tag)
            if conf:
                confidences.append(conf)
            notes.extend(parsed_notes)

        entities: list[AnchoredVisualEntity] = []
        if args.anchor_entities and entity_names:
            entities = await asyncio.gather(
                *[self._anchor_entity(name, args.subject_type, 0.45) for name in entity_names[: args.limit]]
            )
        confidence = sum(confidences) / len(confidences) if confidences else (0.35 if markdown_parts else 0.0)
        data = ExtractVisualTextResult(
            mode=args.mode,
            image_count=len(images),
            markdown_text="\n\n".join(markdown_parts)[:6000],
            structured_items=items[: args.limit],
            entities=entities[: args.limit],
            visual_tags=tags[:16],
            confidence=confidence,
            raw_vlm_answer="\n\n".join(raw_parts)[:1600],
            caveats=[
                "OCR/结构化结果来自 VLM，可能受清晰度、遮挡、字体和日文/中文混排影响。",
                "已回锚的作品实体可作为检索入口；未回锚文本不得当作 canonical 事实。",
                *notes[:4],
            ],
        )
        return ToolResult(
            ok=True,
            data=data,
            sources=[
                Citation(
                    title=e.bangumi_name or e.name,
                    url=f"https://bgm.tv/subject/{e.bangumi_id}" if e.bangumi_id else images[0],
                    source="bangumi" if e.bangumi_id else "image",
                    image=e.image,
                )
                for e in entities[:5]
            ],
        )


class VisualStyleCandidate(BaseModel):
    id: int
    name: str
    score: float | None = None
    image: str | None = None
    matched_tags: list[str] = Field(default_factory=list)
    reason: str = ""


class VisualStyleRecommendArgs(BaseModel):
    image_url: str = Field("", description="单张图片 URL / data URL / upload://...")
    image_urls: list[str] = Field(default_factory=list, description="多张图片 URL / data URL / upload://...；最多处理 4 张")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    question: str = Field("分析画风、色调、构图和题材氛围，并推荐视觉/氛围相近的作品。")
    limit: int = Field(8, ge=1, le=20)


class VisualStyleRecommendResult(BaseModel):
    style_description: str = ""
    visual_tags: list[str] = Field(default_factory=list)
    bangumi_tags: list[str] = Field(default_factory=list)
    candidates: list[VisualStyleCandidate] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    raw_vlm_answer: str = ""
    caveats: list[str] = Field(default_factory=list)


_STYLE_TAG_ALIASES = {
    "日常": "日常",
    "治愈": "治愈",
    "百合": "百合",
    "露营": "露营",
    "户外": "户外",
    "校园": "校园",
    "芳文社": "芳文社",
    "科幻": "科幻",
    "赛博朋克": "赛博朋克",
    "机甲": "机战",
    "魔法少女": "魔法少女",
    "运动": "运动",
    "音乐": "音乐",
    "偶像": "偶像",
    "悬疑": "悬疑",
    "恋爱": "恋爱",
    "奇幻": "奇幻",
    "萌系": "萌",
    "废萌": "废萌",
    "公路": "旅行",
}


class VisualStyleRecommendTool(Tool):
    name = "recommend_by_visual_style"
    description = (
        "根据上传图片的画风/色调/构图/题材标签，映射 Bangumi tag 并召回视觉氛围相近的作品。"
        "这是弱推荐入口，不用于事实判断。"
    )
    args_model = VisualStyleRecommendArgs
    result_model = VisualStyleRecommendResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    def _parse_style(self, raw: str) -> tuple[str, list[str], float]:
        payload = _extract_json(raw)
        if not payload:
            return raw[:600], _extract_titles(raw), 0.25
        desc = str(payload.get("style_description") or payload.get("description") or "").strip()
        tags = [str(x).strip() for x in (payload.get("visual_tags") or payload.get("tags") or []) if str(x).strip()]
        confidence = max(0.0, min(float(payload.get("confidence") or 0.0), 1.0))
        return desc[:800], tags[:16], confidence

    async def run(self, args: VisualStyleRecommendArgs) -> ToolResult[VisualStyleRecommendResult]:
        images = _image_inputs(ImageInputArgs(image_url=args.image_url, image_urls=args.image_urls))
        if not images:
            return ToolResult(ok=False, error="需要 image_url 或 image_urls")
        if not settings.vlm_model:
            return ToolResult(ok=False, error="recommend_by_visual_style 需要配置 VLM_MODEL")
        system = """你是动画/漫画/游戏视觉风格分析助手。只输出 JSON：
{"style_description":"画风、色调、构图、镜头、题材氛围的简短描述",
"visual_tags":["日常","治愈","百合","校园","科幻"...],
"confidence":0.0到1.0}
不要猜测无法从画面看出的事实。"""
        raws = await asyncio.gather(
            *[_call_vlm_with_prompt(url, system, args.question) for url in images],
            return_exceptions=True,
        )
        descs: list[str] = []
        visual_tags: list[str] = []
        confidences: list[float] = []
        raw_parts: list[str] = []
        for idx, raw_item in enumerate(raws):
            if isinstance(raw_item, Exception):
                continue
            raw = str(raw_item)
            raw_parts.append(f"[image {idx + 1}]\n{raw}")
            desc, tags, conf = self._parse_style(raw)
            if desc:
                descs.append(desc)
            for tag in tags:
                if tag not in visual_tags:
                    visual_tags.append(tag)
            if conf:
                confidences.append(conf)
        bangumi_tags: list[str] = []
        for tag in visual_tags:
            mapped = _STYLE_TAG_ALIASES.get(tag) or _STYLE_TAG_ALIASES.get(tag.replace("系", ""))
            if mapped and mapped not in bangumi_tags:
                bangumi_tags.append(mapped)
        if not bangumi_tags:
            bangumi_tags = visual_tags[:4]
        stype = SUBJECT_TYPE.get(args.subject_type, 2)
        pool: dict[int, VisualStyleCandidate] = {}
        for tag in bangumi_tags[:6]:
            try:
                res = await self.client.search_subjects("", stype, sort="rank", limit=10, tags=[tag])
                rows = res.get("data") or []
                if not rows:
                    res = await self.client.search_subjects(tag, stype, sort="match", limit=10)
                    rows = res.get("data") or []
            except Exception:  # noqa: BLE001
                continue
            for row in rows:
                sid = row.get("id")
                if not sid:
                    continue
                img = row.get("images") or {}
                item = pool.get(sid)
                if not item:
                    item = VisualStyleCandidate(
                        id=sid,
                        name=row.get("name_cn") or row.get("name") or f"subject {sid}",
                        score=row.get("score") or ((row.get("rating") or {}).get("score")),
                        image=img.get("common") or img.get("medium") or img.get("grid"),
                        reason="视觉标签召回",
                    )
                    pool[sid] = item
                if tag not in item.matched_tags:
                    item.matched_tags.append(tag)
        candidates = sorted(
            pool.values(),
            key=lambda x: (len(x.matched_tags), x.score or 0),
            reverse=True,
        )[: args.limit]
        confidence = sum(confidences) / len(confidences) if confidences else 0.35
        data = VisualStyleRecommendResult(
            style_description="；".join(descs)[:1000],
            visual_tags=visual_tags[:16],
            bangumi_tags=bangumi_tags[:10],
            candidates=candidates,
            confidence=confidence,
            raw_vlm_answer="\n\n".join(raw_parts)[:1200],
            caveats=[
                "画风/氛围推荐是弱语义入口，不能证明作品事实或制作公司一致。",
                "Bangumi tag 召回会受标签覆盖和用户打标习惯影响，适合做探索，不适合当严格相似度。",
            ],
        )
        return ToolResult(
            ok=True,
            data=data,
            sources=[
                Citation(title=c.name, url=f"https://bgm.tv/subject/{c.id}", source="bangumi", image=c.image)
                for c in candidates[:5]
            ],
        )


class ImageSourceMatch(BaseModel):
    engine: str
    title: str = ""
    url: str = ""
    source_site: str = ""
    source_type: str = "unknown"
    author: str = ""
    similarity: float = 0.0
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    thumbnail: str | None = None
    anilist_id: int | None = None
    episode: str | int | None = None
    timestamp: str = ""
    note: str = ""


def _saucenao_source_type(header: dict[str, Any], data: dict[str, Any]) -> str:
    hay = " ".join(
        str(x or "")
        for x in (
            header.get("index_name"),
            data.get("source"),
            data.get("material"),
            data.get("title"),
            data.get("jp_name"),
            data.get("eng_name"),
            *(data.get("ext_urls") or [])[:3],
        )
    ).lower()
    if any(k in hay for k in ("h-game", "h game", "visual novel", "vndb", "erogame", "dlsite", "getchu")):
        return "galgame"
    if any(k in hay for k in ("manga", "comic", "mangadex", "bookwalker")):
        return "comic"
    if any(k in hay for k in ("anime", "anidb", "anilist", "nyaa")):
        return "anime"
    if any(k in hay for k in ("pixiv", "danbooru", "gelbooru", "yande.re", "konachan", "twitter", "x.com", "deviantart")):
        return "fanart"
    return "unknown"


def _best_saucenao_title(data: dict[str, Any]) -> str:
    for key in ("title", "jp_name", "eng_name", "material", "source"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


class ImageSourceSearchArgs(BaseModel):
    image_url: str = Field("", description="单张图片 URL / data URL / upload://...")
    image_urls: list[str] = Field(default_factory=list, description="多张图片 URL / data URL / upload://...；最多处理 4 张")
    engines: list[Literal["trace_moe", "saucenao", "ascii2d", "pixiv"]] = Field(
        default_factory=lambda: ["trace_moe", "saucenao", "ascii2d", "pixiv"],
        description="trace_moe=动画截图；saucenao=插画/同人溯源；ascii2d/pixiv 仅生成导航入口或使用 SauceNAO 外链。",
    )
    limit: int = Field(8, ge=1, le=20)


class ImageSourceSearchResult(BaseModel):
    matches: list[ImageSourceMatch] = Field(default_factory=list)
    navigation_links: list[dict[str, str]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


async def _saucenao_search(image_url: str, limit: int) -> list[dict[str, Any]]:
    key = _cache_key("saucenao", image_url)
    if (hit := _saucenao_cache.get(key)) is not None:
        return hit
    params = {
        "output_type": 2,
        "api_key": settings.saucenao_api_key,
        "numres": min(limit, 10),
    }
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        payload = _resolve_image_bytes(image_url)
        if payload:
            data, mime = payload
            resp = await client.post(_SAUCENAO_API, params=params, files={"file": ("image", data, mime)})
        else:
            resp = await client.get(_SAUCENAO_API, params={**params, "url": upload_store.resolve_image_url(image_url)})
        resp.raise_for_status()
        body = resp.json() or {}
    result = body.get("results") or []
    _saucenao_cache.set(key, result)
    return result


async def _extract_video_frames(
    *,
    video_url: str = "",
    local_video_path: str = "",
    max_frames: int = 6,
    sample_interval_seconds: int = 30,
) -> list[dict[str, Any]]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg；请安装 ffmpeg 或直接传 frame_image_urls")
    source = local_video_path.strip() or video_url.strip()
    if not source:
        return []
    if local_video_path:
        path = Path(local_video_path).expanduser()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"video not found: {local_video_path}")
        source = str(path)
    interval = max(1, int(sample_interval_seconds))
    max_frames = max(1, min(int(max_frames), 12))
    with tempfile.TemporaryDirectory(prefix="otomo_frames_") as tmp:
        out_pattern = str(Path(tmp) / "frame_%03d.jpg")
        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            source,
            "-vf",
            f"fps=1/{interval}",
            "-frames:v",
            str(max_frames),
            out_pattern,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError((stderr or b"ffmpeg failed").decode("utf-8", errors="ignore")[:500])
        frames: list[dict[str, Any]] = []
        for idx, path in enumerate(sorted(Path(tmp).glob("frame_*.jpg"))):
            frames.append(
                {
                    "index": idx,
                    "timestamp": _format_seconds(float(idx * interval)),
                    "image_url": _data_url_from_bytes(path.read_bytes(), "image/jpeg"),
                }
            )
        return frames


class ImageSourceSearchTool(Tool):
    name = "search_image_source"
    description = (
        "以图搜图/溯源：trace.moe 查动画截图，SauceNAO 查插画/同人/Pixiv 外链；"
        "ascii2d/Pixiv 仅给导航或外链，不后台抓取。"
    )
    args_model = ImageSourceSearchArgs
    result_model = ImageSourceSearchResult

    async def run(self, args: ImageSourceSearchArgs) -> ToolResult[ImageSourceSearchResult]:
        images = _image_inputs(ImageInputArgs(image_url=args.image_url, image_urls=args.image_urls))
        if not images:
            return ToolResult(ok=False, error="需要 image_url 或 image_urls")
        matches: list[ImageSourceMatch] = []
        links: list[dict[str, str]] = []
        caveats = [
            "溯源只返回来源链接/候选，不下载、不托管、不分发原图。",
            "同人图/Pixiv/R18 内容有版权与登录态边界；未配置 SauceNAO 时只提供导航入口。",
        ]
        for idx, image in enumerate(images):
            if "trace_moe" in args.engines:
                try:
                    for row in (await _trace_moe_search(image))[: args.limit]:
                        similarity = float(row.get("similarity") or 0.0)
                        if similarity < 0.7:
                            continue
                        anilist = row.get("anilist")
                        anilist_id = anilist.get("id") if isinstance(anilist, dict) else anilist
                        title = (_anilist_titles(row) or [""])[0]
                        start = row.get("from")
                        timestamp = _format_seconds(float(start)) if isinstance(start, (int, float)) else ""
                        matches.append(
                            ImageSourceMatch(
                                engine="trace.moe",
                                title=title,
                                url=row.get("video") or row.get("image") or "",
                                source_site="trace.moe",
                                similarity=similarity,
                                confidence=min(similarity, 0.99),
                                thumbnail=row.get("image"),
                                anilist_id=int(anilist_id) if isinstance(anilist_id, int) else None,
                                episode=row.get("episode"),
                                timestamp=timestamp,
                                note=f"image {idx + 1} 动画截图反查",
                            )
                        )
                except Exception as e:  # noqa: BLE001
                    caveats.append(f"trace.moe 查询失败：{type(e).__name__}")
            if "saucenao" in args.engines:
                if not settings.saucenao_api_key:
                    caveats.append("未配置 SAUCENAO_API_KEY，已跳过 SauceNAO 结构化溯源。")
                else:
                    try:
                        for row in (await _saucenao_search(image, args.limit))[: args.limit]:
                            header = row.get("header") or {}
                            data = row.get("data") or {}
                            ext_urls = data.get("ext_urls") or []
                            sim = float(header.get("similarity") or 0.0)
                            source_type = _saucenao_source_type(header, data)
                            matches.append(
                                ImageSourceMatch(
                                    engine="saucenao",
                                    title=_best_saucenao_title(data),
                                    url=str(ext_urls[0] if ext_urls else ""),
                                    source_site=str(header.get("index_name") or "SauceNAO"),
                                    source_type=source_type,
                                    author=str(data.get("member_name") or data.get("author_name") or data.get("creator") or ""),
                                    similarity=sim,
                                    confidence=min(max(sim / 100.0, 0.0), 0.99),
                                    thumbnail=header.get("thumbnail"),
                                    note=f"image {idx + 1} SauceNAO 候选 · {source_type}",
                                )
                            )
                    except Exception as e:  # noqa: BLE001
                        caveats.append(f"SauceNAO 查询失败：{type(e).__name__}")
            if image.startswith("http") and "ascii2d" in args.engines:
                links.append({"title": f"ascii2d 搜索 image {idx + 1}", "url": f"https://ascii2d.net/search/url/{image}", "source": "ascii2d"})
        if "pixiv" in args.engines:
            pixiv_links = [m.url for m in matches if "pixiv.net" in m.url]
            if pixiv_links:
                links.extend({"title": "Pixiv 来源候选", "url": url, "source": "pixiv"} for url in pixiv_links[:5])
            else:
                caveats.append("Pixiv 没有稳定公开官方搜索 API；Otomo 不后台抓取 Pixiv，仅使用 SauceNAO 返回的 Pixiv 外链。")
        matches.sort(key=lambda x: x.confidence, reverse=True)
        data = ImageSourceSearchResult(matches=matches[: args.limit], navigation_links=links[:8], caveats=caveats)
        return ToolResult(
            ok=True,
            data=data,
            sources=[
                Citation(title=m.title or m.engine, url=m.url or images[0], source=m.engine, image=m.thumbnail)
                for m in data.matches[:5]
                if m.url or m.thumbnail
            ],
        )


ImageRoute = Literal["auto", "anime", "galgame", "comic", "novel", "fanart", "unknown"]


class RoutedImageCandidate(BaseModel):
    route: str = "unknown"
    title: str = ""
    reason: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    source: str = ""
    source_site: str = ""
    url: str = ""
    thumbnail: str | None = None
    image_index: int = 0
    bangumi_type: Literal["anime", "book", "music", "game", "real"] | None = None
    bangumi_id: int | None = None
    bangumi_name: str = ""
    bangumi_score: float | None = None
    external_id: str = ""
    author: str = ""
    anilist_id: int | None = None
    episode: str | int | None = None
    from_seconds: float | None = None
    to_seconds: float | None = None
    timestamp: str = ""
    match_note: str = ""
    evidence: list[str] = Field(default_factory=list)
    note: str = ""


class RouteImageSourceArgs(BaseModel):
    image_url: str = Field("", description="单张图片 URL / data URL / upload://...")
    image_urls: list[str] = Field(default_factory=list, description="多张图片 URL / data URL / upload://...；最多处理 4 张")
    routes: list[ImageRoute] = Field(
        default_factory=lambda: ["auto"],
        description="期望路由：auto/anime/galgame/comic/novel/fanart/unknown；auto 会多源并行。",
    )
    question: str = Field("判断这张 ACGN 图片可能是什么来源，并给出候选。")
    use_ocr: bool = Field(True, description="配置 VLM 后，用 OCR/视觉模型抽文字与作品名。")
    include_book_sources: bool = Field(True, description="对漫画/小说/书封候选补 Google Books/Open Library/MangaDex。")
    include_paid_reverse: bool = Field(False, description="配置 SERPAPI_API_KEY 且图片是公网 URL 时，才调用 SerpApi 付费反搜。")
    limit: int = Field(10, ge=1, le=20)


class RouteImageSourceResult(BaseModel):
    image_refs: list[str] = Field(default_factory=list)
    routes_considered: list[str] = Field(default_factory=list)
    decision: str = "low_confidence"
    needs_user_confirmation: bool = True
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    candidates: list[RoutedImageCandidate] = Field(default_factory=list)
    character_candidates: list[CharacterCandidate] = Field(default_factory=list)
    ocr_text: str = ""
    visual_tags: list[str] = Field(default_factory=list)
    raw_vlm_answer: str = ""
    navigation_links: list[dict[str, str]] = Field(default_factory=list)
    next_tools: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


def _route_set(routes: list[ImageRoute]) -> set[str]:
    values = {str(r) for r in routes if str(r).strip()}
    return values or {"auto"}


def _route_wants(routes: set[str], *names: str) -> bool:
    return "auto" in routes or any(name in routes for name in names)


def _bangumi_type_for_route(route: str) -> Literal["anime", "book", "music", "game", "real"] | None:
    if route == "anime":
        return "anime"
    if route == "galgame":
        return "game"
    if route in {"comic", "novel"}:
        return "book"
    return None


def _candidate_key(c: RoutedImageCandidate) -> str:
    if c.bangumi_id and c.bangumi_type:
        return f"bangumi:{c.bangumi_type}:{c.bangumi_id}"
    if c.url:
        return f"url:{c.url.lower()}"
    return f"{c.route}:{c.source}:{c.title.lower()}"


def _merge_route_candidates(items: list[RoutedImageCandidate], limit: int) -> list[RoutedImageCandidate]:
    merged: dict[str, RoutedImageCandidate] = {}
    for cand in sorted(items, key=lambda x: x.confidence, reverse=True):
        key = _candidate_key(cand)
        if key not in merged:
            merged[key] = cand
            continue
        old = merged[key]
        old.confidence = max(old.confidence, cand.confidence)
        if cand.thumbnail and not old.thumbnail:
            old.thumbnail = cand.thumbnail
        if cand.url and not old.url:
            old.url = cand.url
        if cand.bangumi_id and not old.bangumi_id:
            old.bangumi_id = cand.bangumi_id
            old.bangumi_type = cand.bangumi_type
            old.bangumi_name = cand.bangumi_name
            old.bangumi_score = cand.bangumi_score
        if cand.reason and cand.reason not in old.reason:
            old.reason = (old.reason + "；" + cand.reason).strip("；")
        if cand.match_note and cand.match_note not in old.match_note:
            old.match_note = (old.match_note + "；" + cand.match_note).strip("；")
        if cand.episode is not None and old.episode is None:
            old.episode = cand.episode
        if cand.timestamp and not old.timestamp:
            old.timestamp = cand.timestamp
        if cand.from_seconds is not None and old.from_seconds is None:
            old.from_seconds = cand.from_seconds
        if cand.to_seconds is not None and old.to_seconds is None:
            old.to_seconds = cand.to_seconds
        for ev in cand.evidence:
            if ev and ev not in old.evidence:
                old.evidence.append(ev)
        if cand.note and cand.note not in old.note:
            old.note = (old.note + "；" + cand.note).strip("；")
    return list(merged.values())[:limit]


def _candidate_source_families(c: RoutedImageCandidate) -> set[str]:
    text = " ".join([c.source, c.source_site, c.note, c.match_note, *c.evidence]).lower()
    families: set[str] = set()
    if "trace.moe" in text or c.source == "trace.moe":
        families.add("trace.moe")
    if "saucenao" in text or c.source == "saucenao":
        families.add("saucenao")
    if "vlm 视觉" in " ".join(c.evidence).lower() or c.source == "vlm_semantic":
        families.add("vlm_semantic")
    if "ocr" in text or c.source == "vlm_ocr":
        families.add("ocr")
    if c.source in {"google_books", "open_library", "mangadex"} or "metadata" in text:
        families.add(c.source or "metadata")
    if "serpapi" in text or c.source == "serpapi_google_reverse":
        families.add("serpapi")
    if not families and c.source:
        families.add(c.source)
    return families


def _trace_similarity(c: RoutedImageCandidate) -> float:
    for ev in c.evidence:
        if not ev.startswith("trace.moe similarity="):
            continue
        try:
            return float(ev.split("=", 1)[1])
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _route_decision(candidates: list[RoutedImageCandidate]) -> tuple[str, bool, float]:
    if not candidates:
        return "no_candidate", True, 0.0
    top = candidates[0]
    top_conf = top.confidence
    second = candidates[1].confidence if len(candidates) > 1 else 0.0
    same_route_gap = not (second > 0 and top_conf - second < 0.08 and candidates[1].route != top.route)
    families = _candidate_source_families(top)

    # ACGN reverse image search is noisy. Keep this as a candidate generator
    # unless evidence is unusually strong or independently corroborated.
    multi_source_supported = top.bangumi_id is not None and len(families) >= 2 and top_conf >= 0.78
    very_high_trace = top.source == "trace.moe" and top.bangumi_id is not None and _trace_similarity(top) >= 0.97
    accepted = same_route_gap and (multi_source_supported or very_high_trace)
    if accepted:
        return f"likely_{top.route}", False, top_conf
    return "needs_user_confirmation", True, top_conf


async def _anchor_route_candidate(
    client: BangumiClient,
    *,
    title: str,
    route: str,
    image_index: int,
    source: str,
    confidence: float,
    url: str = "",
    source_site: str = "",
    thumbnail: str | None = None,
    author: str = "",
    evidence: list[str] | None = None,
    note: str = "",
    reason: str = "",
    episode: str | int | None = None,
    anilist_id: int | None = None,
    from_seconds: float | None = None,
    to_seconds: float | None = None,
    timestamp: str = "",
) -> RoutedImageCandidate:
    bangumi_type = _bangumi_type_for_route(route)
    cand = RoutedImageCandidate(
        route=route,
        title=title,
        confidence=max(0.0, min(confidence, 0.99)),
        source=source,
        source_site=source_site,
        url=url,
        thumbnail=thumbnail,
        image_index=image_index,
        bangumi_type=bangumi_type,
        author=author,
        anilist_id=anilist_id,
        evidence=evidence or [],
        note=note,
        reason=reason,
        episode=episode,
        from_seconds=from_seconds,
        to_seconds=to_seconds,
        timestamp=timestamp,
    )
    if not title or not bangumi_type:
        return cand
    try:
        res = await client.search_subjects(title, SUBJECT_TYPE.get(bangumi_type), limit=3)
    except Exception:  # noqa: BLE001
        cand.evidence.append("Bangumi 回锚失败")
        return cand
    rows = res.get("data") or []
    if not rows:
        cand.evidence.append(f"Bangumi {bangumi_type} 未命中")
        cand.match_note = f"Bangumi {bangumi_type} 未对齐"
        return cand
    row = rows[0]
    cand.bangumi_id = row.get("id")
    cand.bangumi_name = row.get("name_cn") or row.get("name") or ""
    cand.bangumi_score = row.get("score") or ((row.get("rating") or {}).get("score"))
    images = row.get("images") or {}
    cand.thumbnail = cand.thumbnail or images.get("common") or images.get("medium") or images.get("grid")
    cand.url = cand.url or (f"https://bgm.tv/subject/{cand.bangumi_id}" if cand.bangumi_id else "")
    cand.confidence = min(0.98, max(cand.confidence, cand.confidence + 0.08))
    cand.evidence.append(f"已回锚 Bangumi {bangumi_type}")
    cand.match_note = f"已回锚 Bangumi {bangumi_type}"
    return cand


async def _anchor_visual_candidate(client: BangumiClient, cand: VisualCandidate, subject_type: str) -> None:
    stype = SUBJECT_TYPE.get(subject_type, 2)
    try:
        res = await client.search_subjects(cand.title, stype, limit=3)
    except Exception:  # noqa: BLE001
        return
    rows = res.get("data") or []
    if not rows:
        cand.match_note = "Bangumi 未对齐"
        return
    row = rows[0]
    cand.bangumi_id = row.get("id")
    cand.bangumi_name = row.get("name_cn") or row.get("name") or ""
    cand.bangumi_score = row.get("score") or ((row.get("rating") or {}).get("score"))
    img = row.get("images") or {}
    cand.image = img.get("common") or img.get("medium") or img.get("grid") or cand.image
    cand.confidence = min(0.98, max(cand.confidence, cand.confidence + (0.06 if cand.source == "trace.moe" else 0.12)))
    cand.match_note = "已回锚 Bangumi；截图/画面来源仍需结合候选置信度确认。"


async def _google_books_search(query: str, limit: int) -> list[dict[str, Any]]:
    key = _cache_key("google_books", query)
    if (hit := _external_image_cache.get(key)) is not None:
        return hit
    params = {"q": query, "maxResults": min(limit, 10), "printType": "books"}
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(_GOOGLE_BOOKS_API, params=params)
        resp.raise_for_status()
        payload = resp.json() or {}
    items: list[dict[str, Any]] = []
    for row in payload.get("items") or []:
        info = row.get("volumeInfo") or {}
        ids = info.get("industryIdentifiers") or []
        isbn = next((str(x.get("identifier") or "") for x in ids if x.get("identifier")), "")
        images = info.get("imageLinks") or {}
        items.append(
            {
                "source": "google_books",
                "title": info.get("title") or "",
                "author": " / ".join(info.get("authors") or []),
                "published": info.get("publishedDate") or "",
                "url": info.get("infoLink") or info.get("canonicalVolumeLink") or "",
                "thumbnail": images.get("thumbnail") or images.get("smallThumbnail"),
                "external_id": row.get("id") or isbn,
                "note": f"Google Books {info.get('publishedDate') or ''}".strip(),
            }
        )
    _external_image_cache.set(key, items)
    return items


async def _open_library_search(query: str, limit: int) -> list[dict[str, Any]]:
    key = _cache_key("open_library", query)
    if (hit := _external_image_cache.get(key)) is not None:
        return hit
    params = {"q": query, "limit": min(limit, 10)}
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(_OPEN_LIBRARY_SEARCH_API, params=params)
        resp.raise_for_status()
        payload = resp.json() or {}
    items: list[dict[str, Any]] = []
    for row in payload.get("docs") or []:
        cover_i = row.get("cover_i")
        key_path = row.get("key") or ""
        items.append(
            {
                "source": "open_library",
                "title": row.get("title") or "",
                "author": " / ".join(row.get("author_name") or []),
                "published": str(row.get("first_publish_year") or ""),
                "url": f"https://openlibrary.org{key_path}" if key_path else "",
                "thumbnail": f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg" if cover_i else None,
                "external_id": key_path,
                "note": f"Open Library {row.get('first_publish_year') or ''}".strip(),
            }
        )
    _external_image_cache.set(key, items)
    return items


async def _mangadex_search(query: str, limit: int) -> list[dict[str, Any]]:
    key = _cache_key("mangadex", query)
    if (hit := _external_image_cache.get(key)) is not None:
        return hit
    params = {"title": query, "limit": min(limit, 10), "order[relevance]": "desc"}
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(_MANGADEX_MANGA_API, params=params)
        resp.raise_for_status()
        payload = resp.json() or {}
    items: list[dict[str, Any]] = []
    for row in payload.get("data") or []:
        attrs = row.get("attributes") or {}
        titles = attrs.get("title") or {}
        alt_titles = attrs.get("altTitles") or []
        title = titles.get("zh") or titles.get("ja-ro") or titles.get("ja") or titles.get("en") or next(iter(titles.values()), "")
        if not title:
            for item in alt_titles:
                if isinstance(item, dict) and item:
                    title = next(iter(item.values()))
                    break
        tags = []
        for tag in attrs.get("tags") or []:
            name = ((tag.get("attributes") or {}).get("name") or {}).get("en")
            if name:
                tags.append(name)
        mid = row.get("id") or ""
        items.append(
            {
                "source": "mangadex",
                "title": title,
                "author": "",
                "published": str(attrs.get("year") or ""),
                "url": f"https://mangadex.org/title/{mid}" if mid else "",
                "thumbnail": None,
                "external_id": mid,
                "note": " / ".join([attrs.get("status") or "", *tags[:3]]).strip(" /"),
            }
        )
    _external_image_cache.set(key, items)
    return items


async def _serpapi_reverse_image(image_url: str, limit: int) -> list[dict[str, Any]]:
    if not settings.serpapi_api_key or not image_url.startswith(("http://", "https://")):
        return []
    key = _cache_key("serpapi_reverse", image_url)
    if (hit := _external_image_cache.get(key)) is not None:
        return hit
    params = {
        "engine": "google_reverse_image",
        "image_url": image_url,
        "api_key": settings.serpapi_api_key,
    }
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(_SERPAPI_SEARCH_API, params=params)
        resp.raise_for_status()
        payload = resp.json() or {}
    rows = payload.get("image_results") or payload.get("inline_images") or payload.get("visual_matches") or []
    items: list[dict[str, Any]] = []
    for row in rows[:limit]:
        items.append(
            {
                "source": "serpapi_google_reverse",
                "title": row.get("title") or row.get("snippet") or "",
                "url": row.get("link") or row.get("source") or row.get("original") or "",
                "thumbnail": row.get("thumbnail") or row.get("image") or row.get("original"),
                "source_site": row.get("source") or "Google Reverse Image",
                "note": "SerpApi Google Reverse Image 付费兜底",
            }
        )
    _external_image_cache.set(key, items)
    return items


class RouteImageSourceTool(Tool):
    name = "route_image_source"
    description = (
        "多源图片来源路由：动画截图用 trace.moe+Bangumi，galgame CG/同人图用 SauceNAO，"
        "漫画/小说/书封走 OCR+Bangumi book+Google Books/Open Library/MangaDex，"
        "fanart/未知图给 Pixiv/ascii2d/IQDB 等外链候选；低置信时要求用户确认。"
    )
    args_model = RouteImageSourceArgs
    result_model = RouteImageSourceResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

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
        cand.match_note = "已用 Bangumi character search 回锚。"

    async def _trace_candidates(self, image: str, idx: int, limit: int) -> list[RoutedImageCandidate]:
        out: list[RoutedImageCandidate] = []
        try:
            rows = await _trace_moe_search(image)
        except Exception as e:  # noqa: BLE001
            return [
                RoutedImageCandidate(
                    route="anime",
                    source="trace.moe",
                    title="",
                    confidence=0.0,
                    image_index=idx,
                    note=f"trace.moe 查询失败：{type(e).__name__}",
                )
            ]
        for row in rows[:limit]:
            sim = float(row.get("similarity") or 0.0)
            if sim < 0.5:
                continue
            titles = _anilist_titles(row)
            if not titles:
                continue
            start = row.get("from")
            end = row.get("to")
            timestamp = _format_seconds(float(start)) if isinstance(start, (int, float)) else ""
            anilist = row.get("anilist")
            anilist_id = anilist.get("id") if isinstance(anilist, dict) else anilist
            cand = await _anchor_route_candidate(
                self.client,
                title=titles[0],
                route="anime",
                image_index=idx,
                source="trace.moe",
                confidence=min(sim, 0.99),
                url=row.get("video") or row.get("image") or "",
                source_site="trace.moe",
                thumbnail=row.get("image"),
                evidence=[f"trace.moe similarity={sim:.2f}"],
                note="动画截图反查；相似度不是 canonical 事实",
                reason=(
                    "trace.moe 动画截图反查"
                    + (f" · 第 {row.get('episode')} 集" if row.get("episode") is not None else "")
                    + (f" · {timestamp}" if timestamp else "")
                ),
                episode=row.get("episode"),
                anilist_id=int(anilist_id) if isinstance(anilist_id, int) else None,
                from_seconds=float(start) if isinstance(start, (int, float)) else None,
                to_seconds=float(end) if isinstance(end, (int, float)) else None,
                timestamp=timestamp,
            )
            out.append(cand)
        return out

    def _parse_semantic_payload(
        self,
        raw: str,
        image_index: int,
        limit: int,
    ) -> tuple[list[tuple[str, str, float]], list[CharacterCandidate], list[str], str]:
        payload = _extract_json(raw)
        titles: list[tuple[str, str, float]] = []
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
                    titles.append(
                        (
                            title,
                            str(item.get("reason") or "")[:180],
                            max(0.0, min(float(item.get("confidence") or 0.0), 1.0)),
                        )
                    )
        if not titles:
            titles = [(title, "从 VLM 自然语言回答中抽取", 0.35) for title in _extract_titles(raw)]
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
        return titles, characters, tags, ocr_text

    async def _vlm_semantic_candidates(
        self,
        images: list[str],
        routes: set[str],
        limit: int,
        question_hint: str,
    ) -> tuple[list[RoutedImageCandidate], list[CharacterCandidate], list[str], str, str, list[str]]:
        if not settings.vlm_model:
            return [], [], [], "", "", ["未配置 VLM_MODEL，无法读取角色、画面语义或 VLM 作品候选。"]
        raws = await asyncio.gather(
            *[_safe_call_vlm(url, question_hint) for url in images],
            return_exceptions=True,
        )
        subject_routes: list[str] = []
        if _route_wants(routes, "anime"):
            subject_routes.append("anime")
        if _route_wants(routes, "galgame"):
            subject_routes.append("galgame")
        if _route_wants(routes, "comic"):
            subject_routes.append("comic")
        if _route_wants(routes, "novel"):
            subject_routes.append("novel")
        if "auto" in routes and not subject_routes:
            subject_routes = ["anime", "galgame", "comic", "novel"]
        candidates: list[RoutedImageCandidate] = []
        characters: list[CharacterCandidate] = []
        tags: list[str] = []
        ocr_parts: list[str] = []
        raw_parts: list[str] = []
        caveats: list[str] = []
        tasks: list[Any] = []
        for idx, raw_item in enumerate(raws):
            if isinstance(raw_item, Exception):
                caveats.append(f"image {idx + 1} VLM 识别失败：{type(raw_item).__name__}")
                continue
            raw = str(raw_item)
            raw_parts.append(f"[semantic image {idx + 1}]\n{raw}")
            titles, chars, parsed_tags, ocr_text = self._parse_semantic_payload(raw, idx, limit)
            characters.extend(chars)
            for tag in parsed_tags:
                if tag not in tags:
                    tags.append(tag)
            if ocr_text:
                ocr_parts.append(f"[image {idx + 1}] {ocr_text}")
            for title, reason, confidence in titles[:limit]:
                for route in subject_routes:
                    tasks.append(
                        _anchor_route_candidate(
                            self.client,
                            title=title,
                            route=route,
                            image_index=idx,
                            source="vlm",
                            source_site="VLM",
                            confidence=confidence or 0.35,
                            reason=reason,
                            evidence=["VLM 视觉语义候选"],
                            note="VLM 候选已尝试回锚；仍需用户确认截图是否匹配。",
                        )
                    )
        if tasks:
            candidates = await asyncio.gather(*tasks)
        if characters:
            await asyncio.gather(*[self._anchor_character(c) for c in characters])
            dedup: dict[str, CharacterCandidate] = {}
            for cand in sorted(characters, key=lambda x: x.confidence, reverse=True):
                key = str(cand.bangumi_id or "").strip() or cand.name.lower()
                dedup.setdefault(key, cand)
            characters = list(dedup.values())[:limit]
        return candidates, characters, tags[:16], "\n".join(ocr_parts)[:2000], "\n\n".join(raw_parts)[:1600], caveats

    async def _saucenao_candidates(self, image: str, idx: int, routes: set[str], limit: int) -> list[RoutedImageCandidate]:
        if not settings.saucenao_api_key:
            return []
        out: list[RoutedImageCandidate] = []
        rows = await _saucenao_search(image, limit)
        for row in rows[:limit]:
            header = row.get("header") or {}
            data = row.get("data") or {}
            sim = float(header.get("similarity") or 0.0)
            source_type = _saucenao_source_type(header, data)
            if source_type == "unknown":
                source_type = "fanart" if _route_wants(routes, "fanart") else "unknown"
            if not _route_wants(routes, source_type, "unknown", "fanart"):
                continue
            title = _best_saucenao_title(data)
            ext_urls = [str(x) for x in (data.get("ext_urls") or []) if str(x).strip()]
            cand = await _anchor_route_candidate(
                self.client,
                title=title,
                route=source_type,
                image_index=idx,
                source="saucenao",
                confidence=min(max(sim / 100.0, 0.0), 0.99),
                url=ext_urls[0] if ext_urls else "",
                source_site=str(header.get("index_name") or "SauceNAO"),
                thumbnail=header.get("thumbnail"),
                author=str(data.get("member_name") or data.get("author_name") or data.get("creator") or ""),
                evidence=[f"SauceNAO {header.get('index_name') or ''} similarity={sim:.1f}".strip()],
                note=f"SauceNAO 分类为 {source_type}；同人/CG 来源不等于作品事实",
            )
            out.append(cand)
        return out

    async def _ocr_candidates(
        self,
        images: list[str],
        routes: set[str],
        limit: int,
        question_hint: str,
    ) -> tuple[list[RoutedImageCandidate], str, list[str], list[str]]:
        if not settings.vlm_model:
            return [], "", [], ["未配置 VLM_MODEL，无法做 OCR-first 漫画/小说/书封识别。"]
        question = (
            "识别图片中的标题、作品名、角色名、ISBN、出版社、卷数、榜单文字或台词。"
            "如果像漫画页/轻小说封面/游戏CG，请优先抽取可检索的日文/中文/英文标题。"
        )
        raws = await asyncio.gather(
            *[_call_vlm_with_prompt(url, _OCR_PROMPT, f"{question}\n用户问题：{question_hint}") for url in images],
            return_exceptions=True,
        )
        ocr_parts: list[str] = []
        tags: list[str] = []
        entity_names: list[tuple[str, int]] = []
        caveats: list[str] = []
        for idx, raw_item in enumerate(raws):
            if isinstance(raw_item, Exception):
                caveats.append(f"image {idx + 1} OCR 失败：{type(raw_item).__name__}")
                continue
            raw = str(raw_item)
            payload = _extract_json(raw)
            markdown = str(payload.get("markdown_text") or payload.get("text") or "").strip() if payload else raw[:1200]
            if markdown:
                ocr_parts.append(f"[image {idx + 1}] {markdown}")
            for tag in (payload.get("visual_tags") or []) if isinstance(payload, dict) else []:
                value = str(tag).strip()
                if value and value not in tags:
                    tags.append(value)
            names = [str(x).strip() for x in (payload.get("entities") or []) if str(x).strip()] if isinstance(payload, dict) else []
            if not names:
                names = _extract_titles(markdown or raw)
            for name in names[:limit]:
                entity_names.append((name, idx))
        subject_routes: list[str] = []
        if _route_wants(routes, "anime"):
            subject_routes.append("anime")
        if _route_wants(routes, "galgame"):
            subject_routes.append("galgame")
        if _route_wants(routes, "comic"):
            subject_routes.append("comic")
        if _route_wants(routes, "novel"):
            subject_routes.append("novel")
        if "auto" in routes and not subject_routes:
            subject_routes = ["anime", "comic", "novel", "galgame"]
        out: list[RoutedImageCandidate] = []
        tasks = []
        for name, idx in entity_names[:limit]:
            for route in subject_routes:
                tasks.append(
                    _anchor_route_candidate(
                        self.client,
                        title=name,
                        route=route,
                        image_index=idx,
                        source="vlm_ocr",
                        confidence=0.5,
                        source_site="VLM OCR",
                        evidence=["OCR/VLM 抽取实体"],
                        note="OCR-first 候选，需要结合封面/标题确认",
                    )
                )
        if tasks:
            out = await asyncio.gather(*tasks)
        return out, "\n\n".join(ocr_parts)[:3000], tags[:16], caveats

    async def _book_source_candidates(self, names: list[str], routes: set[str], limit: int) -> tuple[list[RoutedImageCandidate], list[str]]:
        if not names or not _route_wants(routes, "comic", "novel"):
            return [], []
        out: list[RoutedImageCandidate] = []
        caveats: list[str] = []
        queries = []
        for name in names:
            clean = name.strip()
            if clean and clean not in queries:
                queries.append(clean)
        for query in queries[:4]:
            sources: list[dict[str, Any]] = []
            jobs = [_google_books_search(query, 4), _open_library_search(query, 4)]
            if _route_wants(routes, "comic"):
                jobs.append(_mangadex_search(query, 4))
            results = await asyncio.gather(*jobs, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    caveats.append(f"{query} 外部书籍源查询失败：{type(result).__name__}")
                else:
                    sources.extend(result)
            for item in sources[:limit]:
                source = str(item.get("source") or "book_source")
                if source == "mangadex":
                    route = "comic"
                elif "novel" in routes or "auto" in routes:
                    route = "novel"
                else:
                    route = "comic"
                out.append(
                    RoutedImageCandidate(
                        route=route,
                        title=str(item.get("title") or query),
                        confidence=0.58 if source != "mangadex" else 0.62,
                        source=source,
                        source_site=source,
                        url=str(item.get("url") or ""),
                        thumbnail=item.get("thumbnail"),
                        external_id=str(item.get("external_id") or ""),
                        author=str(item.get("author") or ""),
                        evidence=[f"{source} metadata"],
                        note=str(item.get("note") or "外部书籍/漫画元数据源"),
                    )
                )
        return out, caveats

    async def run(self, args: RouteImageSourceArgs) -> ToolResult[RouteImageSourceResult]:
        images = _image_inputs(ImageInputArgs(image_url=args.image_url, image_urls=args.image_urls))
        if not images:
            return ToolResult(ok=False, error="需要 image_url 或 image_urls")
        routes = _route_set(args.routes)
        candidates: list[RoutedImageCandidate] = []
        caveats = [
            "图片来源识别是多源弱证据聚合；低置信或多候选接近时必须让用户确认。",
            "trace.moe/SauceNAO/OCR 结果都不是 canonical 事实，最终作品事实仍需回 Bangumi/VNDB/EGS 等工具核验。",
        ]
        nav: list[dict[str, str]] = []

        jobs: list[Any] = []
        if _route_wants(routes, "anime"):
            jobs.extend(self._trace_candidates(image, idx, args.limit) for idx, image in enumerate(images))
        if _route_wants(routes, "galgame", "fanart", "comic", "unknown", "anime"):
            if settings.saucenao_api_key:
                jobs.extend(self._saucenao_candidates(image, idx, routes, args.limit) for idx, image in enumerate(images))
            else:
                caveats.append("未配置 SAUCENAO_API_KEY，galgame CG / fanart / booru / Pixiv 溯源会明显变弱。")
        if jobs:
            for batch in await asyncio.gather(*jobs, return_exceptions=True):
                if isinstance(batch, Exception):
                    caveats.append(f"图片源查询失败：{type(batch).__name__}")
                else:
                    candidates.extend(batch)

        ocr_text = ""
        raw_vlm_answer = ""
        character_candidates: list[CharacterCandidate] = []
        tags: list[str] = []
        semantic_ocr_text = ""
        semantic_tags: list[str] = []
        if args.use_ocr and _route_wants(routes, "anime", "galgame", "comic", "novel", "unknown", "fanart"):
            semantic_candidates, characters, semantic_tags, semantic_ocr_text, semantic_raw, semantic_caveats = await self._vlm_semantic_candidates(
                images, routes, args.limit, args.question
            )
            candidates.extend(semantic_candidates)
            character_candidates.extend(characters)
            raw_vlm_answer = semantic_raw
            if semantic_ocr_text:
                ocr_text = semantic_ocr_text
            for tag in semantic_tags:
                if tag not in tags:
                    tags.append(tag)
            caveats.extend(semantic_caveats)
        if args.use_ocr and _route_wants(routes, "anime", "galgame", "comic", "novel", "unknown"):
            ocr_candidates, ocr_text_result, ocr_tags, ocr_caveats = await self._ocr_candidates(images, routes, args.limit, args.question)
            candidates.extend(ocr_candidates)
            if ocr_text_result:
                ocr_text = f"{semantic_ocr_text}\n\n{ocr_text_result}".strip()[:3000] if semantic_ocr_text else ocr_text_result
            for tag in ocr_tags:
                if tag not in tags:
                    tags.append(tag)
            caveats.extend(ocr_caveats)

        ocr_names = [c.title for c in candidates if c.source == "vlm_ocr" and c.title]
        if args.include_book_sources:
            book_candidates, book_caveats = await self._book_source_candidates(ocr_names, routes, args.limit)
            candidates.extend(book_candidates)
            caveats.extend(book_caveats)

        if args.include_paid_reverse:
            if settings.serpapi_api_key:
                for idx, image in enumerate(images):
                    try:
                        for item in await _serpapi_reverse_image(image, args.limit):
                            candidates.append(
                                RoutedImageCandidate(
                                    route="unknown",
                                    title=str(item.get("title") or ""),
                                    confidence=0.52,
                                    source="serpapi_google_reverse",
                                    source_site=str(item.get("source_site") or "Google Reverse Image"),
                                    url=str(item.get("url") or ""),
                                    thumbnail=item.get("thumbnail"),
                                    image_index=idx,
                                    evidence=["Google Reverse Image 付费兜底"],
                                    note=str(item.get("note") or ""),
                                )
                            )
                    except Exception as e:  # noqa: BLE001
                        caveats.append(f"SerpApi 反搜失败：{type(e).__name__}")
            else:
                caveats.append("include_paid_reverse=true 但未配置 SERPAPI_API_KEY，已跳过 SerpApi。")

        for idx, image in enumerate(images):
            if image.startswith(("http://", "https://")):
                nav.append({"title": f"ascii2d 搜索 image {idx + 1}", "url": f"https://ascii2d.net/search/url/{image}", "source": "ascii2d"})
                nav.append({"title": f"IQDB 搜索 image {idx + 1}", "url": f"https://iqdb.org/?url={quote_plus(image)}", "source": "iqdb"})
                nav.append({"title": f"Google Lens image {idx + 1}", "url": f"https://lens.google.com/uploadbyurl?url={quote_plus(image)}", "source": "google_lens"})
            else:
                nav.append({"title": f"ascii2d 上传入口 image {idx + 1}", "url": "https://ascii2d.net/", "source": "ascii2d"})
                nav.append({"title": f"IQDB 上传入口 image {idx + 1}", "url": "https://iqdb.org/", "source": "iqdb"})

        candidates = [c for c in candidates if c.title or c.url or c.note]
        candidates = _merge_route_candidates(candidates, args.limit)
        candidates.sort(key=lambda x: x.confidence, reverse=True)
        decision, needs_confirmation, top = _route_decision(candidates)
        if needs_confirmation and candidates:
            caveats.append("图片反搜只给候选；当前证据不足以直接确认来源，请让用户从候选中确认或补充截图/上下文。")
        next_tools = []
        if any(c.route == "anime" for c in candidates):
            next_tools.extend(["get_subject", "get_subject_episodes"])
        if any(c.route == "galgame" for c in candidates):
            next_tools.extend(["search_visual_novels", "search_erogamescape", "review_subject"])
        if any(c.route in {"comic", "novel"} for c in candidates):
            next_tools.extend(["search_subjects(type=book)", "review_subject"])
        if any(c.route in {"fanart", "unknown"} for c in candidates):
            next_tools.extend(["search_image_source", "extract_visual_text"])
        seen_tools: list[str] = []
        for tool in next_tools:
            if tool not in seen_tools:
                seen_tools.append(tool)
        data = RouteImageSourceResult(
            image_refs=[
                url if (url.startswith("upload://") or url.startswith("http://") or url.startswith("https://")) else ""
                for url in images
            ],
            routes_considered=sorted(routes),
            decision=decision,
            needs_user_confirmation=needs_confirmation,
            confidence=top,
            candidates=candidates[: args.limit],
            character_candidates=character_candidates[: args.limit],
            ocr_text=ocr_text,
            visual_tags=tags[:16],
            raw_vlm_answer=raw_vlm_answer,
            navigation_links=nav[:10],
            next_tools=seen_tools[:8],
            caveats=caveats[:10],
        )
        return ToolResult(
            ok=True,
            data=data,
            sources=[
                Citation(
                    title=c.bangumi_name or c.title or c.source,
                    url=f"https://bgm.tv/subject/{c.bangumi_id}" if c.bangumi_id else (c.url or images[min(c.image_index, len(images) - 1)]),
                    source=c.source or c.route,
                    image=c.thumbnail,
                )
                for c in data.candidates[:5]
                if c.title or c.url or c.bangumi_id
            ],
        )


class VideoFrameEvidence(BaseModel):
    index: int
    timestamp: str = ""
    ocr_text: str = ""
    structured_items: list[VisualTextItem] = Field(default_factory=list)
    candidates: list[VisualCandidate] = Field(default_factory=list)
    visual_tags: list[str] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class AnalyzeVideoFramesArgs(BaseModel):
    video_url: str = Field("", description="可直接访问的视频文件 URL；不支持 B站普通页面 URL 后台下载")
    local_video_path: str = Field("", description="本地视频文件路径；仅处理用户自己提供/有权分析的视频")
    frame_image_urls: list[str] = Field(default_factory=list, description="已经截好的关键帧 URL / data URL / upload://...；优先使用")
    purpose: Literal["ocr", "identify", "both"] = Field("both", description="ocr=抽文字；identify=关键帧识番；both=两者都做")
    mode: Literal["auto", "subtitle", "ranking", "magazine", "ppt", "table"] = "ppt"
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    max_frames: int = Field(6, ge=1, le=12)
    sample_interval_seconds: int = Field(30, ge=5, le=300)
    question: str = Field("分析视频关键帧里的文字、榜单、作品和视觉线索。")


class AnalyzeVideoFramesResult(BaseModel):
    frame_count: int = 0
    purpose: str = "both"
    frames: list[VideoFrameEvidence] = Field(default_factory=list)
    merged_ocr_text: str = ""
    candidate_subjects: list[VisualCandidate] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class AnalyzeVideoFramesTool(Tool):
    name = "analyze_video_frames"
    description = (
        "对用户提供的视频文件/直链或关键帧图片抽帧分析：无字幕 PPT/榜单视频可 OCR，动画片段可用 trace.moe 做关键帧识番。"
        "不后台下载 B站普通页面视频；如需分析 B站无字幕内容，请上传/提供有权分析的视频或关键帧。"
    )
    args_model = AnalyzeVideoFramesArgs
    result_model = AnalyzeVideoFramesResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def _ocr_frame(self, image_url: str, args: AnalyzeVideoFramesArgs) -> tuple[str, list[VisualTextItem], list[str], float]:
        if not settings.vlm_model:
            return "", [], [], 0.0
        mode_hint = f"视频关键帧 OCR，mode={args.mode}，重点保留榜单/PPT/字幕中的作品名、评分、日期、观点短语。"
        raw = await _call_vlm_with_prompt(image_url, _OCR_PROMPT, f"{args.question}\n{mode_hint}")
        payload = _extract_json(raw)
        if not payload:
            return raw[:1500], [], [], 0.25
        text = str(payload.get("markdown_text") or payload.get("text") or "").strip()[:1800]
        items = [
            VisualTextItem(
                type=str(x.get("type") or "other")[:40],
                name=str(x.get("name") or "")[:120],
                value=str(x.get("value") or "")[:400],
                note=str(x.get("note") or "")[:180],
            )
            for x in (payload.get("structured_items") or [])
            if isinstance(x, dict)
        ]
        tags = [str(x).strip() for x in (payload.get("visual_tags") or []) if str(x).strip()]
        confidence = max(0.0, min(float(payload.get("confidence") or 0.0), 1.0))
        return text, items, tags, confidence

    async def _identify_frame(self, image_url: str, index: int, args: AnalyzeVideoFramesArgs) -> list[VisualCandidate]:
        if args.subject_type != "anime":
            return []
        rows = []
        try:
            rows = await _trace_moe_search(image_url)
        except Exception:  # noqa: BLE001
            return []
        out: list[VisualCandidate] = []
        for row in rows[:3]:
            similarity = float(row.get("similarity") or 0.0)
            if similarity < 0.72:
                continue
            title = (_anilist_titles(row) or [""])[0]
            if not title:
                continue
            start = row.get("from")
            timestamp = _format_seconds(float(start)) if isinstance(start, (int, float)) else ""
            cand = VisualCandidate(
                title=title,
                reason=f"关键帧 {index + 1} trace.moe 识番" + (f" · {timestamp}" if timestamp else ""),
                confidence=min(similarity, 0.99),
                source="trace.moe",
                image_index=index,
                image=row.get("image"),
                episode=row.get("episode"),
                timestamp=timestamp,
            )
            await _anchor_visual_candidate(self.client, cand, args.subject_type)
            out.append(cand)
        return out

    async def run(self, args: AnalyzeVideoFramesArgs) -> ToolResult[AnalyzeVideoFramesResult]:
        frame_urls = [x for x in args.frame_image_urls if str(x).strip()][: args.max_frames]
        extracted: list[dict[str, Any]] = []
        caveats = [
            "视频帧分析只处理用户提供/有权分析的视频或关键帧；不会后台下载 B站普通页面视频。",
            "抽帧 OCR/识番是采样结果，可能漏掉未采样时间点的信息。",
        ]
        if frame_urls:
            extracted = [{"index": idx, "timestamp": "", "image_url": url} for idx, url in enumerate(frame_urls)]
        elif args.video_url or args.local_video_path:
            try:
                extracted = await _extract_video_frames(
                    video_url=args.video_url,
                    local_video_path=args.local_video_path,
                    max_frames=args.max_frames,
                    sample_interval_seconds=args.sample_interval_seconds,
                )
            except Exception as e:  # noqa: BLE001
                return ToolResult(ok=False, error=f"抽帧失败：{type(e).__name__}: {e}")
        else:
            return ToolResult(ok=False, error="需要 video_url/local_video_path 或 frame_image_urls")

        frames: list[VideoFrameEvidence] = []
        all_candidates: list[VisualCandidate] = []
        for frame in extracted:
            idx = int(frame.get("index") or 0)
            image_url = str(frame.get("image_url") or "")
            ocr_text = ""
            items: list[VisualTextItem] = []
            tags: list[str] = []
            confidence = 0.0
            if args.purpose in {"ocr", "both"}:
                ocr_text, items, tags, confidence = await self._ocr_frame(image_url, args)
            candidates: list[VisualCandidate] = []
            if args.purpose in {"identify", "both"}:
                candidates = await self._identify_frame(image_url, idx, args)
                all_candidates.extend(candidates)
            frames.append(
                VideoFrameEvidence(
                    index=idx,
                    timestamp=str(frame.get("timestamp") or ""),
                    ocr_text=ocr_text,
                    structured_items=items[:8],
                    candidates=candidates[:5],
                    visual_tags=tags[:10],
                    confidence=max(confidence, max([c.confidence for c in candidates], default=0.0)),
                )
            )
        merged: dict[str, VisualCandidate] = {}
        for cand in sorted(all_candidates, key=lambda x: x.confidence, reverse=True):
            key = str(cand.bangumi_id or "").strip() or cand.title.lower()
            merged.setdefault(key, cand)
        data = AnalyzeVideoFramesResult(
            frame_count=len(frames),
            purpose=args.purpose,
            frames=frames,
            merged_ocr_text="\n\n".join(f"[{f.timestamp or f.index}] {f.ocr_text}" for f in frames if f.ocr_text)[:4000],
            candidate_subjects=list(merged.values())[:8],
            caveats=caveats + ([] if settings.vlm_model else ["未配置 VLM_MODEL，OCR 分析已跳过，仅可做 trace.moe 关键帧识番。"]),
        )
        return ToolResult(
            ok=True,
            data=data,
            sources=[
                Citation(title=c.bangumi_name or c.title, url=f"https://bgm.tv/subject/{c.bangumi_id}", source="bangumi", image=c.image)
                for c in data.candidate_subjects[:5]
                if c.bangumi_id
            ],
        )


def build_multimodal_tools(client: BangumiClient) -> list[Tool]:
    return [
        ExtractVisualTextTool(client),
        VisualStyleRecommendTool(client),
        ImageSourceSearchTool(),
        RouteImageSourceTool(client),
        AnalyzeVideoFramesTool(client),
    ]
