"""Recommendation batches, item feedback, and lightweight online metrics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config import settings
from .memory import LongTermMemory
from .memory.consolidate import now_iso
from .memory.models import FeedbackItem
from .security_context import tenant_scope

RecommendationEvent = Literal[
    "impression", "open", "wishlist", "started", "dismiss", "more", "less", "watched", "undo",
]
DismissReason = Literal[
    "not_interested", "already_seen", "genre", "visual", "pace", "length", "temporary",
]
FeedbackAspect = Literal["item", "genre", "visual", "pace", "length"]


class RecommendationFeedbackRequest(BaseModel):
    recommendation_set_id: str
    subject_id: int = Field(..., ge=1)
    event: RecommendationEvent
    reason: DismissReason | None = None
    aspect: FeedbackAspect = "item"
    note: str = Field("", max_length=500)


class RecommendationEventStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.recommendation_event_store_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS recommendation_sets (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL, subject_type TEXT NOT NULL,
                    scenario TEXT NOT NULL, request_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recommendation_items (
                    set_id TEXT NOT NULL, subject_id INTEGER NOT NULL, position INTEGER NOT NULL,
                    score REAL, payload_json TEXT NOT NULL, PRIMARY KEY(set_id, subject_id),
                    FOREIGN KEY(set_id) REFERENCES recommendation_sets(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS recommendation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, set_id TEXT NOT NULL, username TEXT NOT NULL,
                    subject_id INTEGER NOT NULL, event TEXT NOT NULL, reason TEXT, aspect TEXT NOT NULL DEFAULT 'item', note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rec_events_user_time
                    ON recommendation_events(username, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_rec_events_set
                    ON recommendation_events(set_id, subject_id, event);
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(recommendation_events)").fetchall()
            }
            if "aspect" not in columns:
                conn.execute(
                    "ALTER TABLE recommendation_events ADD COLUMN aspect TEXT NOT NULL DEFAULT 'item'"
                )

    def create_set(
        self, username: str, subject_type: str, scenario: str,
        request: dict[str, Any], items: list[dict[str, Any]],
    ) -> str:
        set_id = f"rec_{secrets.token_urlsafe(12).replace('-', '').replace('_', '')[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
            conn.execute(
                """DELETE FROM recommendation_events
                   WHERE set_id IN (SELECT id FROM recommendation_sets WHERE created_at<?)""",
                (cutoff,),
            )
            conn.execute("DELETE FROM recommendation_sets WHERE created_at<?", (cutoff,))
            conn.execute(
                "INSERT INTO recommendation_sets VALUES(?,?,?,?,?,?)",
                (set_id, username, subject_type, scenario, json.dumps(request, ensure_ascii=False), now),
            )
            conn.executemany(
                "INSERT INTO recommendation_items VALUES(?,?,?,?,?)",
                [
                    (set_id, int(item["id"]), pos, item.get("score"), json.dumps(item, ensure_ascii=False))
                    for pos, item in enumerate(items, 1)
                ],
            )
        return set_id

    def belongs_to(self, set_id: str, username: str, subject_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT 1 FROM recommendation_sets s JOIN recommendation_items i ON i.set_id=s.id
                   WHERE s.id=? AND s.username=? AND i.subject_id=?""",
                (set_id, username, subject_id),
            ).fetchone()
        return bool(row)

    def get_set(self, set_id: str, username: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM recommendation_sets WHERE id=? AND username=?",
                (set_id, username),
            ).fetchone()
            if not row:
                return None
            items = conn.execute(
                "SELECT subject_id,position,payload_json FROM recommendation_items "
                "WHERE set_id=? ORDER BY position",
                (set_id,),
            ).fetchall()
        return {
            "id": row["id"],
            "username": row["username"],
            "subject_type": row["subject_type"],
            "scenario": row["scenario"],
            "request": json.loads(row["request_json"]),
            "items": [json.loads(item["payload_json"]) for item in items],
            "created_at": row["created_at"],
        }

    def item_payload(self, set_id: str, username: str, subject_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT i.payload_json FROM recommendation_items i
                   JOIN recommendation_sets s ON s.id=i.set_id
                   WHERE s.id=? AND s.username=? AND i.subject_id=?""",
                (set_id, username, subject_id),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def record(self, username: str, req: RecommendationFeedbackRequest) -> dict[str, Any]:
        if not self.belongs_to(req.recommendation_set_id, username, req.subject_id):
            raise PermissionError("推荐批次不存在、已过期或不属于当前用户")
        now = datetime.now(timezone.utc).isoformat()
        # Browser retries and React strict mode must not duplicate durable preference signals.
        preference_events = {"wishlist", "started", "dismiss", "more", "less", "watched", "undo"}
        with self._connect() as conn:
            if req.event == "impression":
                exists = conn.execute(
                    """SELECT 1 FROM recommendation_events
                       WHERE set_id=? AND subject_id=? AND event='impression'""",
                    (req.recommendation_set_id, req.subject_id),
                ).fetchone()
                if exists:
                    return {"recorded": False, "deduplicated": True, "created_at": now}
            elif req.event in preference_events:
                latest = conn.execute(
                    """SELECT event,reason,aspect FROM recommendation_events
                       WHERE set_id=? AND username=? AND subject_id=?
                         AND event IN ('wishlist','started','dismiss','more','less','watched','undo')
                       ORDER BY id DESC LIMIT 1""",
                    (req.recommendation_set_id, username, req.subject_id),
                ).fetchone()
                if (
                    latest
                    and latest["event"] == req.event
                    and (latest["reason"] or None) == req.reason
                    and str(latest["aspect"] or "item") == req.aspect
                ):
                    return {"recorded": False, "deduplicated": True, "created_at": now}
            conn.execute(
                """INSERT INTO recommendation_events(
                       set_id,username,subject_id,event,reason,aspect,note,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    req.recommendation_set_id, username, req.subject_id, req.event,
                    req.reason, req.aspect, req.note.strip(), now,
                ),
            )
        return {"recorded": True, "deduplicated": False, "created_at": now}

    def recent_excluded_ids(self, username: str, days: int = 90) -> set[int]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days, 1))).isoformat()
        temporary_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """WITH latest AS (
                       SELECT subject_id,event,reason,created_at,
                              ROW_NUMBER() OVER (PARTITION BY subject_id ORDER BY id DESC) AS rn
                       FROM recommendation_events
                       WHERE username=?
                         AND event IN ('wishlist','started','dismiss','more','less','watched','undo')
                   )
                   SELECT subject_id FROM latest WHERE rn=1 AND (
                       (created_at>=? AND event IN ('dismiss','less')
                        AND COALESCE(reason,'')!='temporary')
                       OR (created_at>=? AND event='dismiss' AND reason='temporary')
                   )""",
                (username, cutoff, temporary_cutoff),
            ).fetchall()
        return {int(row["subject_id"]) for row in rows}

    def metrics(self, username: str, days: int = 30) -> dict[str, Any]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days, 1))).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT event,COUNT(*) n FROM recommendation_events
                   WHERE username=? AND created_at>=? GROUP BY event""",
                (username, cutoff),
            ).fetchall()
            reasons = conn.execute(
                """SELECT reason,COUNT(*) n FROM recommendation_events
                   WHERE username=? AND created_at>=? AND reason IS NOT NULL
                   GROUP BY reason ORDER BY n DESC""",
                (username, cutoff),
            ).fetchall()
            visible = conn.execute(
                """SELECT COUNT(DISTINCT set_id) sets,
                          COUNT(DISTINCT set_id || ':' || subject_id) items
                   FROM recommendation_events
                   WHERE username=? AND created_at>=? AND event='impression'""",
                (username, cutoff),
            ).fetchone()
            dimensions = conn.execute(
                """SELECT s.subject_type,s.scenario,COUNT(*) n
                   FROM recommendation_events e
                   JOIN recommendation_sets s ON s.id=e.set_id
                   WHERE e.username=? AND e.created_at>=? AND e.event='impression'
                   GROUP BY s.subject_type,s.scenario ORDER BY n DESC""",
                (username, cutoff),
            ).fetchall()
            model_rows = conn.execute(
                """SELECT DISTINCT s.request_json
                   FROM recommendation_events e
                   JOIN recommendation_sets s ON s.id=e.set_id
                   WHERE e.username=? AND e.created_at>=? AND e.event='impression'""",
                (username, cutoff),
            ).fetchall()
        counts = {row["event"]: int(row["n"]) for row in rows}
        impressions = counts.get("impression", 0)
        models: dict[str, int] = {}
        for row in model_rows:
            try:
                metadata = json.loads(row["request_json"]).get("_model_metadata") or {}
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            label = str(metadata.get("version") or metadata.get("built_at") or "无协同模型")
            models[label] = models.get(label, 0) + 1
        return {
            "days": days,
            "counts": counts,
            "wishlist_rate": counts.get("wishlist", 0) / impressions if impressions else 0.0,
            "start_rate": counts.get("started", 0) / impressions if impressions else 0.0,
            "dismiss_rate": counts.get("dismiss", 0) / impressions if impressions else 0.0,
            "dismiss_reasons": {row["reason"]: int(row["n"]) for row in reasons},
            "visible_impressions": impressions,
            "unique_visible_sets": int(visible["sets"] or 0),
            "unique_visible_items": int(visible["items"] or 0),
            "segments": [
                {
                    "subject_type": row["subject_type"],
                    "scenario": row["scenario"],
                    "impressions": int(row["n"]),
                }
                for row in dimensions
            ],
            "model_versions": models,
        }


def record_recommendation_feedback(
    store: RecommendationEventStore,
    ltm: LongTermMemory,
    username: str,
    req: RecommendationFeedbackRequest,
    *,
    channel: str = "web",
) -> dict[str, Any]:
    """Persist one recommendation decision and its cross-client memory signal."""
    event = store.record(username, req)
    payload = store.item_payload(req.recommendation_set_id, username, req.subject_id) or {}
    signal = None
    if req.event in {"more", "wishlist"}:
        signal = "more"
    elif req.event in {"started", "watched"}:
        signal = "like"
    elif req.event == "less":
        signal = "less"
    elif req.event == "dismiss" and req.reason in {"not_interested", "genre"}:
        signal = "dislike"
    replace_memory = req.event in {"more", "less", "wishlist", "started", "watched", "dismiss", "undo"}
    if event.get("recorded") and replace_memory:
        with tenant_scope(username, authenticated=True):
            memory = ltm.load_user(username)
            memory.feedback = [
                item for item in memory.feedback
                if not (
                    item.subject_id == req.subject_id
                    and item.source == "explicit_user"
                    and item.note.startswith("recommendation_card:")
                )
            ]
            if signal and req.event != "undo":
                note = req.note or req.reason or req.event
                memory.feedback.append(FeedbackItem(
                    subject_id=req.subject_id,
                    name=str(payload.get("name") or ""),
                    signal=signal,
                    scope=req.aspect,
                    note=f"recommendation_card:{channel}:{note}"[:500],
                    source="explicit_user",
                    confidence=0.9,
                    ts=now_iso(),
                ))
            memory.feedback = memory.feedback[-100:]
            ltm.save_user(memory)
    return event
