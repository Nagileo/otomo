"""Authenticated, read-mostly operations dashboard and community moderation."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import __version__
from ..config import settings
from ..recsys_registry import cf_model_registry

router = APIRouter(prefix="/admin", tags=["admin"])


def _admins() -> set[str]:
    return {name.strip() for name in settings.community_admin_usernames.split(",") if name.strip()}


def _admin(request: Request):
    session_id = request.cookies.get(settings.session_cookie_name, "") or ""
    identity = request.app.state.auth.identity(session_id) if session_id else None
    if not identity or not identity.authenticated or not identity.username:
        raise HTTPException(status_code=401, detail="请先连接 Bangumi")
    if identity.username not in _admins():
        raise HTTPException(status_code=403, detail="当前账号没有管理权限")
    return identity


def _csrf(request: Request) -> None:
    if not settings.csrf_protection_enabled:
        return
    session_id = request.cookies.get(settings.session_cookie_name, "") or ""
    session = request.app.state.auth.load_session(session_id) if session_id else None
    header = request.headers.get(settings.csrf_header_name) or request.headers.get("x-csrf-token") or ""
    cookie = request.cookies.get(settings.csrf_cookie_name, "")
    if not session or not header or header != session.csrf_token or cookie != session.csrf_token:
        raise HTTPException(status_code=403, detail="CSRF 校验失败，请刷新页面")


class CommentModerationRequest(BaseModel):
    action: Literal["hide", "restore", "delete"]
    note: str = Field("", max_length=240)


class ReportResolutionRequest(BaseModel):
    status: Literal["resolved", "dismissed"]
    note: str = Field("", max_length=240)


def _storage_file(path: str) -> dict[str, Any]:
    target = Path(path)
    try:
        size = target.stat().st_size if target.is_file() else sum(
            item.stat().st_size for item in target.rglob("*") if item.is_file()
        ) if target.exists() else 0
    except OSError:
        size = 0
    return {"path": str(target), "exists": target.exists(), "bytes": size}


@router.get("/overview")
async def admin_overview(request: Request, days: int = 30) -> dict[str, Any]:
    identity = _admin(request)
    bounded_days = min(max(int(days), 1), 365)
    chat_runs, recommendation_runs = await request.app.state.chat_runs.recent(limit=40), await request.app.state.recommendation_runs.recent(limit=40)
    store_paths = {
        "long_term_memory": settings.ltm_store_path,
        "recommendation_events": settings.recommendation_event_store_path,
        "recommendation_cache": settings.recommendation_artifact_cache_path,
        "background_tasks": settings.background_run_store_path,
        "community": settings.community_store_path,
        "sessions": settings.session_store_path,
    }
    try:
        disk = shutil.disk_usage(str(Path(settings.community_store_path).resolve().parent))
        disk_payload = {"total": disk.total, "used": disk.used, "free": disk.free}
    except OSError:
        disk_payload = {"total": 0, "used": 0, "free": 0}
    return {
        "ok": True,
        "admin": identity.username,
        "system": {
            "version": __version__,
            "commit": os.getenv("OTOMO_COMMIT_SHA", "")[:40],
            "uptime_seconds": max(0, round(time.time() - request.app.state.started_at)),
            "memory_users": len(request.app.state.ltm.list_users()),
            "disk": disk_payload,
            "storage": {name: _storage_file(path) for name, path in store_paths.items()},
        },
        "community": {
            "stats": request.app.state.community_store.stats(),
            "moderation": request.app.state.community_store.moderation_overview(),
            "comments": request.app.state.community_store.list_comments(
                identity.username, 100, _admins(),
            ),
        },
        "recommendations": {
            "evaluation": request.app.state.recommendation_event_store.evaluation_report(
                None, bounded_days,
            ),
            "models": [
                status.model_dump(mode="json", exclude_none=True)
                for status in cf_model_registry.statuses()
            ],
            "artifact_cache": request.app.state.recommendation_artifact_cache.stats(),
        },
        "tasks": {
            "chat": chat_runs,
            "recommendation": recommendation_runs,
        },
    }


@router.post("/comments/{comment_id}/moderate")
async def moderate_comment(
    comment_id: str, payload: CommentModerationRequest, request: Request,
) -> dict[str, Any]:
    identity = _admin(request)
    _csrf(request)
    try:
        comment = request.app.state.community_store.moderate_comment(
            comment_id, payload.action, identity.username, payload.note,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "comment": comment}


@router.post("/reports/{report_id}/resolve")
async def resolve_report(
    report_id: str, payload: ReportResolutionRequest, request: Request,
) -> dict[str, Any]:
    identity = _admin(request)
    _csrf(request)
    try:
        report = request.app.state.community_store.resolve_report(
            report_id, payload.status, identity.username, payload.note,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "report": report}
