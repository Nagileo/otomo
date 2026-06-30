"""FastAPI 应用：/health + /chat（SSE：plan / tool_call / observation / reflect / answer_delta / final）。

短期记忆：传 session_id 即可跨请求复用同一 AgentState（多轮对话/指代）。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from typing import Any, AsyncIterator, Literal
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..agent.adaptive import AdaptiveRunner
from ..agent.contracts import AgentState
from ..auth import AuthStore, BangumiToken, build_authorization_url, exchange_oauth_code, token_for_session
from ..config import settings
from ..memory import LongTermMemory
from ..memory.models import memory_summary
from ..obs import traced_stream
from ..factory import build_registry
from ..uploads import upload_store
from ..weekly import WeeklyDigestService
from ..agent.plan_execute import PlanExecuteRunner
from ..agent.react import ReActRunner
from ..tools.bangumi.client import BangumiClient
from ..tools.moegirl.client import MoegirlClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.bangumi = BangumiClient()
    app.state.moegirl = MoegirlClient()
    app.state.ltm = LongTermMemory()
    app.state.auth = AuthStore()
    app.state.weekly_service = WeeklyDigestService(app.state.ltm, app.state.auth)
    app.state.weekly_task = (
        asyncio.create_task(app.state.weekly_service.run_forever())
        if settings.weekly_scheduler_enabled else None
    )
    app.state.sessions: dict[str, AgentState] = {}  # 短期记忆：session_id -> 会话状态
    try:
        yield
    finally:
        if app.state.weekly_task is not None:
            await app.state.weekly_service.stop()
            app.state.weekly_task.cancel()
            try:
                await app.state.weekly_task
            except asyncio.CancelledError:
                pass
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
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    auth_session_id: str | None = None


class UploadImageRequest(BaseModel):
    data_url: str
    filename: str = ""


class ActionRequest(BaseModel):
    action_id: str
    username: str | None = None
    reason: str = ""
    auth_session_id: str | None = None


class UndoActionRequest(BaseModel):
    action_id: str | None = None
    username: str | None = None
    auth_session_id: str | None = None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/auth/session")
async def auth_session(auth_session_id: str) -> dict[str, Any]:
    payload = app.state.auth.identity(auth_session_id).model_dump(mode="json")
    payload["oauth_configured"] = bool(settings.bangumi_oauth_client_id and settings.bangumi_oauth_client_secret)
    payload["dev_token_available"] = bool(settings.bangumi_token)
    return payload


@app.post("/weekly/run-due")
async def weekly_run_due() -> dict[str, Any]:
    count = await app.state.weekly_service.run_due_once()
    return {"ok": True, "generated": count}


@app.get("/auth/bangumi/login")
async def bangumi_login(auth_session_id: str) -> dict[str, Any]:
    try:
        url = build_authorization_url(app.state.auth, auth_session_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"authorization_url": url}


def _is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost"}


@app.post("/auth/dev-token-login")
async def dev_token_login(req: dict[str, str], request: Request) -> dict[str, Any]:
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="本地 Token 登录仅允许 localhost 开发调试")
    auth_session_id = req.get("auth_session_id") or ""
    if not auth_session_id:
        raise HTTPException(status_code=400, detail="缺少 auth_session_id")
    if not settings.bangumi_token:
        raise HTTPException(status_code=400, detail="未配置 BANGUMI_TOKEN")
    try:
        async with BangumiClient(token=settings.bangumi_token) as bgm:
            me = await bgm.get_me()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"BANGUMI_TOKEN 验证失败：{type(e).__name__}: {str(e)[:160]}") from e
    token = BangumiToken(
        auth_session_id=auth_session_id,
        access_token=settings.bangumi_token,
        user_id=int(me["id"]) if me.get("id") is not None else None,
        username=str(me.get("username") or ""),
    )
    app.state.auth.save_token(token)
    return {"ok": True, "identity": app.state.auth.identity(auth_session_id).model_dump(mode="json")}


@app.get("/auth/bangumi/callback")
async def bangumi_callback(code: str = "", state: str = "") -> RedirectResponse:
    status = "ok"
    params: dict[str, str] = {}
    try:
        token = await exchange_oauth_code(app.state.auth, code, state)
        params["user"] = token.username
    except Exception as e:  # noqa: BLE001
        status = "error"
        params["error"] = f"{type(e).__name__}: {str(e)[:180]}"
    params["bangumi_auth"] = status
    redirect_to = f"{settings.frontend_base_url.rstrip('/')}?{urlencode(params)}"
    return RedirectResponse(redirect_to)


@app.post("/auth/logout")
async def bangumi_logout(req: dict[str, str]) -> dict[str, Any]:
    auth_session_id = req.get("auth_session_id") or ""
    if auth_session_id:
        app.state.auth.delete_token(auth_session_id)
    return {"ok": True, "auth_session_id": auth_session_id}


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


def _runner_from_registry(kind: str, registry):
    if kind == "plan":
        return PlanExecuteRunner(registry)
    if kind == "react":
        return ReActRunner(registry)
    return AdaptiveRunner(registry)


_MAX_SESSIONS = 500


def _session_state(app: FastAPI, session_id: str) -> AgentState:
    """复用会话状态；进程内会话数有上限，超出按 FIFO 丢弃最旧会话（近似 LRU），避免长跑内存无界增长。"""
    sessions: dict[str, AgentState] = app.state.sessions
    state = sessions.get(session_id)
    if state is None:
        state = AgentState()
        sessions[session_id] = state
        while len(sessions) > _MAX_SESSIONS:
            sessions.pop(next(iter(sessions)))
    return state


async def _request_client(app: FastAPI, auth_session_id: str | None) -> BangumiClient:
    token = await token_for_session(app.state.auth, auth_session_id)
    if token:
        return BangumiClient(token=token.access_token)
    if auth_session_id:
        # Browser sessions must not silently fall back to the developer's local
        # BANGUMI_TOKEN. Otherwise an unauthenticated user can appear to be
        # operating with the machine owner's account during local testing.
        return BangumiClient(token="")
    return BangumiClient()


async def _dispatch_action(
    app: FastAPI,
    tool_name: str,
    payload: dict[str, Any],
    *,
    allow_write: bool,
    auth_session_id: str | None = None,
) -> dict[str, Any]:
    client = await _request_client(app, auth_session_id)
    try:
        registry = build_registry(client, app.state.moegirl, app.state.ltm)
        result = await registry.dispatch(
            tool_name, json.dumps(payload, ensure_ascii=False), allow_write=allow_write
        )
        return result.model_dump(mode="json", exclude_none=True)
    finally:
        await client.aclose()


@app.post("/actions/confirm")
async def confirm_action(req: ActionRequest) -> dict[str, Any]:
    return await _dispatch_action(
        app,
        "execute_bangumi_write_action",
        {"username": req.username, "action_id": req.action_id, "confirmed": True},
        allow_write=True,
        auth_session_id=req.auth_session_id,
    )


@app.post("/actions/cancel")
async def cancel_action(req: ActionRequest) -> dict[str, Any]:
    return await _dispatch_action(
        app,
        "cancel_bangumi_write_action",
        {"username": req.username, "action_id": req.action_id, "reason": req.reason},
        allow_write=False,
        auth_session_id=req.auth_session_id,
    )


@app.post("/actions/undo")
async def undo_action(req: UndoActionRequest) -> dict[str, Any]:
    return await _dispatch_action(
        app,
        "undo_bangumi_write_action",
        {"username": req.username, "action_id": req.action_id, "confirmed": True},
        allow_write=True,
        auth_session_id=req.auth_session_id,
    )


async def _attach_memory_state(app: FastAPI, state: AgentState | None, client: BangumiClient) -> None:
    if state is None:
        return
    try:
        me = await client.get_me()
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
    client = await _request_client(app, req.auth_session_id)
    registry = build_registry(client, app.state.moegirl, app.state.ltm)
    runner = _runner_from_registry(req.runner, registry)
    # 短期记忆：有 session_id 就复用既有状态，否则新建
    state = None
    if req.session_id:
        state = _session_state(app, req.session_id)
    elif req.spoiler_mode or req.progress_episode is not None or req.attachments:
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
    await _attach_memory_state(app, state, client)

    async def event_gen() -> AsyncIterator[dict]:
        meta = {
            "session_id": req.session_id or "",
            "auth_session_id": req.auth_session_id or "",
            "runner": req.runner,
        }
        try:
            async for ev in traced_stream(runner, req.message, state, meta):
                yield {"event": ev.type, "data": ev.model_dump_json()}
        finally:
            if turn_has_attachments and state is not None:
                state.short_term.pop("attachments", None)
            await client.aclose()

    return EventSourceResponse(event_gen())
