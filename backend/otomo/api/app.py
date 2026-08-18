"""FastAPI 应用：/health + /chat（SSE：plan / tool_call / observation / reflect / answer_delta / final）。

短期记忆：传 session_id 即可跨请求复用同一 AgentState（多轮对话/指代）。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Literal
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..agent.adaptive import AdaptiveRunner
from ..agent.compaction import compact_agent_state, restore_state
from ..agent.contracts import AgentState
from ..auth import (
    AuthStore,
    BangumiToken,
    build_authorization_url,
    exchange_oauth_code,
    resolve_profile_avatar,
    token_for_session,
)
from ..chat_runs import ChatRun, ChatRunHub
from ..community import CommunityStore
from ..config import settings
from ..memory import LongTermMemory
from ..memory.consolidate import now_iso
from ..memory.models import VisualFeedbackItem, VisualFeedbackSignal, memory_summary
from ..memory.runtime import attach_memory_state
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
from ..recommendation_events import (
    RecommendationEventStore,
    RecommendationFeedbackRequest,
    record_recommendation_feedback,
)
from ..recsys_registry import cf_model_registry
from ..session_realtime import SessionRealtimeHub
from ..session_store import SessionStore
from ..session_trace import step_from_event, trace_item_from_event
from ..security_context import tenant_scope
from ..share import CreateShareSnapshotRequest, ShareSnapshot, ShareSnapshotStore
from ..subscriptions import (
    CreateSubscriptionRuleRequest,
    SubscriptionService,
    SubscriptionStore,
    UpdateSubscriptionRuleRequest,
    WebPushSubscriptionRequest,
)
from ..today import TodayCockpitService, TodayPreferenceStore
from ..uploads import upload_store
from .. import trajectory
from ..agent.plan_execute import PlanExecuteRunner
from ..agent.react import ReActRunner
from ..tools.bangumi.client import SUBJECT_TYPE, BangumiClient
from ..tools.bangumi.tools import SearchSubjectsArgs, SearchSubjectsTool
from ..tools.discovery.tool import CompareSubjectsArgs, CompareSubjectsTool
from ..tools.moegirl.client import MoegirlClient
from ..tools.recommend.tool import RecommendArgs, RecommendTool
from ..tools.product_loop.tool import (
    MonthlyWatchReportArgs,
    MonthlyWatchReportTool,
    SubjectDossierArgs,
    SubjectDossierTool,
)
from ..tools.profile.tool import CollectionDashboardArgs, CollectionDashboardTool
from ..tools.season.tool import SeasonGuideBriefArgs, SeasonGuideBriefTool
from ..tools.user_analysis.tool import CompareUserTasteTool, TasteCompareArgs, _fetch_friends
from ..workspace import (
    SavedViewCreate,
    WorkspaceFriendCreate,
    WorkspaceFriendImportRequest,
    WorkspaceListCreate,
    WorkspaceListItemRequest,
    WorkspaceStore,
)
from .community import router as community_router

log = logging.getLogger("otomo.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.bangumi = BangumiClient()
    app.state.moegirl = MoegirlClient()
    app.state.ltm = LongTermMemory()
    app.state.auth = AuthStore()
    app.state.session_store = SessionStore()
    app.state.session_realtime = SessionRealtimeHub()
    app.state.chat_runs = ChatRunHub()
    app.state.share_store = ShareSnapshotStore()
    app.state.subscription_store = SubscriptionStore(cipher=app.state.auth.cipher)
    app.state.today_store = TodayPreferenceStore()
    app.state.recommendation_event_store = RecommendationEventStore()
    app.state.workspace_store = WorkspaceStore()
    app.state.community_store = CommunityStore()
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
            try:
                await app.state.chat_runs.cleanup()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(24 * 3600)

    app.state.session_cleanup_task = asyncio.create_task(_session_cleanup_loop())
    try:
        yield
    finally:
        await app.state.chat_runs.shutdown()
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
app.include_router(community_router)


def _cors_origins() -> list[str]:
    return [x.strip() for x in settings.cors_allowed_origins.split(",") if x.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Otomo-Run-Id"],
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
    device_id: str = Field("", max_length=96)


class UploadImageRequest(BaseModel):
    data_url: str
    filename: str = ""


class ActionRequest(BaseModel):
    action_id: str
    reason: str = ""


class UndoActionRequest(BaseModel):
    action_id: str | None = None


class PrepareWriteRequest(BaseModel):
    operation: Literal["set_collection", "mark_episodes_watched"] = "set_collection"
    subject_id: int = Field(..., ge=1)
    subject_name: str = ""
    collection_type: int = Field(1, ge=1, le=5)
    up_to_episode: int | None = Field(None, ge=1)
    recommendation_set_id: str | None = Field(None, min_length=8, max_length=64)
    reason: str = "前端卡片一键写回"


class TodayPreferenceRequest(BaseModel):
    hidden_this_season: bool | None = None
    pinned: bool | None = None


class RecommendationNextRequest(BaseModel):
    recommendation_set_id: str = Field(..., min_length=8, max_length=64)


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


class SessionHandoffRequest(BaseModel):
    code: str = Field(..., min_length=16, max_length=128)


class ProductCompareRequest(BaseModel):
    subject_ids: list[int] = Field(..., min_length=2, max_length=3)


class InboxReadRequest(BaseModel):
    unread: bool = False


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


def _safe_return_to(value: str) -> str:
    value = value.strip()
    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(char) < 32 for char in value)
    ):
        return ""
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return ""
    return value[:600]


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
    token = await token_for_session(app.state.auth, session.auth_session_id)
    if token is not None and not token.avatar_url:
        try:
            async with BangumiClient(token=token.access_token) as bgm:
                me = await bgm.get_me()
                token.avatar_url = await resolve_profile_avatar(bgm, me)
            if token.avatar_url:
                app.state.auth.save_token(token)
        except Exception:  # noqa: BLE001 - avatar backfill must not block the whole product
            pass
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
    if "webpush" in req.channels and not _webpush_ready():
        raise HTTPException(status_code=400, detail="启用浏览器推送前必须先配置 VAPID")
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
    if req.channels is not None and "webpush" in req.channels and not _webpush_ready():
        raise HTTPException(status_code=400, detail="启用浏览器推送前必须先配置 VAPID")
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
    return_to: str = "",
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    try:
        url = build_authorization_url(
            app.state.auth,
            session.auth_session_id,
            return_to=_safe_return_to(return_to),
        )
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
            avatar_url = await resolve_profile_avatar(bgm, me)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"BANGUMI_TOKEN 验证失败：{type(e).__name__}: {str(e)[:160]}") from e
    token = BangumiToken(
        auth_session_id=session.auth_session_id,
        access_token=settings.bangumi_token,
        user_id=int(me["id"]) if me.get("id") is not None else None,
        username=str(me.get("username") or ""),
        avatar_url=avatar_url,
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
async def bangumi_start(
    request: Request,
    response: Response,
    discord_code: str = "",
    return_to: str = "",
) -> RedirectResponse:
    """浏览器可直接打开的登录入口:302 跳 Bangumi 授权。Discord 绑定用——
    bot 的 /绑定 给出一次性短码，授权成功后回调里自动绑定。"""
    session = _ensure_auth_session(request, response)
    discord_user_id = ""
    if discord_code:
        discord_user_id = app.state.auth.consume_discord_link_code(discord_code) or ""
        if not discord_user_id:
            raise HTTPException(status_code=400, detail="Discord 绑定链接无效或已过期，请回 Discord 重新生成")
    try:
        url = build_authorization_url(
            app.state.auth,
            session.auth_session_id,
            discord_user_id or "",
            _safe_return_to(return_to),
        )
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
    return_to = _safe_return_to(app.state.auth.pop_oauth_return_to(session_id)) if session_id else ""
    target = f"{settings.frontend_base_url.rstrip('/')}{return_to}"
    separator = "&" if "?" in target else "?"
    redirect_to = f"{target}{separator}{urlencode(params)}"
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
async def list_sessions(
    request: Request,
    response: Response,
    limit: int = 40,
    device_id: str = "",
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    owner = _session_owner(session.auth_session_id)
    rows = app.state.session_store.list_sessions(owner, limit)
    rows = await app.state.session_realtime.decorate_sessions(owner, rows, device_id[:96])
    return {"ok": True, "sessions": rows, "last_active_session": rows[0] if rows else None}


@app.get("/sessions/events")
async def session_events(
    request: Request,
    response: Response,
    device_id: str = "",
) -> EventSourceResponse:
    session = _ensure_auth_session(request, response)
    owner = _session_owner(session.auth_session_id)

    async def event_gen() -> AsyncIterator[dict[str, str]]:
        async for event in app.state.session_realtime.stream(owner, device_id[:96]):
            if await request.is_disconnected():
                break
            yield {
                "event": "session",
                "data": json.dumps(event, ensure_ascii=False),
            }

    stream = EventSourceResponse(event_gen())
    _set_auth_cookies(stream, session)
    return stream


@app.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    request: Request,
    response: Response,
    device_id: str = "",
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    try:
        payload = app.state.session_store.load_messages(session_id, _session_owner(session.auth_session_id))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail="无权访问该会话") from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="会话不存在") from e
    owner = _session_owner(session.auth_session_id)
    session_rows = await app.state.session_realtime.decorate_sessions(
        owner, [payload.get("session") or {}], device_id[:96]
    )
    payload["session"] = session_rows[0] if session_rows else payload.get("session")
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
    await app.state.session_realtime.notify(
        _session_owner(session.auth_session_id),
        "session_changed",
        session_id=session_id,
        reason="renamed",
    )
    return {"ok": True, "session": payload}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    owner = _session_owner(session.auth_session_id)
    if await app.state.session_realtime.activity(owner, session_id):
        raise HTTPException(status_code=409, detail="会话正在生成，完成或停止后才能删除")
    try:
        app.state.session_store.delete_session(session_id, owner)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail="无权删除该会话") from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="会话不存在") from e
    app.state.sessions.pop(session_id, None)
    await app.state.session_realtime.notify(
        owner,
        "session_deleted",
        session_id=session_id,
    )
    return {"ok": True}


@app.post("/sessions/handoff/consume")
async def consume_session_handoff(
    req: SessionHandoffRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    identity = _authenticated_identity(session.auth_session_id)
    owner = _session_owner(session.auth_session_id)
    try:
        imported = app.state.session_store.consume_handoff(req.code, identity.username, owner)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail="该续聊链接不属于当前 Bangumi 账号") from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="续聊链接不存在、已使用或已过期") from e
    await app.state.session_realtime.notify(
        owner,
        "session_changed",
        session_id=imported["id"],
        reason="discord_import",
    )
    return {"ok": True, "session": imported}


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
    result = await _dispatch_action(
        app,
        "execute_bangumi_write_action",
        {"action_id": req.action_id, "confirmed": True},
        allow_write=True,
        auth_session_id=session.auth_session_id,
    )
    action = ((result.get("data") or {}).get("action") or {}) if result.get("ok") else {}
    set_id = str((action.get("context") or {}).get("recommendation_set_id") or "")
    if (
        set_id
        and action.get("operation") == "set_collection"
        and int((action.get("payload") or {}).get("type") or 0) == 1
        and action.get("subject_id")
    ):
        identity = _authenticated_identity(session.auth_session_id)
        try:
            _record_recommendation_feedback(identity.username, RecommendationFeedbackRequest(
                recommendation_set_id=set_id,
                subject_id=int(action["subject_id"]),
                event="wishlist",
                note="confirmed_bangumi_write",
            ))
        except PermissionError:
            log.warning("recommendation set expired before confirmed write attribution: %s", set_id)
    return result


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
            "operation": req.operation,
            "subject_id": req.subject_id,
            "subject_name": req.subject_name,
            "collection_type": req.collection_type,
            "up_to_episode": req.up_to_episode,
            "reason": req.reason,
            "context": (
                {"recommendation_set_id": req.recommendation_set_id}
                if req.recommendation_set_id else {}
            ),
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


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    session = app.state.auth.get_or_create_session(_auth_session_id(request) or None)
    _require_csrf(request, session.auth_session_id)
    _check_chat_limits(request, session.auth_session_id)
    quota_key = _quota_key(session.auth_session_id, request)
    app.state.quota_store.check(quota_key)
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
    claimed, activity = await app.state.session_realtime.claim(
        session_owner,
        chat_session_id,
        turn_id,
        req.device_id or "web-unknown",
        surface="web",
    )
    if not claimed:
        await client.aclose()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "session_busy",
                "message": "这个会话正在另一处生成，请等待完成或新建对话",
                "surface": activity.surface,
                "started_at": activity.started_at,
            },
        )

    async def execute_run(run: ChatRun) -> None:
        begin_usage_ledger()  # 后台任务内的全部 LLM/VLM 调用记到同一本账
        meta = {
            "session_id": chat_session_id,
            "runner": req.runner,
            "turn_id": turn_id,
            "run_id": run.id,
        }
        final_answer = ""
        evidence: dict[str, list[dict[str, Any]]] = {}
        sources: list[dict[str, Any]] = []
        tools_called: list[str] = []
        stored_trace: list[dict[str, Any]] = []
        stored_steps: list[str] = []
        turn_started_at = time.monotonic()
        state: AgentState | None = None
        baseline: AgentState | None = None
        cancelled = False
        try:
            async with lock:
                # The complete read-modify-stream-save transaction is under
                # one per-conversation lock. Loading state before acquiring it
                # loses turns when two browser requests overlap.
                state = _session_state(app, chat_session_id, session_owner)
                baseline = state.model_copy(deep=True)
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
                    await attach_memory_state(
                        state,
                        client,
                        app.state.ltm,
                        username=identity.username if authenticated else None,
                    )
                app.state.session_store.append_message(
                    chat_session_id,
                    session_owner,
                    role="user",
                    content=req.message,
                    attachments=stored_attachments,
                )
                await app.state.session_realtime.notify(
                    session_owner,
                    "session_changed",
                    session_id=chat_session_id,
                    reason="user_message",
                )
                await run.publish("meta", {"type": "meta", **meta})
                try:
                    with tenant_scope(identity.username, authenticated=authenticated):
                        async for ev in traced_stream(runner, req.message, state, meta):
                            trace_item = trace_item_from_event(ev)
                            if trace_item is not None:
                                stored_trace.append(trace_item)
                                stored_trace[:] = stored_trace[-200:]
                            step = step_from_event(ev)
                            if step and (not stored_steps or stored_steps[-1] != step):
                                stored_steps.append(step)
                                stored_steps[:] = stored_steps[-120:]
                            if ev.type == "tool_call":
                                tools_called.append(ev.name)
                            if ev.type == "observation" and getattr(ev, "data", None):
                                evidence.setdefault(ev.name, []).append(ev.data)
                            elif ev.type == "claim_check":
                                evidence.setdefault("claim_check", []).append(ev.model_dump(mode="json", exclude_none=True))
                            elif ev.type == "final":
                                final_answer = ev.answer
                                sources = [s.model_dump(mode="json", exclude_none=True) for s in ev.sources]
                            await run.publish(ev.type, ev.model_dump_json())
                except asyncio.CancelledError:
                    cancelled = True
                    user_cancelled = run.cancel_reason == "user"
                    cancellation_text = (
                        "本轮生成已由用户停止。"
                        if user_cancelled
                        else "服务正在重启，本轮生成已中断，请稍后重试。"
                    )
                    cancellation_step = "已停止本轮生成" if user_cancelled else "服务重启，本轮生成中断"
                    cancellation_note = {"kind": "note", "text": cancellation_step}
                    if not stored_trace or stored_trace[-1] != cancellation_note:
                        stored_trace.append(cancellation_note)
                    if not stored_steps or stored_steps[-1] != cancellation_step:
                        stored_steps.append(cancellation_step)
                    if baseline is not None and not final_answer:
                        restore_state(state, baseline)
                        final_answer = cancellation_text
                        state.messages.extend(
                            [
                                {"role": "user", "content": req.message},
                                {"role": "assistant", "content": final_answer},
                            ]
                        )
                        state.status = "done"
                    await run.publish(
                        "final",
                        {
                            "type": "final",
                            "answer": final_answer or cancellation_text,
                            "sources": [],
                        },
                    )
                    raise
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
                            trace=stored_trace,
                            steps=stored_steps,
                            turn_id=turn_id,
                            elapsed_ms=int((time.monotonic() - turn_started_at) * 1000),
                        )
                    if final_answer and not cancelled:
                        try:
                            await compact_agent_state(
                                state,
                                getattr(runner, "llm", None),
                                getattr(runner, "model", None),
                            )
                        except Exception:  # noqa: BLE001 - compaction must not lose a completed turn
                            log.exception("conversation compaction failed")
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
                    await app.state.session_realtime.notify(
                        session_owner,
                        "session_changed",
                        session_id=chat_session_id,
                        reason="turn_completed" if final_answer else "state_saved",
                    )
        finally:
            await app.state.session_realtime.release(activity)
            if not lock.locked() and app.state.session_locks.get(lock_key) is lock:
                app.state.session_locks.pop(lock_key, None)
            await client.aclose()

    try:
        run = await app.state.chat_runs.start(
            turn_id,
            session_owner,
            chat_session_id,
            req.device_id or "web-unknown",
            execute_run,
        )
    except RuntimeError as exc:
        await app.state.session_realtime.release(activity)
        await client.aclose()
        raise HTTPException(status_code=409, detail="这个会话已经有后台任务在运行") from exc

    async def event_stream(after: int = 0) -> AsyncIterator[dict[str, str]]:
        async for item in run.stream(after):
            if await request.is_disconnected():
                break
            if item is None:
                yield {
                    "event": "ping",
                    "data": json.dumps({"type": "ping", "at": time.time()}),
                }
            else:
                yield {"id": str(item.sequence), "event": item.event, "data": item.data}

    response = EventSourceResponse(event_stream())
    response.headers["X-Otomo-Run-Id"] = run.id
    _set_auth_cookies(response, session)
    return response


@app.get("/chat/runs/{run_id}")
async def get_chat_run(run_id: str, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    run = await app.state.chat_runs.get(_session_owner(session.auth_session_id), run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="后台任务不存在或已过期")
    return {
        "ok": True,
        "run": {
            "id": run.id,
            "session_id": run.session_id,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at or None,
            "error": run.error,
            "last_sequence": run.sequence,
        },
    }


@app.get("/chat/runs/{run_id}/events")
async def stream_chat_run_events(
    run_id: str,
    request: Request,
    after: int = 0,
) -> EventSourceResponse:
    session = app.state.auth.get_or_create_session(_auth_session_id(request) or None)
    run = await app.state.chat_runs.get(_session_owner(session.auth_session_id), run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="后台任务不存在或已过期")
    header_cursor = request.headers.get("last-event-id", "").strip()
    if header_cursor.isdigit():
        after = max(after, int(header_cursor))

    async def replay() -> AsyncIterator[dict[str, str]]:
        async for item in run.stream(after):
            if await request.is_disconnected():
                break
            if item is None:
                yield {
                    "event": "ping",
                    "data": json.dumps({"type": "ping", "at": time.time()}),
                }
            else:
                yield {"id": str(item.sequence), "event": item.event, "data": item.data}

    stream = EventSourceResponse(replay())
    _set_auth_cookies(stream, session)
    return stream


@app.post("/chat/runs/{run_id}/cancel")
async def cancel_chat_run(
    run_id: str,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    owner = _session_owner(session.auth_session_id)
    run = await app.state.chat_runs.get(owner, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="后台任务不存在或已过期")
    if run.terminal:
        return {"ok": True, "status": run.status}
    await app.state.chat_runs.cancel(owner, run_id)
    return {"ok": True, "status": "cancelling"}


@app.get("/today")
async def get_today(
    request: Request, response: Response,
    include_wishlist: bool = True, include_hidden: bool = True,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    app.state.rate_limiter.check(
        f"today:{_quota_key(session.auth_session_id, request)}",
        limit=settings.rate_limit_today_per_hour,
        window_seconds=3600,
    )
    identity = _authenticated_identity(session.auth_session_id)
    client = await _request_client(app, session.auth_session_id)
    try:
        with tenant_scope(identity.username, authenticated=True):
            data = await TodayCockpitService(client, app.state.today_store).build(
                identity.username,
                include_wishlist=include_wishlist,
                include_hidden=include_hidden,
            )
        return {"ok": True, "data": data.model_dump(mode="json", exclude_none=True)}
    finally:
        await client.aclose()


def _product_rate_limit(request: Request, auth_session_id: str, surface: str) -> None:
    app.state.rate_limiter.check(
        f"product:{surface}:{_quota_key(auth_session_id, request)}",
        limit=settings.rate_limit_today_per_hour,
        window_seconds=3600,
    )


@app.get("/product/season-guide")
async def product_season_guide(
    request: Request,
    response: Response,
    year: int,
    month: int,
    mode: Literal["guide", "hot"] = "guide",
    limit: int = 12,
    focus_tags: str = "",
    include_video_comments: bool = False,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _product_rate_limit(request, session.auth_session_id, "season")
    if month not in {1, 4, 7, 10}:
        raise HTTPException(status_code=422, detail="month 必须是季度首月：1、4、7 或 10")
    identity = app.state.auth.identity(session.auth_session_id)
    client = await _request_client(app, session.auth_session_id)
    try:
        args = SeasonGuideBriefArgs(
            year=year,
            month=month,
            mode=mode,
            limit=min(max(limit, 1), 20),
            username=identity.username if identity.authenticated else None,
            focus_tags=[x.strip() for x in focus_tags.split(",") if x.strip()] or None,
            include_video_comments=include_video_comments,
        )
        with tenant_scope(identity.username, authenticated=identity.authenticated):
            result = await SeasonGuideBriefTool(client).run(args)
        return result.model_dump(mode="json", exclude_none=True)
    finally:
        await client.aclose()


@app.post("/product/recommendations")
async def product_recommendations(
    req: RecommendArgs,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    _product_rate_limit(request, session.auth_session_id, "recommend")
    identity = app.state.auth.identity(session.auth_session_id)
    args = req.model_copy(update={
        "username": identity.username if identity.authenticated else None,
    })
    client = await _request_client(app, session.auth_session_id)
    try:
        with tenant_scope(identity.username, authenticated=identity.authenticated):
            result = await RecommendTool(
                client,
                app.state.ltm,
                event_store=app.state.recommendation_event_store,
            ).run(args)
        return result.model_dump(mode="json", exclude_none=True)
    finally:
        await client.aclose()


@app.get("/product/library")
async def product_library(
    request: Request,
    response: Response,
    subject_types: str = "anime,book,game,music,real",
    enrich_people: bool = True,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _product_rate_limit(request, session.auth_session_id, "library")
    identity = _authenticated_identity(session.auth_session_id)
    allowed = {"anime", "book", "game", "music", "real"}
    selected = [x.strip() for x in subject_types.split(",") if x.strip() in allowed]
    args = CollectionDashboardArgs(
        username=identity.username,
        subject_types=selected or ["anime"],
        enrich_people=enrich_people,
    )
    client = await _request_client(app, session.auth_session_id)
    try:
        with tenant_scope(identity.username, authenticated=True):
            result = await CollectionDashboardTool(client, app.state.ltm).run(args)
        return result.model_dump(mode="json", exclude_none=True)
    finally:
        await client.aclose()


@app.get("/product/monthly-report")
async def product_monthly_report(
    request: Request,
    response: Response,
    period: Literal["month", "year"] = "month",
    year: int | None = None,
    month: int | None = None,
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime",
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _product_rate_limit(request, session.auth_session_id, "report")
    identity = _authenticated_identity(session.auth_session_id)
    client = await _request_client(app, session.auth_session_id)
    try:
        args = MonthlyWatchReportArgs(
            username=identity.username,
            period=period,
            year=year,
            month=month,
            subject_type=subject_type,
        )
        with tenant_scope(identity.username, authenticated=True):
            result = await MonthlyWatchReportTool(client).run(args)
        return result.model_dump(mode="json", exclude_none=True)
    finally:
        await client.aclose()


@app.get("/product/subjects/{subject_id}")
async def product_subject_dossier(
    subject_id: int,
    request: Request,
    response: Response,
    spoiler_level: Literal["none", "mild", "full"] = "none",
    include_release: bool = True,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _product_rate_limit(request, session.auth_session_id, "subject")
    identity = app.state.auth.identity(session.auth_session_id)
    client = await _request_client(app, session.auth_session_id)
    try:
        args = SubjectDossierArgs(
            subject_id=subject_id,
            spoiler_level=spoiler_level,
            include_release=include_release,
        )
        with tenant_scope(identity.username, authenticated=identity.authenticated):
            result = await SubjectDossierTool(client).run(args)
        return result.model_dump(mode="json", exclude_none=True)
    finally:
        await client.aclose()


@app.get("/product/search")
async def product_search(
    request: Request,
    response: Response,
    q: str,
    subject_type: Literal["anime", "book", "music", "game", "real"] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Fast, structured search used by command palette and picker surfaces."""
    session = _ensure_auth_session(request, response)
    _product_rate_limit(request, session.auth_session_id, "search")
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="搜索词不能为空")
    client = await _request_client(app, session.auth_session_id)
    try:
        result = await SearchSubjectsTool(client).run(SearchSubjectsArgs(
            keyword=query, type=subject_type, limit=min(max(limit, 1), 12),
        ))
        return result.model_dump(mode="json", exclude_none=True)
    finally:
        await client.aclose()


