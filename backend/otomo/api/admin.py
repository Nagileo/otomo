"""Authenticated, read-mostly operations dashboard and community moderation."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import time
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import __version__
from ..config import settings
from ..recsys_registry import cf_model_registry
from ..bilibili_account import validate_bilibili_cookie_text
from ..series_overrides import SeriesOverrideRule, builtin_series_rules
from ..tools.videos.tool import verify_bilibili_account
from ..tools.release.qbittorrent import check_qbittorrent, downloader_public_status

router = APIRouter(prefix="/admin", tags=["admin"])


def _series_rules_payload(store) -> list[dict[str, Any]]:
    builtin_ids = {rule.id for rule in builtin_series_rules()}
    operator_ids = store.operator_rule_ids()
    return [
        {
            **row.model_dump(mode="json"),
            "builtin": row.id in builtin_ids,
            "operator_override": row.id in operator_ids,
        }
        for row in store.list()
    ]


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


class BilibiliQrPollRequest(BaseModel):
    login_id: str = Field(min_length=8, max_length=96)


def _validate_bilibili_cookie_text(value: str) -> None:
    try:
        validate_bilibili_cookie_text(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _storage_file(path: str) -> dict[str, Any]:
    target = Path(path)
    try:
        size = target.stat().st_size if target.is_file() else sum(
            item.stat().st_size for item in target.rglob("*") if item.is_file()
        ) if target.exists() else 0
    except OSError:
        size = 0
    return {"path": str(target), "exists": target.exists(), "bytes": size}


async def _asr_integration_status() -> dict[str, Any]:
    provider = (settings.asr_provider or "off").strip().lower()
    result: dict[str, Any] = {
        "provider": provider,
        "configured": provider in {"local", "worker"},
        "healthy": False,
        "max_video_seconds": settings.asr_max_video_seconds,
    }
    if provider == "local":
        dependencies = {
            "yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
            "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
        }
        try:
            from ..tools._rag import _LOCAL_MODELS

            local_model = _LOCAL_MODELS / f"faster-whisper-{settings.asr_model}"
            model_local = local_model.is_dir() and any(local_model.iterdir())
        except (ImportError, OSError):
            local_model = Path("")
            model_local = False
        result.update({
            "dependencies": dependencies,
            "model": settings.asr_model,
            "model_local": model_local,
            "model_path": str(local_model) if model_local else "",
            "healthy": all(dependencies.values()) and model_local,
            "execution": "final-candidates-only",
        })
        missing = [name for name, available in dependencies.items() if not available]
        if missing:
            result["error"] = "缺少本地 ASR 依赖：" + " / ".join(missing)
        elif not model_local:
            result["error"] = f"本地模型 faster-whisper-{settings.asr_model} 不存在或为空"
        return result
    if provider != "worker":
        return result
    try:
        async with httpx.AsyncClient(timeout=min(settings.asr_worker_timeout, 5)) as client:
            response = await client.get(f"{settings.asr_worker_url.rstrip('/')}/health")
            response.raise_for_status()
        result["healthy"] = response.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError) as exc:
        result["healthy"] = False
        result["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return result


@router.get("/overview")
async def admin_overview(request: Request, days: int = 30) -> dict[str, Any]:
    identity = _admin(request)
    bounded_days = min(max(int(days), 1), 365)
    chat_runs, recommendation_runs = await request.app.state.chat_runs.recent(limit=40), await request.app.state.recommendation_runs.recent(limit=40)
    store_paths = {
        "long_term_memory": settings.ltm_store_path,
        "recommendation_events": settings.recommendation_event_store_path,
        "recommendation_cache": settings.recommendation_artifact_cache_path,
        "anime_hub_cache": settings.anime_hub_cache_path,
        "anime_hub_metrics": settings.anime_hub_metrics_path,
        "background_tasks": settings.background_run_store_path,
        "community": settings.community_store_path,
        "sessions": settings.session_store_path,
    }
    try:
        disk = shutil.disk_usage(str(Path(settings.community_store_path).resolve().parent))
        disk_payload = {"total": disk.total, "used": disk.used, "free": disk.free}
    except OSError:
        disk_payload = {"total": 0, "used": 0, "free": 0}
    asr_status = await _asr_integration_status()
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
        "anime_hub": {
            "metrics": request.app.state.anime_hub_metrics.summary(bounded_days),
            "artifact_cache": request.app.state.anime_hub_cache.stats(),
        },
        "tasks": {
            "chat": chat_runs,
            "recommendation": recommendation_runs,
        },
        "subscriptions": request.app.state.subscription_store.scheduler_status(),
        "integrations": {
            "bilibili": verify_bilibili_account(identity.username),
            "asr": asr_status,
            "qbittorrent": downloader_public_status(),
        },
        "series_overrides": {
            "status": request.app.state.series_overrides.status(),
            "rules": _series_rules_payload(request.app.state.series_overrides),
        },
    }


@router.get("/integrations/bilibili")
async def bilibili_integration_status(request: Request) -> dict[str, Any]:
    identity = _admin(request)
    return {"ok": True, "integration": verify_bilibili_account(identity.username)}


@router.post("/integrations/bilibili/qr/start")
async def start_bilibili_qr(request: Request) -> dict[str, Any]:
    identity = _admin(request)
    _csrf(request)
    try:
        login = await request.app.state.bilibili_qr.start(identity.username)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"B站扫码登录暂不可用：{exc}") from exc
    return {"ok": True, "login": login}


@router.post("/integrations/bilibili/qr/poll")
async def poll_bilibili_qr(payload: BilibiliQrPollRequest, request: Request) -> dict[str, Any]:
    identity = _admin(request)
    _csrf(request)
    try:
        login = await request.app.state.bilibili_qr.poll(identity.username, payload.login_id)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"B站扫码状态读取失败：{exc}") from exc
    result: dict[str, Any] = {"ok": True, "login": login}
    if login.get("status") == "connected":
        result["integration"] = verify_bilibili_account(identity.username)
    return result


@router.post("/integrations/bilibili")
async def import_bilibili_cookies(payload: BilibiliCookieImportRequest, request: Request) -> dict[str, Any]:
    identity = _admin(request)
    _csrf(request)
    _validate_bilibili_cookie_text(payload.cookies_text)
    request.app.state.bilibili_credentials.save(identity.username, payload.cookies_text)
    return {"ok": True, "integration": verify_bilibili_account(identity.username)}


@router.delete("/integrations/bilibili")
async def clear_bilibili_cookies(request: Request) -> dict[str, Any]:
    identity = _admin(request)
    _csrf(request)
    request.app.state.bilibili_credentials.delete(identity.username)
    return {"ok": True, "integration": verify_bilibili_account(identity.username)}


@router.post("/integrations/qbittorrent/test")
async def test_qbittorrent_integration(request: Request) -> dict[str, Any]:
    _admin(request)
    _csrf(request)
    return {"ok": True, "integration": await check_qbittorrent()}


@router.get("/series-overrides")
async def list_series_overrides(request: Request) -> dict[str, Any]:
    _admin(request)
    return {
        "ok": True,
        "rules": _series_rules_payload(request.app.state.series_overrides),
    }


@router.put("/series-overrides/{rule_id}")
async def upsert_series_override(
    rule_id: str,
    payload: SeriesOverrideRule,
    request: Request,
) -> dict[str, Any]:
    _admin(request)
    _csrf(request)
    if rule_id.strip().lower() != payload.id:
        raise HTTPException(status_code=422, detail="URL 中的规则 ID 必须与内容中的 id 一致")
    try:
        saved = request.app.state.series_overrides.upsert(payload)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "rule": saved.model_dump(mode="json")}


@router.delete("/series-overrides/{rule_id}")
async def delete_series_override(rule_id: str, request: Request) -> dict[str, Any]:
    _admin(request)
    _csrf(request)
    deleted = request.app.state.series_overrides.delete(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="人工系列规则不存在")
    return {"ok": True, "deleted": True}


@router.post("/subscriptions/{rule_id}/retry")
async def admin_retry_subscription(rule_id: str, request: Request) -> dict[str, Any]:
    _admin(request)
    _csrf(request)
    rule = request.app.state.subscription_store.get(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    if rule.consecutive_failures <= 0:
        raise HTTPException(status_code=409, detail="这条订阅当前没有待重试的失败")
    delivery = await request.app.state.subscription_service.run_rule(rule, force=True)
    return {
        "ok": True,
        "delivery": delivery.model_dump(mode="json", exclude={"owner_key"}),
    }


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
