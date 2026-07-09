"""Share snapshot storage and redaction.

Share pages are immutable-ish public snapshots: they should be stable after a
chat turn finishes and safe to open without authentication.  The store is
SQLite-backed so local demos survive restarts, while the schema stays close to
what a future Postgres table would use.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

from .config import settings
from .memory.consolidate import now_iso

ShareType = Literal[
    "subject_dossier",
    "watch_order",
    "monthly_report",
    "season_guide",
    "watch_cockpit",
]
ShareVisibility = Literal["public_unlisted", "private_preview", "revoked"]
ShareSpoilerLevel = Literal["none", "mild", "full"]
SharePersonalization = Literal["public_generic", "public_personalized", "private_preview"]

_SENSITIVE_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "csrf",
    "auth_session",
    "session_id",
    "api_key",
    "apikey",
    "access_key",
    "refresh",
    "webhook",
    "web_push",
    "p256dh",
    "endpoint",
    "email",
    "smtp",
    "local_path",
    "file_path",
    "save_path",
    "data_url",
)
_COMMENT_KEYS = {"comment", "private_comment", "user_comment", "raw_comment"}
_QUERY_SECRET_KEYS = {"token", "key", "api_key", "apikey", "secret", "signature", "sign", "code", "auth"}


class ShareRedaction(BaseModel):
    profile_private_fields_removed: bool = False
    token_fields_removed: bool = False
    webhook_fields_removed: bool = False
    email_fields_removed: bool = False
    comment_fields_removed: bool = False
    local_reference_removed: bool = False
    url_tokens_removed: bool = False
    removed_paths: list[str] = Field(default_factory=list)


class ShareSnapshot(BaseModel):
    id: str
    type: ShareType
    title: str
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    visibility: ShareVisibility = "public_unlisted"
    created_by: str = ""
    owner_key: str = ""
    created_at: str = ""
    updated_at: str = ""
    expires_at: str | None = None
    schema_version: int = 1
    spoiler_level: ShareSpoilerLevel = "none"
    personalized: bool = False
    personalization_mode: SharePersonalization = "public_generic"
    redaction: ShareRedaction = Field(default_factory=ShareRedaction)


class CreateShareSnapshotRequest(BaseModel):
    type: ShareType
    title: str = ""
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    visibility: ShareVisibility = "public_unlisted"
    include_personalized_reason: bool = False
    personalization_mode: SharePersonalization = "public_generic"
    expires_in_days: int | None = Field(None, ge=1, le=365)
    spoiler_level: ShareSpoilerLevel = "none"


class ShareSnapshotStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.share_store_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS share_snapshots (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    visibility TEXT NOT NULL,
                    created_by TEXT NOT NULL DEFAULT '',
                    owner_key TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    spoiler_level TEXT NOT NULL DEFAULT 'none',
                    personalized INTEGER NOT NULL DEFAULT 0,
                    personalization_mode TEXT NOT NULL DEFAULT 'public_generic',
                    redaction_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_share_owner_created ON share_snapshots(owner_key, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_share_type_created ON share_snapshots(type, created_at DESC)")

    def create(
        self,
        req: CreateShareSnapshotRequest,
        *,
        owner_key: str,
        created_by: str,
    ) -> ShareSnapshot:
        redaction = ShareRedaction()
        payload = redact_share_payload(req.payload, redaction, personalization_mode=req.personalization_mode)
        sources = redact_share_payload(req.sources, redaction, personalization_mode=req.personalization_mode)
        now = now_iso()
        expires_at = None
        if req.expires_in_days:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=req.expires_in_days)).isoformat()
        snapshot = ShareSnapshot(
            id=f"share_{secrets.token_urlsafe(18).replace('-', '').replace('_', '')[:24]}",
            type=req.type,
            title=(req.title or _infer_title(req.type, payload)).strip()[:160] or "Otomo 分享页",
            summary=req.summary.strip()[:500],
            payload=payload if isinstance(payload, dict) else {"value": payload},
            sources=sources if isinstance(sources, list) else [],
            visibility=req.visibility,
            created_by=created_by[:120],
            owner_key=owner_key[:160],
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            spoiler_level=req.spoiler_level,
            personalized=req.personalization_mode != "public_generic" or req.include_personalized_reason,
            personalization_mode=req.personalization_mode,
            redaction=redaction,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO share_snapshots(
                    id,type,title,summary,payload_json,sources_json,visibility,created_by,owner_key,
                    created_at,updated_at,expires_at,schema_version,spoiler_level,personalized,
                    personalization_mode,redaction_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot.id,
                    snapshot.type,
                    snapshot.title,
                    snapshot.summary,
                    _dump(snapshot.payload),
                    _dump(snapshot.sources),
                    snapshot.visibility,
                    snapshot.created_by,
                    snapshot.owner_key,
                    snapshot.created_at,
                    snapshot.updated_at,
                    snapshot.expires_at,
                    snapshot.schema_version,
                    snapshot.spoiler_level,
                    1 if snapshot.personalized else 0,
                    snapshot.personalization_mode,
                    _dump(snapshot.redaction.model_dump(mode="json")),
                ),
            )
        return snapshot

    def get(self, share_id: str, *, include_revoked: bool = False) -> ShareSnapshot | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM share_snapshots WHERE id=?", (share_id,)).fetchone()
        if not row:
            return None
        snapshot = _row_to_snapshot(row)
        if snapshot.visibility == "revoked" and not include_revoked:
            return None
        if snapshot.expires_at and _is_expired(snapshot.expires_at):
            return None
        return snapshot

    def list_mine(self, owner_key: str, limit: int = 50) -> list[ShareSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM share_snapshots
                WHERE owner_key=?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (owner_key, max(1, min(limit, 100))),
            ).fetchall()
        return [_row_to_snapshot(row) for row in rows]

    def revoke(self, share_id: str, owner_key: str) -> bool:
        now = now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE share_snapshots SET visibility='revoked', updated_at=? WHERE id=? AND owner_key=?",
                (now, share_id, owner_key),
            )
        return bool(cur.rowcount)


def redact_share_payload(value: Any, redaction: ShareRedaction | None = None, *, personalization_mode: str = "public_generic") -> Any:
    redaction = redaction or ShareRedaction()
    return _redact(value, redaction, path="$", personalization_mode=personalization_mode)


def _redact(value: Any, redaction: ShareRedaction, *, path: str, personalization_mode: str) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, child in value.items():
            skey = str(key)
            lower = skey.lower()
            child_path = f"{path}.{skey}"
            if _is_sensitive_key(lower):
                _mark_redaction(redaction, lower, child_path)
                out[skey] = "[redacted]"
                continue
            if personalization_mode == "public_generic" and lower in {
                "memory",
                "profile_snapshot",
                "weekly_digest_subscription",
                "aspect_profiles",
                "recent_feedback",
                "recent_decisions",
                "watch_plan",
                "recommendation_lists",
            }:
                redaction.profile_private_fields_removed = True
                redaction.removed_paths.append(child_path)
                continue
            if lower in _COMMENT_KEYS:
                redaction.comment_fields_removed = True
                redaction.removed_paths.append(child_path)
                out[skey] = "[redacted private comment]"
                continue
            out[skey] = _redact(child, redaction, path=child_path, personalization_mode=personalization_mode)
        return out
    if isinstance(value, list):
        return [_redact(item, redaction, path=f"{path}[{i}]", personalization_mode=personalization_mode) for i, item in enumerate(value)]
    if isinstance(value, str):
        return _redact_string(value, redaction, path=path)
    return value


def _is_sensitive_key(key: str) -> bool:
    return any(part in key for part in _SENSITIVE_KEY_PARTS)


def _mark_redaction(redaction: ShareRedaction, key: str, path: str) -> None:
    redaction.removed_paths.append(path)
    if "token" in key or "secret" in key or "password" in key or "api_key" in key or "auth" in key:
        redaction.token_fields_removed = True
    if "webhook" in key or "web_push" in key or "endpoint" in key:
        redaction.webhook_fields_removed = True
    if "email" in key:
        redaction.email_fields_removed = True
    if "local_path" in key or "file_path" in key or "save_path" in key or "data_url" in key:
        redaction.local_reference_removed = True


def _redact_string(value: str, redaction: ShareRedaction, *, path: str) -> str:
    raw = value.strip()
    if raw.startswith(("data:image/", "upload://", "file://")):
        redaction.local_reference_removed = True
        redaction.removed_paths.append(path)
        return "[redacted local reference]"
    if re.match(r"^[A-Za-z]:\\", raw) or raw.startswith(("\\\\", "/home/", "/Users/")):
        redaction.local_reference_removed = True
        redaction.removed_paths.append(path)
        return "[redacted local path]"
    if raw.startswith(("http://", "https://")):
        return _sanitize_url(raw, redaction, path)
    return value


def _sanitize_url(url: str, redaction: ShareRedaction, path: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    clean_pairs = []
    removed = False
    for key, val in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _QUERY_SECRET_KEYS or any(part in key.lower() for part in _QUERY_SECRET_KEYS):
            removed = True
            continue
        clean_pairs.append((key, val))
    if not removed:
        return url
    redaction.url_tokens_removed = True
    redaction.removed_paths.append(path)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(clean_pairs, doseq=True), parts.fragment))


def _infer_title(share_type: str, payload: Any) -> str:
    if isinstance(payload, dict):
        subject = payload.get("subject") or {}
        if isinstance(subject, dict) and subject.get("name"):
            suffix = {
                "subject_dossier": "作品档案",
                "watch_order": "补番路线",
                "season_guide": "新番导视",
                "monthly_report": "月度报告",
                "watch_cockpit": "追番驾驶舱",
            }.get(share_type, "分享页")
            return f"{subject.get('name')} {suffix}"
        if payload.get("title"):
            return str(payload["title"])
        if payload.get("year") and payload.get("month"):
            return f"{payload['year']}年{payload['month']}月报告"
    return f"Otomo {share_type}"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _row_to_snapshot(row: sqlite3.Row) -> ShareSnapshot:
    return ShareSnapshot(
        id=row["id"],
        type=row["type"],
        title=row["title"],
        summary=row["summary"],
        payload=_load(row["payload_json"], {}),
        sources=_load(row["sources_json"], []),
        visibility=row["visibility"],
        created_by=row["created_by"],
        owner_key=row["owner_key"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        schema_version=int(row["schema_version"] or 1),
        spoiler_level=row["spoiler_level"],
        personalized=bool(row["personalized"]),
        personalization_mode=row["personalization_mode"],
        redaction=ShareRedaction.model_validate(_load(row["redaction_json"], {})),
    )


def _is_expired(expires_at: str) -> bool:
    try:
        dt = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < datetime.now(timezone.utc)
