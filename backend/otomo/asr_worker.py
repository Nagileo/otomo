"""Internal-only faster-whisper worker for optional Bilibili transcription."""
from __future__ import annotations

import asyncio
import secrets
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import settings
from .tools.videos.tool import _sync_local_bili_asr

app = FastAPI(title="Otomo ASR Worker", version="0.1.0")


class TranscriptionRequest(BaseModel):
    url: str = Field(..., min_length=12, max_length=600)
    max_segments: int = Field(80, ge=10, le=160)


def _validate_bilibili_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    allowed = {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"}
    if parsed.scheme != "https" or host not in allowed or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="只允许 HTTPS Bilibili 视频链接")
    if host != "b23.tv" and not parsed.path.startswith("/video/"):
        raise HTTPException(status_code=400, detail="只允许 Bilibili /video/ 链接")
    return value.strip()


def _authorize(authorization: str) -> None:
    expected = settings.asr_worker_token.strip()
    if not expected:
        return
    provided = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="ASR worker token 无效")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/transcribe")
async def transcribe(
    payload: TranscriptionRequest,
    authorization: str = Header(""),
) -> dict:
    _authorize(authorization)
    source_url = _validate_bilibili_url(payload.url)
    try:
        segments = await asyncio.to_thread(
            _sync_local_bili_asr, source_url, payload.max_segments,
        )
    except Exception as exc:  # noqa: BLE001 - worker must return a bounded public error
        raise HTTPException(
            status_code=502,
            detail=f"{type(exc).__name__}: {str(exc)[:400]}",
        ) from exc
    return {
        "ok": True,
        "segments": [item.model_dump(mode="json", exclude_none=True) for item in segments],
    }
