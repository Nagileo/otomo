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
from ..notifications import validate_webhook_url
from ..obs import append_visual_feedback, traced_stream
from ..factory import build_registry
from ..quota import (
    RateLimiter,
    TokenQuotaStore,
    begin_usage_ledger,
    client_ip,
    collected_usage,
    estimate_tokens,
)
from ..session_store import SessionStore
from ..security_context import tenant_scope
from ..share import CreateShareSnapshotRequest, ShareSnapshot, ShareSnapshotStore
from ..subscriptions import (
    CreateSubscriptionRuleRequest,
    SubscriptionService,
    SubscriptionStore,
    UpdateSubscriptionRuleRequest,
)
from ..uploads import upload_store
from .. import trajectory
from ..agent.plan_execute import PlanExecuteRunner
from ..agent.react import ReActRunner
from ..tools.bangumi.client import SUBJECT_TYPE, BangumiClient
from ..tools.moegirl.client import MoegirlClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.bangumi = BangumiClient()
    app.state.moegirl = MoegirlClient()
    app.state.ltm = LongTermMemory()
    app.state.auth = AuthStore()
    app.state.session_store = SessionStore()
    app.state.share_store = ShareSnapshotStore()
    app.state.subscription_store = SubscriptionStore()
    app.state.subscription_service = SubscriptionService(
        app.state.subscription_store,
        app.state.ltm,
        app.state.auth,
    )
    app.state.subscription_task = (
        asyncio.create_task(app.state.subscription_service.run_forever())
        if settings.subscription_scheduler_enabled else None
    )
    app.state.rate_limiter = RateLimiter()
    app.state.quota_store = TokenQuotaStore()
    app.state.sessions: dict[str, AgentState] = {}  # 短期记忆：session_id -> 会话状态
    app.state.session_locks: dict[str, asyncio.Lock] = {}

    async def _session_cleanup_loop() -> None:
        while True:
            try:
                app.state.session_store.cleanup_expired()
            except Exception:  # noqa: BLE001 - 清理失败不影响服务
                pass
            try:
                upload_store.cleanup_expired()  # 上传图片 TTL，防 cache/uploads 无限膨胀
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(24 * 3600)

    app.state.session_cleanup_task = asyncio.create_task(_session_cleanup_loop())
    try:
        yield
    finally:
        app.state.session_cleanup_task.cancel()
        try:
            await app.state.session_cleanup_task
        except asyncio.CancelledError:
            pass
        if app.state.subscription_task is not None:
            await app.state.subscription_service.stop()
            app.state.subscription_task.cancel()
            try:
                await app.state.subscription_task
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


class UploadImageRequest(BaseModel):
    data_url: str
    filename: str = ""


class ActionRequest(BaseModel):
    action_id: str
    reason: str = ""


class UndoActionRequest(BaseModel):
    action_id: str | None = None


class PrepareWriteRequest(BaseModel):
    subject_id: int = Field(..., ge=1)
    subject_name: str = ""
    collection_type: int = Field(1, ge=1, le=5)
    reason: str = "前端卡片一键写回"


class PrepareDownloaderPushRequest(BaseModel):
    torrent_url: str = ""
    magnet: str = ""
    title: str = ""
    subject_id: int | None = None
    subject_name: str = ""
    category: str = ""
    save_path: str = ""
    paused: bool = False
    reason: str = "从 release 面板准备推送到下载器"


class VisualFeedbackRequest(BaseModel):
    image_uri: str = ""
    tool_name: str = "route_image_source"
    predicted_subject_id: int | None = None
    predicted_subject_name: str = ""
    predicted_title: str = ""
    source: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    signal: VisualFeedbackSignal
    corrected_subject_id: int | None = None
    corrected_subject_name: str = ""
    note: str = ""


class VisualFeedbackSearchRequest(BaseModel):
    keyword: str
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime"
    limit: int = Field(8, ge=1, le=12)