@app.post("/product/compare")
async def product_compare(
    req: ProductCompareRequest, request: Request, response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    _product_rate_limit(request, session.auth_session_id, "compare")
    client = await _request_client(app, session.auth_session_id)
    try:
        result = await CompareSubjectsTool(client).run(CompareSubjectsArgs(
            subject_ids=list(dict.fromkeys(req.subject_ids)),
        ))
        return result.model_dump(mode="json", exclude_none=True)
    finally:
        await client.aclose()


@app.get("/product/inbox")
async def product_inbox(request: Request, response: Response, limit: int = 60) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    identity = _authenticated_identity(session.auth_session_id)
    with tenant_scope(identity.username, authenticated=True):
        mem = app.state.ltm.load_user(identity.username)
    items = list(reversed(mem.inbox[-min(max(limit, 1), 100):]))
    return {
        "ok": True,
        "data": {
            "items": [x.model_dump(mode="json", exclude_none=True) for x in items],
            "unread": sum(1 for x in mem.inbox if x.unread),
        },
    }


def _webpush_ready() -> bool:
    return bool(
        settings.webpush_enabled
        and settings.webpush_vapid_public_key
        and settings.webpush_vapid_private_key
        and settings.webpush_vapid_subject
    )


@app.get("/subscriptions/webpush/config")
async def webpush_config(request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    owner, _ = _subscription_owner(session.auth_session_id)
    devices = app.state.subscription_store.list_webpush_devices(owner)
    return {
        "ok": True,
        "enabled": _webpush_ready(),
        "public_key": settings.webpush_vapid_public_key if _webpush_ready() else "",
        "devices": [device.model_dump(mode="json", exclude={"owner_key"}) for device in devices],
    }


@app.post("/subscriptions/webpush")
async def subscribe_webpush(
    req: WebPushSubscriptionRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    owner, username = _subscription_owner(session.auth_session_id)
    _check_subscription_limits(request, username)
    if not _webpush_ready():
        raise HTTPException(status_code=503, detail="服务器尚未配置 Web Push / VAPID")
    parsed = urlparse(req.endpoint.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Push endpoint 必须是无账号信息的公网 HTTPS URL")
    try:
        await validate_webhook_url(req.endpoint, "generic")
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=f"Push endpoint 不可用：{e}") from e
    try:
        device = app.state.subscription_store.upsert_webpush(
            owner,
            req,
            user_agent=request.headers.get("user-agent", ""),
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"ok": True, "device": device.model_dump(mode="json", exclude={"owner_key"})}


@app.delete("/subscriptions/webpush/{device_id}")
async def unsubscribe_webpush(
    device_id: str,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    owner, username = _subscription_owner(session.auth_session_id)
    _check_subscription_limits(request, username)
    if not device_id.startswith("push_"):
        raise HTTPException(status_code=400, detail="无效的设备 ID")
    ok = app.state.subscription_store.delete_webpush(owner, device_id=device_id)
    if not ok:
        raise HTTPException(status_code=404, detail="浏览器订阅不存在或无权删除")
    return {"ok": True, "id": device_id}


@app.patch("/product/inbox/{item_id}")
async def update_product_inbox(
    item_id: str, req: InboxReadRequest, request: Request, response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    identity = _authenticated_identity(session.auth_session_id)
    with tenant_scope(identity.username, authenticated=True):
        mem = app.state.ltm.load_user(identity.username)
        item = next((x for x in mem.inbox if x.id == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="通知不存在")
        item.unread = req.unread
        app.state.ltm.save_user(mem)
    return {"ok": True, "data": item.model_dump(mode="json", exclude_none=True)}


@app.post("/product/inbox/read-all")
async def read_all_product_inbox(request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    identity = _authenticated_identity(session.auth_session_id)
    with tenant_scope(identity.username, authenticated=True):
        mem = app.state.ltm.load_user(identity.username)
        changed = sum(1 for x in mem.inbox if x.unread)
        for item in mem.inbox:
            item.unread = False
        app.state.ltm.save_user(mem)
    return {"ok": True, "data": {"updated": changed}}


def _workspace_owner(session_id: str) -> str:
    identity = _authenticated_identity(session_id)
    return f"user:{identity.username or identity.user_id}"


@app.get("/workspace/views")
async def list_workspace_views(request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    rows = app.state.workspace_store.list_views(_workspace_owner(session.auth_session_id))
    return {"ok": True, "data": [x.model_dump(mode="json") for x in rows]}


@app.post("/workspace/views")
async def create_workspace_view(
    req: SavedViewCreate, request: Request, response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    row = app.state.workspace_store.create_view(_workspace_owner(session.auth_session_id), req)
    return {"ok": True, "data": row.model_dump(mode="json")}


@app.delete("/workspace/views/{view_id}")
async def delete_workspace_view(view_id: str, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    if not app.state.workspace_store.delete_view(_workspace_owner(session.auth_session_id), view_id):
        raise HTTPException(status_code=404, detail="保存视图不存在")
    return {"ok": True, "id": view_id}


@app.get("/workspace/lists")
async def list_workspace_lists(request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    rows = app.state.workspace_store.list_lists(_workspace_owner(session.auth_session_id))
    return {"ok": True, "data": [x.model_dump(mode="json") for x in rows]}


@app.post("/workspace/lists")
async def create_workspace_list(
    req: WorkspaceListCreate, request: Request, response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    row = app.state.workspace_store.create_list(_workspace_owner(session.auth_session_id), req)
    return {"ok": True, "data": row.model_dump(mode="json")}


@app.delete("/workspace/lists/{list_id}")
async def delete_workspace_list(list_id: str, request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    if not app.state.workspace_store.delete_list(_workspace_owner(session.auth_session_id), list_id):
        raise HTTPException(status_code=404, detail="清单不存在")
    return {"ok": True, "id": list_id}


@app.put("/workspace/lists/{list_id}/items")
async def upsert_workspace_list_item(
    list_id: str, req: WorkspaceListItemRequest, request: Request, response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    row = app.state.workspace_store.upsert_item(_workspace_owner(session.auth_session_id), list_id, req)
    if not row:
        raise HTTPException(status_code=404, detail="清单不存在")
    return {"ok": True, "data": row.model_dump(mode="json")}


@app.delete("/workspace/lists/{list_id}/items/{subject_id}")
async def delete_workspace_list_item(
    list_id: str, subject_id: int, request: Request, response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    if not app.state.workspace_store.delete_item(
        _workspace_owner(session.auth_session_id), list_id, subject_id,
    ):
        raise HTTPException(status_code=404, detail="清单或条目不存在")
    return {"ok": True, "subject_id": subject_id}


@app.get("/workspace/friends")
async def list_workspace_friends(request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    rows = app.state.workspace_store.list_friends(_workspace_owner(session.auth_session_id))
    return {"ok": True, "data": [x.model_dump(mode="json") for x in rows]}


@app.post("/workspace/friends")
async def upsert_workspace_friend(
    req: WorkspaceFriendCreate, request: Request, response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    identity = _authenticated_identity(session.auth_session_id)
    if req.username.strip().lstrip("@").lower() == str(identity.username or "").lower():
        raise HTTPException(status_code=400, detail="不能把自己加入好友关注名单")
    client = await _request_client(app, session.auth_session_id)
    try:
        try:
            profile = await client.get_user(req.username.strip().lstrip("@"))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="没有找到这个 Bangumi 用户") from exc
            raise HTTPException(status_code=502, detail="Bangumi 用户服务暂时不可用") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="无法连接 Bangumi 用户服务") from exc
    finally:
        await client.aclose()
    canonical = str(profile.get("username") or req.username).strip()
    nickname = req.nickname or str(profile.get("nickname") or "")
    row = app.state.workspace_store.upsert_friend(
        _workspace_owner(session.auth_session_id),
        WorkspaceFriendCreate(username=canonical, nickname=nickname),
    )
    return {"ok": True, "data": row.model_dump(mode="json")}


@app.delete("/workspace/friends")
async def clear_workspace_friends(request: Request, response: Response) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    deleted = app.state.workspace_store.clear_friends(
        _workspace_owner(session.auth_session_id),
    )
    return {"ok": True, "deleted": deleted, "data": []}


@app.get("/workspace/friends/import")
async def preview_workspace_friend_import(
    request: Request, response: Response,
) -> dict[str, Any]:
    """Preview Bangumi friends without mutating the Otomo follow list."""
    session = _ensure_auth_session(request, response)
    identity = _authenticated_identity(session.auth_session_id)
    try:
        friends, source_url = await _fetch_friends(identity.username, 200)
    except Exception as exc:  # noqa: BLE001 - external HTML source may change
        raise HTTPException(status_code=502, detail=f"Bangumi 好友页读取失败：{type(exc).__name__}") from exc
    saved = {
        row.username.lower()
        for row in app.state.workspace_store.list_friends(_workspace_owner(session.auth_session_id))
    }
    return {
        "ok": True,
        "data": [
            {**friend.model_dump(mode="json"), "saved": friend.username.lower() in saved}
            for friend in friends
        ],
        "source_url": source_url,
    }


@app.post("/workspace/friends/import")
async def import_workspace_friends(
    req: WorkspaceFriendImportRequest, request: Request, response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    identity = _authenticated_identity(session.auth_session_id)
    try:
        friends, source_url = await _fetch_friends(identity.username, 200)
    except Exception as exc:  # noqa: BLE001 - external HTML source may change
        raise HTTPException(status_code=502, detail=f"Bangumi 好友页读取失败：{type(exc).__name__}") from exc
    selected = {name.strip().lstrip("@").lower() for name in req.usernames}
    matched = [friend for friend in friends if friend.username.lower() in selected]
    if not matched:
        raise HTTPException(status_code=400, detail="没有选中可导入的 Bangumi 好友")
    owner = _workspace_owner(session.auth_session_id)
    previously_saved = {row.username.lower() for row in app.state.workspace_store.list_friends(owner)}
    rows = app.state.workspace_store.import_friends(
        owner,
        [WorkspaceFriendCreate(username=x.username, nickname=x.nickname) for x in matched],
    )
    return {
        "ok": True,
        "data": [x.model_dump(mode="json") for x in rows],
        "imported": sum(1 for friend in matched if friend.username.lower() not in previously_saved),
        "selected": len(matched),
        "source_url": source_url,
    }


@app.delete("/workspace/friends/{username}")
async def delete_workspace_friend(
    username: str, request: Request, response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    if not app.state.workspace_store.delete_friend(
        _workspace_owner(session.auth_session_id), username,
    ):
        raise HTTPException(status_code=404, detail="好友不在关注名单中")
    return {"ok": True, "username": username}


@app.get("/product/friends")
async def product_friends(
    request: Request,
    response: Response,
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime",
    limit: int = 12,
) -> dict[str, Any]:
    """Account-scoped friend pulse and taste ranking for the product UI."""
    session = _ensure_auth_session(request, response)
    _product_rate_limit(request, session.auth_session_id, "friends")
    identity = _authenticated_identity(session.auth_session_id)
    friends = app.state.workspace_store.list_friends(_workspace_owner(session.auth_session_id))
    selected = friends[: min(max(limit, 1), 20)]
    if not selected:
        return {
            "ok": True,
            "data": {"friends": [], "pulse": None, "matrix": [], "caveats": []},
        }
    names = [x.username for x in selected]
    client = await _request_client(app, session.auth_session_id)
    try:
        tool = CompareUserTasteTool(client)
        common = {
            "username": identity.username,
            "subject_type": subject_type,
            "friends_limit": len(names),
            "peer_usernames": names,
        }
        # Run sequentially so the second report reuses the first report's Bangumi cache.
        pulse = await tool.run(TasteCompareArgs(**common, mode="friends_pulse"))
        matrix = await tool.run(TasteCompareArgs(**common, mode="friends_matrix"))
        caveats = list(dict.fromkeys(
            [*(pulse.data.caveats if pulse.ok and pulse.data else []),
             *(matrix.data.caveats if matrix.ok and matrix.data else [])]
        ))
        return {
            "ok": True,
            "data": {
                "friends": [x.model_dump(mode="json") for x in selected],
                "pulse": pulse.data.pulse.model_dump(mode="json", exclude_none=True)
                if pulse.ok and pulse.data and pulse.data.pulse else None,
                "matrix": [x.model_dump(mode="json", exclude_none=True) for x in matrix.data.matrix]
                if matrix.ok and matrix.data else [],
                "caveats": caveats,
            },
        }
    finally:
        await client.aclose()


def _friend_collection_item(row: dict[str, Any]) -> dict[str, Any] | None:
    subject = row.get("subject") if isinstance(row.get("subject"), dict) else {}
    subject_id = subject.get("id") or row.get("subject_id")
    if not subject_id:
        return None
    images = subject.get("images") if isinstance(subject.get("images"), dict) else {}
    return {
        "subject_id": int(subject_id),
        "name": subject.get("name_cn") or subject.get("name") or f"Subject {subject_id}",
        "image": images.get("small") or images.get("common") or "",
        "collection_type": row.get("type"),
        "rate": row.get("rate") or None,
        "ep_status": row.get("ep_status") or 0,
        "eps": subject.get("eps") or subject.get("total_episodes") or None,
        "updated_at": row.get("updated_at") or "",
    }


@app.get("/product/friends/{friend_username}")
async def product_friend_detail(
    friend_username: str,
    request: Request,
    response: Response,
    subject_type: Literal["anime", "book", "music", "game", "real"] = "anime",
) -> dict[str, Any]:
    """Return one saved friend's public collection; arbitrary users are not exposed here."""
    session = _ensure_auth_session(request, response)
    _product_rate_limit(request, session.auth_session_id, "friend-detail")
    saved = {
        row.username: row
        for row in app.state.workspace_store.list_friends(_workspace_owner(session.auth_session_id))
    }
    normalized = friend_username.strip().lstrip("@").lower()
    friend = saved.get(normalized)
    if not friend:
        raise HTTPException(status_code=404, detail="请先把该用户加入好友关注名单")

    client = await _request_client(app, session.auth_session_id)
    try:
        rows = await client.get_all_user_collections(
            normalized,
            subject_type=SUBJECT_TYPE[subject_type],
            collection_type=None,
            max_items=1000,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="没有找到该用户的公开收藏") from exc
        raise HTTPException(status_code=502, detail="Bangumi 收藏服务暂时不可用") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="无法连接 Bangumi 收藏服务") from exc
    finally:
        await client.aclose()

    items = [item for row in rows if (item := _friend_collection_item(row))]
    recent = sorted(items, key=lambda item: item["updated_at"], reverse=True)
    return {
        "ok": True,
        "data": {
            "friend": friend.model_dump(mode="json"),
            "subject_type": subject_type,
            "watching": [item for item in recent if item["collection_type"] == 3][:30],
            "wishlist": [item for item in recent if item["collection_type"] == 1][:20],
            "recent": recent[:30],
            "total_public": len(items),
        },
    }


@app.patch("/today/preferences/{subject_id}")
async def update_today_preference(
    subject_id: int, req: TodayPreferenceRequest, request: Request, response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    app.state.rate_limiter.check(
        f"today:{_quota_key(session.auth_session_id, request)}",
        limit=settings.rate_limit_today_per_hour,
        window_seconds=3600,
    )
    identity = _authenticated_identity(session.auth_session_id)
    pref = app.state.today_store.update(
        identity.username, subject_id,
        hidden_this_season=req.hidden_this_season, pinned=req.pinned,
    )
    return {"ok": True, "data": pref.model_dump(mode="json", exclude_none=True)}


@app.post("/feedback/recommendation")
async def recommendation_feedback(
    req: RecommendationFeedbackRequest, request: Request, response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    app.state.rate_limiter.check(
        f"recommendation-feedback:{_quota_key(session.auth_session_id, request)}",
        limit=settings.rate_limit_recommendation_feedback_per_hour,
        window_seconds=3600,
    )
    identity = _authenticated_identity(session.auth_session_id)
    try:
        event = _record_recommendation_feedback(identity.username, req)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "data": event}


def _record_recommendation_feedback(
    username: str, req: RecommendationFeedbackRequest,
) -> dict[str, Any]:
    return record_recommendation_feedback(
        app.state.recommendation_event_store,
        app.state.ltm,
        username,
        req,
        channel="web",
    )


@app.post("/recommendations/next")
async def next_recommendation_batch(
    req: RecommendationNextRequest, request: Request, response: Response,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    _require_csrf(request, session.auth_session_id)
    app.state.rate_limiter.check(
        f"recommendation-batch:{_quota_key(session.auth_session_id, request)}",
        limit=settings.rate_limit_recommendation_batches_per_hour,
        window_seconds=3600,
    )
    identity = _authenticated_identity(session.auth_session_id)
    previous = app.state.recommendation_event_store.get_set(req.recommendation_set_id, identity.username)
    if not previous:
        raise HTTPException(status_code=404, detail="推荐批次不存在或不属于当前用户")
    args_raw = dict(previous["request"])
    args_raw["username"] = identity.username
    args_raw["exclude_ids"] = list(dict.fromkeys(
        [int(item["id"]) for item in previous["items"]]
        + list(args_raw.get("exclude_ids") or [])
        + list(app.state.recommendation_event_store.recent_excluded_ids(identity.username))
    ))[:80]
    client = await _request_client(app, session.auth_session_id)
    try:
        with tenant_scope(identity.username, authenticated=True):
            result = await RecommendTool(
                client, app.state.ltm, event_store=app.state.recommendation_event_store,
            ).run(RecommendArgs.model_validate(args_raw))
        return result.model_dump(mode="json", exclude_none=True)
    finally:
        await client.aclose()


@app.get("/recommendations/metrics")
async def recommendation_metrics(
    request: Request, response: Response, days: int = 30,
) -> dict[str, Any]:
    session = _ensure_auth_session(request, response)
    identity = _authenticated_identity(session.auth_session_id)
    bounded_days = min(max(days, 1), 365)
    return {"ok": True, "data": app.state.recommendation_event_store.metrics(identity.username, bounded_days)}


@app.get("/recommendations/models")
async def recommendation_models() -> dict[str, Any]:
    return {
        "ok": True,
        "data": [status.model_dump(mode="json", exclude_none=True) for status in cf_model_registry.statuses()],
    }
