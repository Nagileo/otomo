"""FastAPI 应用：/health + /chat（SSE：plan / tool_call / observation / reflect / answer_delta / final）。

短期记忆：传 session_id 即可跨请求复用同一 AgentState（多轮对话/指代）。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import uuid
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
from ..memory.consolidate import now_iso
from ..memory.models import VisualFeedbackItem, VisualFeedbackSignal, memory_summary
from ..obs import append_visual_feedback, traced_stream
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


def _cors_origins() -> list[str]:
    return [x.strip() for x in settings.cors_allowed_origins.split(",") if x.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    return response


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


class VisualFeedbackRequest(BaseModel):
    image_uri: str = ""
    tool_name: str = "identify_acgn_screenshot"
    predicted_subject_id: int | None = None
    predicted_subject_name: str = ""
    predicted_title: str = ""
    source: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    signal: VisualFeedbackSignal
    corrected_subject_id: int | None = None
    corrected_subject_name: str = ""
    note: str = ""
    auth_session_id: str | None = None


def _set_auth_cookies(response: Response, session) -> None:
    max_age = max(int(settings.session_ttl_seconds), 60)
    response.set_cookie(
        settings.session_cookie_name,
        session.auth_session_id,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        session.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def _auth_session_id(request: Request, explicit: str | None = None) -> str:
    return explicit or request.cookies.get(settings.session_cookie_name, "") or ""


def _ensure_auth_session(request: Request, response: Response, explicit: str | None = None):
    session = app.state.auth.get_or_create_session(_auth_session_id(request, explicit) or None)
    _set_auth_cookies(response, session)
    return session


def _require_csrf(request: Request, auth_session_id: str) -> None:
    if not settings.csrf_protection_enabled:
        return
    session = app.state.auth.load_session(auth_session_id)
    if not session:
        raise HTTPException(status_code=403, detail="会话不存在或已过期，请刷新页面")
    header_value = request.headers.get(settings.csrf_header_name) or request.headers.get("x-csrf-token") or ""
    cookie_value = request.cookies.get(settings.csrf_cookie_name, "")
    if not header_value or header_value != session.csrf_token or cookie_value != session.csrf_token:
        raise HTTPException(status_code=403, detail="CSRF 校验失败，请刷新页面")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/auth/session")
async def auth_session(
    request: Request,
    response: Response,
    auth_session_id: str | None = None,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response, auth_session_id)
    payload = app.state.auth.identity(session.auth_session_id).model_dump(mode="json")
    payload["oauth_configured"] = bool(settings.bangumi_oauth_client_id and settings.bangumi_oauth_client_secret)
    payload["dev_token_available"] = bool(settings.bangumi_token)
    payload["csrf_token"] = session.csrf_token
    return payload


@app.post("/weekly/run-due")
async def weekly_run_due(request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    count = await app.state.weekly_service.run_due_once()
    return {"ok": True, "generated": count}


@app.get("/auth/bangumi/login")
async def bangumi_login(
    request: Request,
    response: Response,
    auth_session_id: str | None = None,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response, auth_session_id)
    try:
        url = build_authorization_url(app.state.auth, session.auth_session_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"authorization_url": url}


def _is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost"}


@app.post("/auth/dev-token-login")
async def dev_token_login(req: dict[str, str], request: Request, response: Response) -> dict[str, Any]:
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="本地 Token 登录仅允许 localhost 开发调试")
    session = _ensure_auth_session(request, response, req.get("auth_session_id") or None)
    _require_csrf(request, session.auth_session_id)
    if not settings.bangumi_token:
        raise HTTPException(status_code=400, detail="未配置 BANGUMI_TOKEN")
    try:
        async with BangumiClient(token=settings.bangumi_token) as bgm:
            me = await bgm.get_me()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"BANGUMI_TOKEN 验证失败：{type(e).__name__}: {str(e)[:160]}") from e
    token = BangumiToken(
        auth_session_id=session.auth_session_id,
        access_token=settings.bangumi_token,
        user_id=int(me["id"]) if me.get("id") is not None else None,
        username=str(me.get("username") or ""),
    )
    app.state.auth.save_token(token)
    identity = app.state.auth.identity(session.auth_session_id).model_dump(mode="json")
    identity["csrf_token"] = session.csrf_token
    return {"ok": True, "identity": identity}


@app.get("/auth/bangumi/callback")
async def bangumi_callback(code: str = "", state: str = "") -> RedirectResponse:
    status = "ok"
    params: dict[str, str] = {}
    session_id = ""
    try:
        token = await exchange_oauth_code(app.state.auth, code, state)
        session_id = token.auth_session_id
        params["user"] = token.username
    except Exception as e:  # noqa: BLE001
        status = "error"
        params["error"] = f"{type(e).__name__}: {str(e)[:180]}"
    params["bangumi_auth"] = status
    redirect_to = f"{settings.frontend_base_url.rstrip('/')}?{urlencode(params)}"
    response = RedirectResponse(redirect_to)
    if session_id:
        session = app.state.auth.get_or_create_session(session_id)
        _set_auth_cookies(response, session)
    return response


@app.post("/auth/logout")
async def bangumi_logout(req: dict[str, str], request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response, req.get("auth_session_id") or None)
    _require_csrf(request, session.auth_session_id)
    app.state.auth.delete_token(session.auth_session_id)
    _clear_auth_cookies(response)
    return {"ok": True, "auth_session_id": session.auth_session_id}


@app.post("/uploads/image")
async def upload_image(req: UploadImageRequest, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
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
async def confirm_action(req: ActionRequest, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response, req.auth_session_id)
    _require_csrf(request, session.auth_session_id)
    return await _dispatch_action(
        app,
        "execute_bangumi_write_action",
        {"username": req.username, "action_id": req.action_id, "confirmed": True},
        allow_write=True,
        auth_session_id=session.auth_session_id,
    )


@app.post("/actions/cancel")
async def cancel_action(req: ActionRequest, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response, req.auth_session_id)
    _require_csrf(request, session.auth_session_id)
    return await _dispatch_action(
        app,
        "cancel_bangumi_write_action",
        {"username": req.username, "action_id": req.action_id, "reason": req.reason},
        allow_write=False,
        auth_session_id=session.auth_session_id,
    )


@app.post("/actions/undo")
async def undo_action(req: UndoActionRequest, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response, req.auth_session_id)
    _require_csrf(request, session.auth_session_id)
    return await _dispatch_action(
        app,
        "undo_bangumi_write_action",
        {"username": req.username, "action_id": req.action_id, "confirmed": True},
        allow_write=True,
        auth_session_id=session.auth_session_id,
    )


@app.post("/feedback/visual")
async def visual_feedback(req: VisualFeedbackRequest, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response, req.auth_session_id)
    _require_csrf(request, session.auth_session_id)
    client = await _request_client(app, session.auth_session_id)
    try:
        try:
            me = await client.get_me()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=401, detail="需要先绑定 Bangumi 账号再记录视觉反馈") from e
        username = str(me.get("username") or me.get("id") or "").strip()
        if not username:
            raise HTTPException(status_code=401, detail="无法识别当前 Bangumi 用户")
        item = VisualFeedbackItem(
            id=uuid.uuid4().hex,
            image_uri=req.image_uri[:500],
            tool_name=req.tool_name[:80] or "identify_acgn_screenshot",
            predicted_subject_id=req.predicted_subject_id,
            predicted_subject_name=req.predicted_subject_name[:160],
            predicted_title=req.predicted_title[:160],
            source=req.source[:80],
            confidence=req.confidence,
            signal=req.signal,
            corrected_subject_id=req.corrected_subject_id,
            corrected_subject_name=req.corrected_subject_name[:160],
            note=req.note[:500],
            ts=now_iso(),
        )
        mem = app.state.ltm.load_user(username)
        mem.visual_feedback.append(item)
        mem.visual_feedback = mem.visual_feedback[-200:]
        app.state.ltm.save_user(mem)
        append_visual_feedback({
            "username": username,
            "auth_session_id": session.auth_session_id,
            "feedback": item.model_dump(mode="json", exclude_none=True),
        })
        return {
            "ok": True,
            "feedback": item.model_dump(mode="json", exclude_none=True),
            "memory": memory_summary(mem).model_dump(mode="json", exclude_none=True),
        }
    finally:
        await client.aclose()


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
async def chat(req: ChatRequest, request: Request):
    session = app.state.auth.get_or_create_session(_auth_session_id(request, req.auth_session_id) or None)
    _require_csrf(request, session.auth_session_id)
    client = await _request_client(app, session.auth_session_id)
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
            "auth_session_id": session.auth_session_id,
            "runner": req.runner,
        }
        try:
            async for ev in traced_stream(runner, req.message, state, meta):
                yield {"event": ev.type, "data": ev.model_dump_json()}
        finally:
            if turn_has_attachments and state is not None:
                state.short_term.pop("attachments", None)
            await client.aclose()

    response = EventSourceResponse(event_gen())
    _set_auth_cookies(response, session)
    return response
