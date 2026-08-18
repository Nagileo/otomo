"""Privacy-light site visit aggregates and the public Otomo guestbook."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
import uuid
from typing import Any

from .config import settings

_PRODUCT_TZ = timezone(timedelta(hours=8))
_VISIT_PATHS = {
    "/", "/chat", "/community", "/discover", "/friends", "/library",
    "/me", "/memory", "/settings/subscriptions", "/share", "/share/mine", "/subject", "/today", "/workspace",
}


def _now() -> datetime:
    return datetime.now(_PRODUCT_TZ)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _clean_comment(value: str) -> str:
    lines = [re.sub(r"[\t ]+", " ", line).strip() for line in str(value or "").splitlines()]
    return "\n".join(line for line in lines if line).strip()[:500]


def _clean_report_reason(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:240]


class CommunityStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.community_store_path)
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
                CREATE TABLE IF NOT EXISTS community_visitors (
                    visitor_key TEXT PRIMARY KEY,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS community_daily_visitors (
                    day TEXT NOT NULL,
                    visitor_key TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    PRIMARY KEY(day, visitor_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS community_page_views (
                    hour_bucket TEXT NOT NULL,
                    visitor_key TEXT NOT NULL,
                    path TEXT NOT NULL,
                    viewed_at TEXT NOT NULL,
                    PRIMARY KEY(hour_bucket, visitor_key, path)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS community_page_counts (
                    day TEXT NOT NULL,
                    path TEXT NOT NULL,
                    views INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, path)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS community_comments (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    avatar_url TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(community_comments)").fetchall()
            }
            if "avatar_url" not in columns:
                conn.execute(
                    "ALTER TABLE community_comments ADD COLUMN avatar_url TEXT NOT NULL DEFAULT ''"
                )
            for name, ddl in {
                "moderation_status": "TEXT NOT NULL DEFAULT 'visible'",
                "moderated_by": "TEXT NOT NULL DEFAULT ''",
                "moderated_at": "TEXT NOT NULL DEFAULT ''",
                "moderation_note": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE community_comments ADD COLUMN {name} {ddl}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS community_comment_reports (
                    id TEXT PRIMARY KEY,
                    comment_id TEXT NOT NULL,
                    reporter TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(comment_id, reporter)
                )
                """
            )
            report_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(community_comment_reports)").fetchall()
            }
            for name, ddl in {
                "status": "TEXT NOT NULL DEFAULT 'pending'",
                "resolved_by": "TEXT NOT NULL DEFAULT ''",
                "resolved_at": "TEXT NOT NULL DEFAULT ''",
                "resolution_note": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in report_columns:
                    conn.execute(f"ALTER TABLE community_comment_reports ADD COLUMN {name} {ddl}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_community_views_time ON community_page_views(viewed_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_community_comments_time ON community_comments(created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_community_reports_comment ON community_comment_reports(comment_id)"
            )

    def record_visit(self, visitor_key: str, path: str) -> dict[str, Any]:
        now = _now()
        timestamp = _iso(now)
        day = now.date().isoformat()
        hour_bucket = now.strftime("%Y-%m-%dT%H")
        candidate_path = path.strip()[:160]
        safe_path = candidate_path if candidate_path in _VISIT_PATHS else "/"
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO community_visitors(visitor_key, first_seen, last_seen) VALUES(?,?,?)",
                (visitor_key, timestamp, timestamp),
            )
            conn.execute(
                "UPDATE community_visitors SET last_seen=? WHERE visitor_key=?",
                (timestamp, visitor_key),
            )
            conn.execute(
                "INSERT OR IGNORE INTO community_daily_visitors(day, visitor_key, first_seen) VALUES(?,?,?)",
                (day, visitor_key, timestamp),
            )
            inserted = conn.execute(
                """
                INSERT OR IGNORE INTO community_page_views(hour_bucket, visitor_key, path, viewed_at)
                VALUES(?,?,?,?)
                """,
                (hour_bucket, visitor_key, safe_path, timestamp),
            )
            if int(inserted.rowcount or 0) > 0:
                conn.execute(
                    """
                    INSERT INTO community_page_counts(day, path, views) VALUES(?,?,1)
                    ON CONFLICT(day, path) DO UPDATE SET views=views+1
                    """,
                    (day, safe_path),
                )
            # Dedup identifiers are short-lived. Long-term page metrics remain aggregate-only.
            cutoff = _iso(now - timedelta(hours=48))
            conn.execute("DELETE FROM community_page_views WHERE viewed_at<?", (cutoff,))
            daily_cutoff = (now.date() - timedelta(days=2)).isoformat()
            conn.execute("DELETE FROM community_daily_visitors WHERE day<?", (daily_cutoff,))
        return self.stats()

    def stats(self) -> dict[str, Any]:
        day = _now().date().isoformat()
        with self._connect() as conn:
            total_visitors = int(conn.execute("SELECT COUNT(*) FROM community_visitors").fetchone()[0])
            visitors_today = int(
                conn.execute(
                    "SELECT COUNT(*) FROM community_daily_visitors WHERE day=?", (day,)
                ).fetchone()[0]
            )
            total_views = int(
                conn.execute("SELECT COALESCE(SUM(views), 0) FROM community_page_counts").fetchone()[0]
            )
            views_today = int(
                conn.execute(
                    "SELECT COALESCE(SUM(views), 0) FROM community_page_counts WHERE day=?", (day,)
                ).fetchone()[0]
            )
            comment_count = int(conn.execute(
                "SELECT COUNT(*) FROM community_comments WHERE moderation_status='visible'"
            ).fetchone()[0])
            tracking_since = conn.execute(
                "SELECT MIN(first_seen) FROM community_visitors"
            ).fetchone()[0]
            popular = conn.execute(
                """
                SELECT path, SUM(views) AS views
                FROM community_page_counts
                GROUP BY path ORDER BY views DESC, path LIMIT 6
                """
            ).fetchall()
        return {
            "total_visitors": total_visitors,
            "visitors_today": visitors_today,
            "total_views": total_views,
            "views_today": views_today,
            "comment_count": comment_count,
            "tracking_since": str(tracking_since or ""),
            "popular_pages": [dict(row) for row in popular],
            "privacy": (
                "不保存原始 IP。累计访客按随机浏览器会话的不可逆哈希长期去重；"
                "逐页去重明细最多保留 48 小时，长期页面数据仅保留聚合计数。"
            ),
        }

    def list_comments(
        self,
        viewer: str = "",
        limit: int = 80,
        admin_usernames: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        admins = admin_usernames or set()
        is_admin = viewer in admins
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.owner, c.display_name, c.avatar_url, c.content,
                       c.created_at, c.updated_at, c.moderation_status,
                       c.moderated_by, c.moderated_at, c.moderation_note,
                       COUNT(CASE WHEN r.status='pending' THEN r.id END) AS report_count,
                       MAX(CASE WHEN r.reporter=? THEN 1 ELSE 0 END) AS viewer_reported
                FROM community_comments AS c
                LEFT JOIN community_comment_reports AS r ON r.comment_id=c.id
                WHERE c.moderation_status='visible' OR ?=1
                GROUP BY c.id, c.owner, c.display_name, c.avatar_url, c.content,
                         c.created_at, c.updated_at, c.moderation_status,
                         c.moderated_by, c.moderated_at, c.moderation_note
                ORDER BY c.created_at DESC LIMIT ?
                """,
                (viewer, int(is_admin), max(1, min(int(limit), 100))),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "display_name": str(row["display_name"]),
                "avatar_url": str(row["avatar_url"] or ""),
                "content": str(row["content"]),
                "created_at": str(row["created_at"]),
                "edited": str(row["updated_at"]) != str(row["created_at"]),
                "can_delete": bool(
                    viewer and (str(row["owner"]) == viewer or viewer in admins)
                ),
                "can_report": bool(
                    viewer
                    and str(row["owner"]) != viewer
                    and not bool(row["viewer_reported"])
                ),
                "reported": bool(row["viewer_reported"]),
                "moderation_status": str(row["moderation_status"] or "visible"),
                **(
                    {
                        "report_count": int(row["report_count"] or 0),
                        "moderated_by": str(row["moderated_by"] or ""),
                        "moderated_at": str(row["moderated_at"] or ""),
                        "moderation_note": str(row["moderation_note"] or ""),
                    }
                    if is_admin else {}
                ),
            }
            for row in rows
        ]

    def create_comment(self, owner: str, content: str, avatar_url: str = "") -> dict[str, Any]:
        clean = _clean_comment(content)
        if not clean:
            raise ValueError("留言不能为空")
        comment_id = uuid.uuid4().hex
        timestamp = _iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO community_comments(
                    id, owner, display_name, avatar_url, content, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (comment_id, owner, owner, avatar_url[:500], clean, timestamp, timestamp),
            )
        return {
            "id": comment_id,
            "display_name": owner,
            "avatar_url": avatar_url[:500],
            "content": clean,
            "created_at": timestamp,
            "edited": False,
            "can_delete": True,
        }

    def delete_comment(
        self,
        comment_id: str,
        owner: str,
        admin_usernames: set[str] | None = None,
    ) -> None:
        admins = admin_usernames or set()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner FROM community_comments WHERE id=?", (comment_id,)
            ).fetchone()
            if not row:
                raise FileNotFoundError("留言不存在")
            if str(row["owner"]) != owner and owner not in admins:
                raise PermissionError("只能删除自己的留言")
            conn.execute("DELETE FROM community_comment_reports WHERE comment_id=?", (comment_id,))
            conn.execute("DELETE FROM community_comments WHERE id=?", (comment_id,))

    def report_comment(self, comment_id: str, reporter: str, reason: str = "") -> None:
        clean_reason = _clean_report_reason(reason) or "不适当内容"
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner FROM community_comments WHERE id=?", (comment_id,)
            ).fetchone()
            if not row:
                raise FileNotFoundError("留言不存在")
            if str(row["owner"]) == reporter:
                raise ValueError("不能举报自己的留言")
            try:
                conn.execute(
                    """
                    INSERT INTO community_comment_reports(id, comment_id, reporter, reason, created_at)
                    VALUES(?,?,?,?,?)
                    """,
                    (uuid.uuid4().hex, comment_id, reporter, clean_reason, _iso()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("你已经举报过这条留言") from exc

    def moderation_overview(self, limit: int = 100) -> dict[str, Any]:
        with self._connect() as conn:
            counts = conn.execute(
                """SELECT
                     (SELECT COUNT(*) FROM community_comments WHERE moderation_status='visible') visible,
                     (SELECT COUNT(*) FROM community_comments WHERE moderation_status='hidden') hidden,
                     (SELECT COUNT(*) FROM community_comment_reports WHERE status='pending') pending_reports,
                     (SELECT COUNT(*) FROM community_comment_reports WHERE status!='pending') resolved_reports"""
            ).fetchone()
            reports = conn.execute(
                """SELECT r.id,r.comment_id,r.reporter,r.reason,r.created_at,r.status,
                          r.resolved_by,r.resolved_at,r.resolution_note,
                          c.display_name,c.content,c.moderation_status
                   FROM community_comment_reports r
                   LEFT JOIN community_comments c ON c.id=r.comment_id
                   ORDER BY CASE WHEN r.status='pending' THEN 0 ELSE 1 END,r.created_at DESC
                   LIMIT ?""",
                (max(1, min(int(limit), 300)),),
            ).fetchall()
        return {
            "counts": {key: int(counts[key] or 0) for key in counts.keys()},
            "reports": [dict(row) for row in reports],
        }

    def moderate_comment(
        self, comment_id: str, action: str, moderator: str, note: str = "",
    ) -> dict[str, Any] | None:
        if action not in {"hide", "restore", "delete"}:
            raise ValueError("未知的治理操作")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM community_comments WHERE id=?", (comment_id,),
            ).fetchone()
            if row is None:
                raise FileNotFoundError("留言不存在")
            if action == "delete":
                conn.execute("DELETE FROM community_comment_reports WHERE comment_id=?", (comment_id,))
                conn.execute("DELETE FROM community_comments WHERE id=?", (comment_id,))
                return None
            status = "hidden" if action == "hide" else "visible"
            timestamp = _iso()
            conn.execute(
                """UPDATE community_comments SET moderation_status=?,moderated_by=?,
                   moderated_at=?,moderation_note=? WHERE id=?""",
                (status, moderator, timestamp, _clean_report_reason(note), comment_id),
            )
            updated = conn.execute(
                """SELECT id,display_name,content,moderation_status,moderated_by,
                   moderated_at,moderation_note FROM community_comments WHERE id=?""",
                (comment_id,),
            ).fetchone()
        return dict(updated) if updated else None

    def resolve_report(
        self, report_id: str, status: str, moderator: str, note: str = "",
    ) -> dict[str, Any]:
        if status not in {"resolved", "dismissed"}:
            raise ValueError("举报处理状态无效")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM community_comment_reports WHERE id=?", (report_id,),
            ).fetchone()
            if row is None:
                raise FileNotFoundError("举报不存在")
            conn.execute(
                """UPDATE community_comment_reports SET status=?,resolved_by=?,
                   resolved_at=?,resolution_note=? WHERE id=?""",
                (status, moderator, _iso(), _clean_report_reason(note), report_id),
            )
            updated = conn.execute(
                "SELECT * FROM community_comment_reports WHERE id=?", (report_id,),
            ).fetchone()
        return dict(updated)
