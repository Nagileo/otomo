"""FastAPI 应用：/health + /chat（SSE：plan / tool_call / observation / reflect / answer_delta / final）。

短期记忆：传 session_id 即可跨请求复用同一 AgentState（多轮对话/指代）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..agent.adaptive import AdaptiveRunner
from ..agent.contracts import AgentState
from ..memory import LongTermMemory
from ..memory.models import memory_summary
from ..obs import traced_stream
from ..factory import build_registry
from ..uploads import upload_store
from ..agent.plan_execute import PlanExecuteRunner
from ..agent.react import ReActRunner
from ..tools.bangumi.client import BangumiClient
from ..tools.moegirl.client import MoegirlClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.bangumi = BangumiClient()
    app.state.moegirl = MoegirlClient()
    app.state.ltm = LongTermMemory()
    app.state.registry = build_registry(app.state.bangumi, app.state.moegirl, app.state.ltm)
    app.state.runners = {
        "react": ReActRunner(app.state.registry),
        "plan": PlanExecuteRunner(app.state.registry),
        "adaptive": AdaptiveRunner(app.state.registry),
    }
    app.state.sessions: dict[str, AgentState] = {}  # 短期记忆：session_id -> 会话状态
    try:
        yield
    finally:
        await app.state.bangumi.aclose()
        await app.state.moegirl.aclose()


app = FastAPI(title="Otomo Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    runner: Literal["react", "plan", "adaptive"] = "adaptive"
    session_id: str | None = None  # 传则跨请求复用会话（短期记忆）
    spoiler_mode: Literal["none", "mild", "full"] | None = None
    progress_episode: int | None = None
    attachments: list[dict[str, Any]] = []


class UploadImageRequest(BaseModel):
    data_url: str
    filename: str = ""


class ActionRequest(BaseModel):
    action_id: str
    username: str | None = None
    reason: str = ""


class UndoActionRequest(BaseModel):
    action_id: str | None = None
    username: str | None = None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/uploads/image")
async def upload_image(req: UploadImageRequest) -> dict[str, Any]:
    try:
        image = upload_store.save_data_url(req.data_url, req.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    payload = image.model_dump(mode="json", exclude_none=True)
    # 前端预览只需要 preview_url，data_url 不回传，避免接口响应和浏览器状态过大。
    payload.pop("data_url", None)
    return payload


@app.get("/uploads/{image_id}/preview")
async def preview_image(image_id: str) -> Response:
    try:
        payload, mime_type = upload_store.read_bytes(image_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="upload not found") from e
    return Response(content=payload, media_type=mime_type)


async def _dispatch_action(app: FastAPI, tool_name: str, payload: dict[str, Any], *, allow_write: bool) -> dict[str, Any]:
    result = await app.state.registry.dispatch(
        tool_name, json.dumps(payload, ensure_ascii=False), allow_write=allow_write
    )
    return result.model_dump(mode="json", exclude_none=True)


@app.post("/actions/confirm")
async def confirm_action(req: ActionRequest) -> dict[str, Any]:
    return await _dispatch_action(
        app,
        "execute_bangumi_write_action",
        {"username": req.username, "action_id": req.action_id, "confirmed": True},
        allow_write=True,
    )


@app.post("/actions/cancel")
async def cancel_action(req: ActionRequest) -> dict[str, Any]:
    return await _dispatch_action(
        app,
        "cancel_bangumi_write_action",
        {"username": req.username, "action_id": req.action_id, "reason": req.reason},
        allow_write=False,
    )


@app.post("/actions/undo")
async def undo_action(req: UndoActionRequest) -> dict[str, Any]:
    return await _dispatch_action(
        app,
        "undo_bangumi_write_action",
        {"username": req.username, "action_id": req.action_id, "confirmed": True},
        allow_write=True,
    )


async def _attach_memory_state(app: FastAPI, state: AgentState | None) -> None:
    if state is None:
        return
    try:
        me = await app.state.bangumi.get_me()
    except Exception:  # noqa: BLE001
        return
    username = me.get("username") or str(me.get("id"))
    if not username:
        return
    mem = app.state.ltm.load_user(username)
    state.short_term["memory"] = memory_summary(mem).model_dump(mode="json", exclude_none=True)
    if mem.spoiler_default and "spoiler" not in state.short_term:
        state.short_term["spoiler"] = {"mode": mem.spoiler_default}


@app.post("/chat")
async def chat(req: ChatRequest):
    runner = app.state.runners[req.runner]
    # 短期记忆：有 session_id 就复用既有状态，否则新建
    state = None
    if req.session_id:
        state = app.state.sessions.setdefault(req.session_id, AgentState())
    elif req.spoiler_mode or req.progress_episode is not None:
        state = AgentState()
    if state is not None and (req.spoiler_mode or req.progress_episode is not None):
        current = dict(state.short_term.get("spoiler") or {})
        if req.spoiler_mode:
            current["mode"] = req.spoiler_mode
        if req.progress_episode is not None:
            current["progress_episode"] = req.progress_episode
        state.short_term["spoiler"] = current
    turn_has_attachments = state is not None and bool(req.attachments)
    if turn_has_attachments:
        cleaned: list[dict[str, Any]] = []
        for item in req.attachments[:4]:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or item.get("image_url") or "").strip()
            if not uri.startswith("upload://"):
                continue
            cleaned.append(
                {
                    "uri": uri,
                    "filename": str(item.get("filename") or "")[:160],
                    "mime_type": str(item.get("mime_type") or "image"),
                    "size": int(item.get("size") or 0),
                }
            )
        if cleaned:
            state.short_term["attachments"] = cleaned
    await _attach_memory_state(app, state)

    async def event_gen() -> AsyncIterator[dict]:
        meta = {"session_id": req.session_id or "", "runner": req.runner}
        try:
            async for ev in traced_stream(runner, req.message, state, meta):
                yield {"event": ev.type, "data": ev.model_dump_json()}
        finally:
            if turn_has_attachments and state is not None:
                state.short_term.pop("attachments", None)

    return EventSourceResponse(event_gen())
