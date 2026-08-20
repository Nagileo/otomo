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
from ..tools.videos.tool import verify_bilibili_account

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


class BilibiliCookieImportRequest(BaseModel):
    cookies_text: str = Field(min_length=32, max_length=512 * 1024)


def _bilibili_cookie_path() -> Path:
    return Path(settings.bilibili_cookies_file).resolve()


def _validate_bilibili_cookie_text(value: str) -> None:
    if "Netscape HTTP Cookie File" not in value[:512]:
        raise HTTPException(status_code=422, detail="需要浏览器插件导出的 Netscape cookies.txt")
    valid_rows = []
    for line in value.splitlines():
        if not line:
            continue
        # Netscape uses this comment-looking prefix for HttpOnly cookies;
        # SESSDATA is commonly exported in exactly this form.
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and parts[0].lstrip(".").lower().endswith("bilibili.com"):
            valid_rows.append(parts)
    if not valid_rows:
        raise HTTPException(status_code=422, detail="文件中没有 bilibili.com Cookie")
    if not any(parts[5] == "SESSDATA" for parts in valid_rows):
        raise HTTPException(status_code=422, detail="没有找到 SESSDATA；这不是可用的 B站登录态导出")


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
        "integrations": {
            "bilibili": verify_bilibili_account(),
        },
    }


@router.get("/integrations/bilibili")
async def bilibili_integration_status(request: Request) -> dict[str, Any]:
    _admin(request)
    return {"ok": True, "integration": verify_bilibili_account()}


@router.post("/integrations/bilibili")
async def import_bilibili_cookies(payload: BilibiliCookieImportRequest, request: Request) -> dict[str, Any]:
    _admin(request)
    _csrf(request)
    _validate_bilibili_cookie_text(payload.cookies_text)
    target = _bilibili_cookie_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(payload.cookies_text.replace("\r\n", "\n"), encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(target)
    return {"ok": True, "integration": verify_bilibili_account()}


@router.delete("/integrations/bilibili")
async def clear_bilibili_cookies(request: Request) -> dict[str, Any]:
    _admin(request)
    _csrf(request)
    target = _bilibili_cookie_path()
    if target.is_file():
        target.unlink()
    return {"ok": True, "integration": verify_bilibili_account()}


@router.get("/recommendations/batches")
async def recommendation_batches(request: Request, limit: int = 30) -> dict[str, Any]:
    _admin(request)
    return {
        "ok": True,
        "batches": request.app.state.recommendation_event_store.recent_batches(limit),
    }


@router.get("/recommendations/batches/{set_id}")
async def recommendation_batch_detail(set_id: str, request: Request) -> dict[str, Any]:
    _admin(request)
    batch = request.app.state.recommendation_event_store.diagnostic_detail(set_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="推荐批次不存在或已过期")
    return {"ok": True, "batch": batch}


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