class RenameSessionRequest(BaseModel):
    title: str


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


def _auth_session_id(request: Request) -> str:
    return request.cookies.get(settings.session_cookie_name, "") or ""


def _ensure_auth_session(request: Request, response: Response):
    session = app.state.auth.get_or_create_session(_auth_session_id(request) or None)
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


def _authenticated_identity(auth_session_id: str):
    identity = app.state.auth.identity(auth_session_id)
    if not identity.authenticated:
        raise HTTPException(status_code=401, detail="需要先绑定 Bangumi 账号")
    return identity


def _quota_key(auth_session_id: str, request: Request) -> str:
    identity = app.state.auth.identity(auth_session_id)
    if identity.authenticated and identity.username:
        return f"user:{identity.username}"
    return f"anon:{auth_session_id or client_ip(request)}"


def _session_owner(auth_session_id: str) -> str:
    """会话归属键：登录用户绑 user:<username>（跨设备/换浏览器可见），匿名沿用 cookie 会话 id。"""
    identity = app.state.auth.identity(auth_session_id)
    if identity.authenticated and identity.username:
        return f"user:{identity.username}"
    return auth_session_id


def _check_chat_limits(request: Request, auth_session_id: str) -> None:
    limiter: RateLimiter = app.state.rate_limiter
    ip = client_ip(request)
    limiter.check(f"chat:ip:{ip}:minute", limit=settings.rate_limit_chat_per_minute, window_seconds=60)
    limiter.check(
        f"chat:session:{auth_session_id}:hour",
        limit=settings.rate_limit_chat_per_hour,
        window_seconds=3600,
    )
    limiter.cleanup()


def _check_share_limits(request: Request, username: str) -> None:
    limiter: RateLimiter = app.state.rate_limiter
    ip = client_ip(request)
    limiter.check(
        f"share:ip:{ip}:hour",
        limit=settings.rate_limit_share_ip_per_hour,
        window_seconds=3600,
    )
    limiter.check(
        f"share:user:{username}:hour",
        limit=settings.rate_limit_share_user_per_hour,
        window_seconds=3600,
    )
    limiter.cleanup()


