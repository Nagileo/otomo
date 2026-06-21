"""FastAPI 应用：/health + /chat（SSE 流式：tool_call / observation / answer_delta / final）。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..factory import build_runner
from ..tools.bangumi.client import BangumiClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.bangumi = BangumiClient()
    app.state.runner = build_runner(app.state.bangumi)
    try:
        yield
    finally:
        await app.state.bangumi.aclose()


app = FastAPI(title="Otomo Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatRequest):
    runner = app.state.runner

    async def event_gen() -> AsyncIterator[dict]:
        async for ev in runner.stream(req.message):
            yield {"event": ev.type, "data": ev.model_dump_json()}

    return EventSourceResponse(event_gen())
