"""Multimodal ACGN screenshot entrypoint.

The tool uses an external VLM API only when configured, then anchors candidates
back to Bangumi subjects. Identification is treated as a weak entry signal;
canonical facts still come from Bangumi tools.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from ..bangumi.client import SUBJECT_TYPE, BangumiClient


class IdentifyScreenshotArgs(BaseModel):
    image_url: str = Field(..., description="截图 URL 或 data URL；前端上传后可传 data:image/...base64")
    question: str = Field("识别这张 ACGN 截图可能来自哪部作品/哪个角色/哪一集。", description="可选关注点")
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    limit: int = Field(5, ge=1, le=10)


class VisualCandidate(BaseModel):
    title: str
    reason: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    bangumi_id: int | None = None
    bangumi_name: str = ""
    bangumi_score: float | None = None
    image: str | None = None
    match_note: str = ""


class IdentifyScreenshotResult(BaseModel):
    question: str
    raw_vlm_answer: str = ""
    candidates: list[VisualCandidate] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


_PROMPT = """你是 ACGN 截图识别助手。请根据图片识别可能的作品、角色或集数线索。
只输出 JSON：
{"candidates":[{"title":"作品名","reason":"视觉线索","confidence":0.0到1.0}],"notes":["..."]}
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


async def _call_vlm(image_url: str, question: str) -> str:
    if not settings.vlm_model:
        raise RuntimeError("未配置 VLM_MODEL；截图识别需要现成 VLM API")
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
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
    )
    return resp.choices[0].message.content or ""


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

    async def run(self, args: IdentifyScreenshotArgs) -> ToolResult[IdentifyScreenshotResult]:
        try:
            raw = await _call_vlm(args.image_url, args.question)
        except Exception as e:  # noqa: BLE001
            return ToolResult(ok=False, error=f"VLM 截图识别失败：{type(e).__name__}: {e}")
        payload = _extract_json(raw)
        candidates: list[VisualCandidate] = []
        raw_candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if isinstance(raw_candidates, list):
            for item in raw_candidates[: args.limit]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                if title:
                    candidates.append(
                        VisualCandidate(
                            title=title,
                            reason=str(item.get("reason") or "")[:180],
                            confidence=max(0.0, min(float(item.get("confidence") or 0.0), 1.0)),
                        )
                    )
        if not candidates:
            candidates = [VisualCandidate(title=t, confidence=0.35, reason="从 VLM 自然语言回答中抽取") for t in _extract_titles(raw)]

        stype = SUBJECT_TYPE.get(args.subject_type, 2)
        for cand in candidates:
            try:
                res = await self.client.search_subjects(cand.title, stype, limit=1)
            except Exception:  # noqa: BLE001
                continue
            rows = res.get("data") or []
            if not rows:
                cand.match_note = "Bangumi 未对齐"
                continue
            row = rows[0]
            cand.bangumi_id = row.get("id")
            cand.bangumi_name = row.get("name_cn") or row.get("name") or ""
            cand.bangumi_score = row.get("score") or ((row.get("rating") or {}).get("score"))
            img = row.get("images") or {}
            cand.image = img.get("common") or img.get("medium") or img.get("grid")
            cand.match_note = "已用 Bangumi search 回锚，仍需用户确认截图是否匹配。"
        return ToolResult(
            ok=True,
            data=IdentifyScreenshotResult(
                question=args.question,
                raw_vlm_answer=raw[:1200],
                candidates=candidates[: args.limit],
                caveats=[
                    "截图识别是 VLM 弱判断；作品事实、staff、评分仍必须回到 Bangumi canonical 工具。",
                    "若图片包含字幕/OCR 或局部画面，候选可能错配；高风险结论需要用户确认。",
                ],
            ),
            sources=[
                Citation(
                    title=c.bangumi_name or c.title,
                    url=f"https://bgm.tv/subject/{c.bangumi_id}" if c.bangumi_id else args.image_url,
                    source="bangumi" if c.bangumi_id else "image",
                    image=c.image,
                )
                for c in candidates[:5]
            ],
        )


def build_multimodal_tools(client: BangumiClient) -> list[Tool]:
    return [IdentifyScreenshotTool(client)]