def _check_subscription_limits(request: Request, username: str, *, test: bool = False) -> None:
    suffix = "test" if test else "mutation"
    limit = (
        settings.rate_limit_subscription_tests_per_hour
        if test else settings.rate_limit_subscription_mutations_per_hour
    )
    app.state.rate_limiter.check(
        f"subscription:{suffix}:{username}:{client_ip(request)}",
        limit=limit,
        window_seconds=3600,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/auth/session")
async def auth_session(
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    await token_for_session(app.state.auth, session.auth_session_id)
    payload = app.state.auth.identity(session.auth_session_id).model_dump(mode="json")
    payload.pop("auth_session_id", None)
    payload["oauth_configured"] = bool(settings.bangumi_oauth_client_id and settings.bangumi_oauth_client_secret)
    payload["dev_token_available"] = bool(settings.bangumi_token)
    payload["csrf_token"] = session.csrf_token
    return payload


def _share_url(snapshot: ShareSnapshot) -> str:
    return f"{settings.frontend_base_url.rstrip('/')}/share/{snapshot.type}/{snapshot.id}"


def _share_public(snapshot: ShareSnapshot, *, include_owner: bool = False) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json", exclude_none=True)
    payload["url"] = _share_url(snapshot)
    if not include_owner:
        payload.pop("owner_key", None)
        payload.pop("created_by", None)
    return payload


@app.post("/share/snapshots")
async def create_share_snapshot(
    req: CreateShareSnapshotRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    identity = _authenticated_identity(session.auth_session_id)
    created_by = identity.username or str(identity.user_id or "")
    _check_share_limits(request, created_by)
    owner = f"user:{created_by}"
    snapshot = app.state.share_store.create(req, owner_key=owner, created_by=created_by)
    return {"ok": True, "id": snapshot.id, "url": _share_url(snapshot), "snapshot": _share_public(snapshot, include_owner=True)}


@app.get("/share/snapshots/{share_id}")
async def get_share_snapshot(share_id: str, request: Request) -> dict[str, Any]:
    snapshot = app.state.share_store.get(share_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="分享页不存在、已过期或已撤销")
    if snapshot.visibility == "private_preview":
        auth_session_id = _auth_session_id(request)
        identity = app.state.auth.identity(auth_session_id) if auth_session_id else None
        owner = f"user:{identity.username}" if identity and identity.authenticated else ""
        if not owner or owner != snapshot.owner_key:
            # Do not disclose whether a private snapshot exists.
            raise HTTPException(status_code=404, detail="分享页不存在、已过期或已撤销")
    return {"ok": True, "snapshot": _share_public(snapshot)}


@app.get("/share/mine")
async def list_my_share_snapshots(
    request: Request,
    response: Response,
    limit: int = 50,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    identity = _authenticated_identity(session.auth_session_id)
    username = identity.username or str(identity.user_id or "")
    rows = app.state.share_store.list_mine(f"user:{username}", limit=limit)
    return {"ok": True, "snapshots": [_share_public(x, include_owner=True) for x in rows]}


@app.delete("/share/snapshots/{share_id}")
async def revoke_share_snapshot(share_id: str, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    identity = _authenticated_identity(session.auth_session_id)
    username = identity.username or str(identity.user_id or "")
    ok = app.state.share_store.revoke(share_id, f"user:{username}")
    if not ok:
        raise HTTPException(status_code=404, detail="分享页不存在或无权撤销")
    return {"ok": True, "id": share_id}


def _subscription_owner(session_id: str) -> tuple[str, str]:
    identity = _authenticated_identity(session_id)
    username = identity.username or str(identity.user_id or "")
    return f"user:{username}", username


@app.get("/subscriptions/rules")
async def list_subscription_rules(request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    owner, username = _subscription_owner(session.auth_session_id)
    rules = app.state.subscription_store.list_rules(owner)
    deliveries = app.state.subscription_store.list_deliveries(owner, limit=80)
    return {
        "ok": True,
        "username": username,
        "rules": [x.model_dump(mode="json", exclude={"owner_key"}) for x in rules],
        "deliveries": [x.model_dump(mode="json", exclude={"owner_key"}) for x in deliveries],
    }


@app.post("/subscriptions/rules")
async def create_subscription_rule(
    req: CreateSubscriptionRuleRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    owner, username = _subscription_owner(session.auth_session_id)
    _check_subscription_limits(request, username)
    if req.webhook_url:
        try:
            await validate_webhook_url(req.webhook_url, req.webhook_format)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    rule = app.state.subscription_store.create(req, owner_key=owner, username=username)
    return {"ok": True, "rule": rule.model_dump(mode="json", exclude={"owner_key"})}


@app.patch("/subscriptions/rules/{rule_id}")
async def update_subscription_rule(
    rule_id: str,
    req: UpdateSubscriptionRuleRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    owner, username = _subscription_owner(session.auth_session_id)
    _check_subscription_limits(request, username)
    existing = app.state.subscription_store.get(rule_id, owner)
    if not existing:
        raise HTTPException(status_code=404, detail="订阅不存在或无权修改")
    if req.webhook_url is not None or req.webhook_format is not None:
        final_url = req.webhook_url if req.webhook_url is not None else existing.webhook_url
        fmt = req.webhook_format or existing.webhook_format
        if final_url:
            try:
                await validate_webhook_url(final_url, fmt)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
    rule = app.state.subscription_store.update(rule_id, owner, req)
    if not rule:
        raise HTTPException(status_code=404, detail="订阅不存在或无权修改")
    return {"ok": True, "rule": rule.model_dump(mode="json", exclude={"owner_key"})}


@app.delete("/subscriptions/rules/{rule_id}")
async def delete_subscription_rule(rule_id: str, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    owner, username = _subscription_owner(session.auth_session_id)
    _check_subscription_limits(request, username)
    ok = app.state.subscription_store.delete(rule_id, owner)
    if not ok:
        raise HTTPException(status_code=404, detail="订阅不存在或无权删除")
    return {"ok": True, "id": rule_id}


@app.post("/subscriptions/rules/{rule_id}/test")
async def test_subscription_rule(rule_id: str, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    owner, username = _subscription_owner(session.auth_session_id)
    _check_subscription_limits(request, username, test=True)
    rule = app.state.subscription_store.get(rule_id, owner)
    if not rule:
        raise HTTPException(status_code=404, detail="订阅不存在或无权测试")
    record = await app.state.subscription_service.run_rule(rule, test=True)
    return {"ok": True, "delivery": record.model_dump(mode="json", exclude={"owner_key"})}


@app.get("/subscriptions/deliveries")
async def list_subscription_deliveries(
    request: Request,
    response: Response,
    rule_id: str | None = None,
    limit: int = 80,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    owner, _ = _subscription_owner(session.auth_session_id)
    rows = app.state.subscription_store.list_deliveries(owner, rule_id=rule_id, limit=limit)
    return {"ok": True, "deliveries": [x.model_dump(mode="json", exclude={"owner_key"}) for x in rows]}


@app.get("/auth/bangumi/login")
async def bangumi_login(
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
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
    session = _ensure_auth_session(request, response)
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
    if token.username:
        # 登录前的匿名会话迁给账号归属，跨设备/换浏览器仍可见
        app.state.session_store.migrate_owner(session.auth_session_id, f"user:{token.username}")
    identity = app.state.auth.identity(session.auth_session_id).model_dump(mode="json")
    identity.pop("auth_session_id", None)
    identity["csrf_token"] = session.csrf_token
    return {"ok": True, "identity": identity}


@app.get("/auth/bangumi/start")
async def bangumi_start(request: Request, response: Response, discord_code: str = "") -> RedirectResponse:
    """浏览器可直接打开的登录入口:302 跳 Bangumi 授权。Discord 绑定用——
    bot 的 /绑定 给出一次性短码，授权成功后回调里自动绑定。"""
    session = _ensure_auth_session(request, response)
    discord_user_id = ""
    if discord_code:
        discord_user_id = app.state.auth.consume_discord_link_code(discord_code) or ""
        if not discord_user_id:
            raise HTTPException(status_code=400, detail="Discord 绑定链接无效或已过期，请回 Discord 重新生成")
    try:
        url = build_authorization_url(app.state.auth, session.auth_session_id, discord_user_id or "")
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    redirect = RedirectResponse(url)
    _set_auth_cookies(redirect, session)
    return redirect


@app.get("/auth/bangumi/callback")
async def bangumi_callback(code: str = "", state: str = "") -> RedirectResponse:
    status = "ok"
    params: dict[str, str] = {}
    session_id = ""
    try:
        token = await exchange_oauth_code(app.state.auth, code, state)
        session_id = token.auth_session_id
        params["user"] = token.username
        if token.username:
            app.state.session_store.migrate_owner(session_id, f"user:{token.username}")
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
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    app.state.auth.delete_token(session.auth_session_id)
    _clear_auth_cookies(response)
    return {"ok": True}


@app.post("/uploads/image")
async def upload_image(req: UploadImageRequest, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    app.state.rate_limiter.check(
        f"upload:ip:{client_ip(request)}:minute",
        limit=settings.rate_limit_uploads_per_minute,
        window_seconds=60,
    )
    _authenticated_identity(session.auth_session_id)
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


@app.get("/sessions")
async def list_sessions(request: Request, response: Response, limit: int = 40) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    return {"ok": True, "sessions": app.state.session_store.list_sessions(_session_owner(session.auth_session_id), limit)}


@app.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    try:
        payload = app.state.session_store.load_messages(session_id, _session_owner(session.auth_session_id))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail="无权访问该会话") from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="会话不存在") from e
    return {"ok": True, **payload}


@app.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str,
    req: RenameSessionRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    try:
        payload = app.state.session_store.rename_session(session_id, _session_owner(session.auth_session_id), req.title)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail="无权修改该会话") from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="会话不存在") from e
    return {"ok": True, "session": payload}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    try:
        app.state.session_store.delete_session(session_id, _session_owner(session.auth_session_id))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail="无权删除该会话") from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="会话不存在") from e
    app.state.sessions.pop(session_id, None)
    return {"ok": True}


def _runner_from_registry(kind: str, registry):
    if kind == "plan":
        return PlanExecuteRunner(registry)
    if kind == "react":
        return ReActRunner(registry)
    return AdaptiveRunner(registry)


_MAX_SESSIONS = 500


def _session_state(app: FastAPI, session_id: str, auth_session_id: str) -> AgentState:
    """复用会话状态；内存热缓存 miss 时从 SQLite 惰性恢复。"""
    app.state.session_store.ensure_session(session_id, auth_session_id)
    sessions: dict[str, AgentState] = app.state.sessions
    state = sessions.get(session_id)
    if state is None:
        state = app.state.session_store.load_state(session_id, auth_session_id) or AgentState()
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
        identity = app.state.auth.identity(auth_session_id or "")
        with tenant_scope(identity.username, authenticated=identity.authenticated):
            result = await registry.dispatch(
                tool_name, json.dumps(payload, ensure_ascii=False), allow_write=allow_write
            )
        return result.model_dump(mode="json", exclude_none=True)
    finally:
        await client.aclose()


@app.post("/actions/confirm")
async def confirm_action(req: ActionRequest, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    return await _dispatch_action(
        app,
        "execute_bangumi_write_action",
        {"action_id": req.action_id, "confirmed": True},
        allow_write=True,
        auth_session_id=session.auth_session_id,
    )


@app.post("/actions/cancel")
async def cancel_action(req: ActionRequest, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    return await _dispatch_action(
        app,
        "cancel_bangumi_write_action",
        {"action_id": req.action_id, "reason": req.reason},
        allow_write=False,
        auth_session_id=session.auth_session_id,
    )


@app.post("/actions/undo")
async def undo_action(req: UndoActionRequest, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    return await _dispatch_action(
        app,
        "undo_bangumi_write_action",
        {"action_id": req.action_id, "confirmed": True},
        allow_write=True,
        auth_session_id=session.auth_session_id,
    )


@app.post("/actions/prepare-write")
async def prepare_write_action(req: PrepareWriteRequest, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    _authenticated_identity(session.auth_session_id)
    return await _dispatch_action(
        app,
        "prepare_bangumi_write_action",
        {
            "operation": "set_collection",
            "subject_id": req.subject_id,
            "subject_name": req.subject_name,
            "collection_type": req.collection_type,
            "reason": req.reason,
        },
        allow_write=False,
        auth_session_id=session.auth_session_id,
    )


@app.post("/actions/prepare-downloader-push")
async def prepare_downloader_push(req: PrepareDownloaderPushRequest, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    _authenticated_identity(session.auth_session_id)
    return await _dispatch_action(
        app,
        "prepare_downloader_push",
        {
            "torrent_url": req.torrent_url,
            "magnet": req.magnet,
            "title": req.title,
            "subject_id": req.subject_id,
            "subject_name": req.subject_name,
            "category": req.category,
            "save_path": req.save_path,
            "paused": req.paused,
            "reason": req.reason,
        },
        allow_write=False,
        auth_session_id=session.auth_session_id,
    )


class AnswerFeedbackRequest(BaseModel):
    session_id: str
    turn_id: str
    rating: Literal["up", "down", "clear"]
    note: str = ""


@app.post("/feedback/answer")
async def answer_feedback(req: AnswerFeedbackRequest, request: Request, response: Response) -> dict[str, Any]:
    """答案级反馈；clear 是撤销事件，导出时同 turn 只取最后一条。"""
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    record = trajectory.record_feedback(
        turn_id=req.turn_id[:64],
        session_id=req.session_id[:64],
        owner=_session_owner(session.auth_session_id),
        rating=req.rating,
        note=req.note,
    )
    return {"ok": True, "feedback": record}


@app.post("/feedback/visual")
async def visual_feedback(req: VisualFeedbackRequest, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
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
            tool_name=req.tool_name[:80] or "route_image_source",
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
        with tenant_scope(username, authenticated=True):
            mem = app.state.ltm.load_user(username)
            mem.visual_feedback.append(item)
            mem.visual_feedback = mem.visual_feedback[-200:]
            app.state.ltm.save_user(mem)
        append_visual_feedback({
            "username": username,
            "feedback": item.model_dump(mode="json", exclude_none=True),
        })
        return {
            "ok": True,
            "feedback": item.model_dump(mode="json", exclude_none=True),
            "memory": memory_summary(mem).model_dump(mode="json", exclude_none=True),
        }
    finally:
        await client.aclose()


@app.post("/feedback/visual/search_subjects")
async def visual_feedback_search_subjects(
    req: VisualFeedbackSearchRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    keyword = req.keyword.strip()
    if not keyword:
        return {"ok": True, "subjects": []}
    client = await _request_client(app, session.auth_session_id)
    try:
        raw = await client.search_subjects(keyword, SUBJECT_TYPE[req.subject_type], limit=req.limit)
        subjects = []
        for row in (raw.get("data") or [])[: req.limit]:
            images = row.get("images") or {}
            subjects.append({
                "id": row.get("id"),
                "name": row.get("name") or "",
                "name_cn": row.get("name_cn") or "",
                "score": row.get("score") or ((row.get("rating") or {}).get("score")),
                "image": images.get("common") or images.get("medium") or images.get("grid") or "",
                "url": f"https://bgm.tv/subject/{row.get('id')}" if row.get("id") else "",
            })
        return {"ok": True, "subjects": subjects}
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
        # Long-term spoiler preference is a hint, not permission to reveal spoilers.
        # A turn enters mild/full only through explicit natural language or req.spoiler_mode.
        state.short_term["spoiler"] = {"mode": "none", "memory_default": mem.spoiler_default}


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    session = app.state.auth.get_or_create_session(_auth_session_id(request) or None)
    _require_csrf(request, session.auth_session_id)
    _check_chat_limits(request, session.auth_session_id)
    quota_key = _quota_key(session.auth_session_id, request)
    app.state.quota_store.check(quota_key)
    begin_usage_ledger()  # 本请求所有 LLM/VLM 调用的真实 token 记到这本账
    identity = app.state.auth.identity(session.auth_session_id)
    authenticated = identity.authenticated
    if req.attachments and not authenticated:
        raise HTTPException(status_code=401, detail="多模态上传需要先绑定 Bangumi 账号")
    client = await _request_client(app, session.auth_session_id)
    registry = build_registry(client, app.state.moegirl, app.state.ltm)
    runner = _runner_from_registry(req.runner, registry)
    chat_session_id = req.session_id or uuid.uuid4().hex
    session_owner = _session_owner(session.auth_session_id)
    try:
        app.state.session_store.ensure_session(chat_session_id, session_owner)
    except PermissionError as e:
        await client.aclose()
        raise HTTPException(status_code=403, detail="无权访问该会话") from e
    if not authenticated and settings.anonymous_session_turn_limit > 0:
        message_count = app.state.session_store.message_count(chat_session_id, session_owner)
        if message_count >= settings.anonymous_session_turn_limit * 2:
            await client.aclose()
            raise HTTPException(
                status_code=403,
                detail=f"未登录会话最多 {settings.anonymous_session_turn_limit} 轮；请绑定 Bangumi 账号后继续",
            )
    turn_has_attachments = bool(req.attachments)
    stored_attachments = [
        {
            **item,
            "preview_url": f"/uploads/{str(item.get('uri', '')).removeprefix('upload://')}/preview"
            if str(item.get("uri", "")).startswith("upload://") else "",
        }
        for item in (req.attachments or [])[:4]
        if isinstance(item, dict)
    ]
    turn_id = uuid.uuid4().hex  # 轨迹/反馈关联键，meta 事件发给前端
    lock_key = f"{session_owner}:{chat_session_id}"
    lock = app.state.session_locks.setdefault(lock_key, asyncio.Lock())

    async def event_gen() -> AsyncIterator[dict]:
        meta = {
            "session_id": chat_session_id,
            "runner": req.runner,
            "turn_id": turn_id,
        }
        final_answer = ""
        evidence: dict[str, list[dict[str, Any]]] = {}
        sources: list[dict[str, Any]] = []
        tools_called: list[str] = []
        state: AgentState | None = None
        try:
            async with lock:
                # The complete read-modify-stream-save transaction is under
                # one per-conversation lock. Loading state before acquiring it
                # loses turns when two browser requests overlap.
                state = _session_state(app, chat_session_id, session_owner)
                if req.spoiler_mode or req.progress_episode is not None:
                    current = dict(state.short_term.get("spoiler") or {})
                    if req.spoiler_mode:
                        current["mode"] = req.spoiler_mode
                    if req.progress_episode is not None:
                        current["progress_episode"] = req.progress_episode
                    state.short_term["spoiler"] = current
                if turn_has_attachments:
                    cleaned: list[dict[str, Any]] = []
                    for item in (req.attachments or [])[:4]:
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
                with tenant_scope(identity.username, authenticated=authenticated):
                    await _attach_memory_state(app, state, client)
                app.state.session_store.append_message(
                    chat_session_id,
                    session_owner,
                    role="user",
                    content=req.message,
                    attachments=stored_attachments,
                )
                yield {"event": "meta", "data": json.dumps({"type": "meta", **meta}, ensure_ascii=False)}
                try:
                    with tenant_scope(identity.username, authenticated=authenticated):
                        async for ev in traced_stream(runner, req.message, state, meta):
                            if ev.type == "tool_call":
                                tools_called.append(ev.name)
                            if ev.type == "observation" and getattr(ev, "data", None):
                                evidence.setdefault(ev.name, []).append(ev.data)
                            elif ev.type == "claim_check":
                                evidence.setdefault("claim_check", []).append(ev.model_dump(mode="json", exclude_none=True))
                            elif ev.type == "final":
                                final_answer = ev.answer
                                sources = [s.model_dump(mode="json", exclude_none=True) for s in ev.sources]
                            yield {"event": ev.type, "data": ev.model_dump_json()}
                finally:
                    if turn_has_attachments:
                        state.short_term.pop("attachments", None)
                    if final_answer:
                        app.state.session_store.append_message(
                            chat_session_id,
                            session_owner,
                            role="assistant",
                            content=final_answer,
                            evidence=evidence,
                            sources=sources,
                        )
                    tokens = 0
                    try:
                        tokens = collected_usage() or estimate_tokens(req.message, final_answer)
                        app.state.quota_store.record(quota_key, tokens)
                    except Exception:  # noqa: BLE001 - quota failure must not hide the answer
                        pass
                    trajectory.log_turn(
                        turn_id=turn_id,
                        session_id=chat_session_id,
                        owner=session_owner,
                        runner=req.runner or "adaptive",
                        user_message=req.message,
                        final_answer=final_answer,
                        messages=state.messages,
                        tools_called=tools_called,
                        usage_tokens=tokens,
                    )
                    app.state.session_store.save_state(chat_session_id, session_owner, state)
        finally:
            await client.aclose()

    response = EventSourceResponse(event_gen())
    _set_auth_cookies(response, session)
    return response
