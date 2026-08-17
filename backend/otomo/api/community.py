"""Public community overview, visit aggregation, and authenticated guestbook routes."""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import settings

router = APIRouter(prefix="/community", tags=["community"])


class VisitRequest(BaseModel):
    path: str = Field("/", max_length=160)


class CommentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=500)


class ReportRequest(BaseModel):
    reason: str = Field("", max_length=240)


def _admin_usernames() -> set[str]:
    return {
        value.strip()
        for value in settings.community_admin_usernames.split(",")
        if value.strip()
    }


def _session_id(request: Request) -> str:
    return request.cookies.get(settings.session_cookie_name, "") or ""


def _identity(request: Request):
    session_id = _session_id(request)
    identity = request.app.state.auth.identity(session_id) if session_id else None
    if not identity or not identity.authenticated or not identity.username:
        raise HTTPException(status_code=401, detail="请先连接 Bangumi 后再留言")
    return identity


def _require_csrf(request: Request) -> None:
    if not settings.csrf_protection_enabled:
        return
    session_id = _session_id(request)
    session = request.app.state.auth.load_session(session_id) if session_id else None
    header = request.headers.get(settings.csrf_header_name) or request.headers.get("x-csrf-token") or ""
    cookie = request.cookies.get(settings.csrf_cookie_name, "")
    if not session or not header or header != session.csrf_token or cookie != session.csrf_token:
        raise HTTPException(status_code=403, detail="CSRF 校验失败，请刷新页面")


def _visitor_key(session_id: str) -> str:
    return hashlib.sha256(f"otomo-community-v1:{session_id}".encode()).hexdigest()


@router.get("")
async def community_overview(request: Request, limit: int = 80) -> dict[str, Any]:
    session_id = _session_id(request)
    identity = request.app.state.auth.identity(session_id) if session_id else None
    viewer = identity.username if identity and identity.authenticated else ""
    store = request.app.state.community_store
    return {
        "ok": True,
        "stats": store.stats(),
        "comments": store.list_comments(viewer, limit, _admin_usernames()),
        "is_admin": bool(viewer and viewer in _admin_usernames()),
    }


@router.post("/visit")
async def record_community_visit(payload: VisitRequest, request: Request) -> dict[str, Any]:
    session_id = _session_id(request)
    store = request.app.state.community_store
    if not session_id:
        return {"ok": True, "stats": store.stats()}
    key = _visitor_key(session_id)
    request.app.state.rate_limiter.check(
        f"community-visit:{key}", limit=180, window_seconds=3600
    )
    return {"ok": True, "stats": store.record_visit(key, payload.path)}


@router.post("/comments")
async def create_community_comment(payload: CommentRequest, request: Request) -> dict[str, Any]:
    identity = _identity(request)
    _require_csrf(request)
    request.app.state.rate_limiter.check(
        f"community-comment:{identity.username}", limit=12, window_seconds=3600
    )
    try:
        comment = request.app.state.community_store.create_comment(
            identity.username,
            payload.content,
            identity.avatar_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "comment": comment, "stats": request.app.state.community_store.stats()}


@router.delete("/comments/{comment_id}")
async def delete_community_comment(comment_id: str, request: Request) -> dict[str, Any]:
    identity = _identity(request)
    _require_csrf(request)
    try:
        request.app.state.community_store.delete_comment(
            comment_id,
            identity.username,
            _admin_usernames(),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"ok": True, "stats": request.app.state.community_store.stats()}


@router.post("/comments/{comment_id}/reports")
async def report_community_comment(
    comment_id: str,
    payload: ReportRequest,
    request: Request,
) -> dict[str, Any]:
    identity = _identity(request)
    _require_csrf(request)
    request.app.state.rate_limiter.check(
        f"community-report:{identity.username}", limit=20, window_seconds=3600
    )
    try:
        request.app.state.community_store.report_comment(
            comment_id,
            identity.username,
            payload.reason,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
