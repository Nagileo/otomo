"""FastAPI 应用：/health + /chat（SSE：plan / tool_call / observation / reflect / answer_delta / final）。

短期记忆：传 session_id 即可跨请求复用同一 AgentState（多轮对话/指代）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..agent.contracts import AgentState
from ..factory import build_registry
from ..agent.plan_execute import PlanExecuteRunner
from ..agent.react import ReActRunner
from ..tools.bangumi.client import BangumiClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.bangumi = BangumiClient()
    app.state.registry = build_registry(app.state.bangumi)
    app.state.runners = {
        "react": ReActRunner(app.state.registry),
        "plan": PlanExecuteRunner(app.state.registry),
    }
    app.state.sessions: dict[str, AgentState] = {}  # 短期记忆：session_id -> 会话状态
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
    runner: Literal["react", "plan"] = "react"
    session_id: str | None = None  # 传则跨请求复用会话（短期记忆）


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatRequest):
    runner = app.state.runners[req.runner]
    # 短期记忆：有 session_id 就复用既有状态，否则新建
    state = None
    if req.session_id:
        state = app.state.sessions.setdefault(req.session_id, AgentState())

    async def event_gen() -> AsyncIterator[dict]:
        async for ev in runner.stream(req.message, state):
            yield {"event": ev.type, "data": ev.model_dump_json()}

    return EventSourceResponse(event_gen())
