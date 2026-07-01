"""Run a real VLM smoke test against the configured multimodal tools.

Usage from backend/:

    python scripts/smoke_vlm.py path/to/image.png --mode screenshot
    python scripts/smoke_vlm.py path/to/frame.jpg --mode ocr --ocr-mode ppt

This script intentionally lives outside pytest: it can call paid/provider APIs
and depends on VLM_* settings in .env.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from otomo.config import settings  # noqa: E402
from otomo.tools.bangumi.client import BangumiClient  # noqa: E402
from otomo.tools.multimodal.tool import (  # noqa: E402
    ExtractVisualTextArgs,
    ExtractVisualTextTool,
    RouteImageSourceArgs,
    RouteImageSourceTool,
    VisualStyleRecommendArgs,
    VisualStyleRecommendTool,
)


def _image_ref(value: str) -> str:
    if value.startswith(("http://", "https://", "data:", "upload://")):
        return value
    path = Path(value)
    if not path.exists():
        raise SystemExit(f"image not found: {value}")
    mime, _ = mimetypes.guess_type(path.name)
    if mime not in {"image/png", "image/jpeg", "image/webp"}:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif suffix == ".webp":
            mime = "image/webp"
        else:
            mime = "image/png"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _compact_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return {"value": value}


async def _run(args: argparse.Namespace) -> int:
    if not settings.vlm_model:
        print("VLM_MODEL is empty; set VLM_BASE_URL/VLM_API_KEY/VLM_MODEL in backend/.env first.", file=sys.stderr)
        return 2
    image = _image_ref(args.image)
    print(
        json.dumps(
            {
                "provider": settings.vlm_provider or "openai-compatible",
                "base_url": settings.vlm_base_url or settings.llm_base_url,
                "model": settings.vlm_model,
                "mode": args.mode,
            },
            ensure_ascii=False,
        )
    )
    async with BangumiClient() as client:
        if args.mode == "ocr":
            tool = ExtractVisualTextTool(client)
            result = await tool.run(
                ExtractVisualTextArgs(
                    image_url=image,
                    mode=args.ocr_mode,
                    question=args.question,
                    subject_type=args.subject_type,
                    limit=args.limit,
                )
            )
        elif args.mode == "style":
            tool = VisualStyleRecommendTool(client)
            result = await tool.run(
                VisualStyleRecommendArgs(
                    image_url=image,
                    question=args.question,
                    subject_type=args.subject_type,
                    limit=args.limit,
                )
            )
        else:
            tool = RouteImageSourceTool(client)
            result = await tool.run(
                RouteImageSourceArgs(
                    image_url=image,
                    question=args.question,
                    routes=[args.route],
                    limit=args.limit,
                    use_ocr=True,
                )
            )
    payload = _compact_payload(result)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test configured VLM tools with a real image.")
    parser.add_argument("image", help="local image path, http(s) URL, data URL, or upload:// id")
    parser.add_argument("--mode", choices=["screenshot", "ocr", "style"], default="screenshot")
    parser.add_argument("--route", choices=["auto", "anime", "galgame", "comic", "novel", "fanart", "unknown"], default="auto")
    parser.add_argument("--ocr-mode", choices=["auto", "subtitle", "ranking", "magazine", "ppt", "table"], default="auto")
    parser.add_argument("--subject-type", choices=["anime", "book", "music", "game", "real"], default="anime")
    parser.add_argument("--question", default="请识别图片中的 ACGN 信息，并给出可回锚的候选。")
    parser.add_argument("--limit", type=int, default=5)
    raise SystemExit(asyncio.run(_run(parser.parse_args())))


if __name__ == "__main__":
    main()
