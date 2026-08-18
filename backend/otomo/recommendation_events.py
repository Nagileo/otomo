"""Recommendation batches, item feedback, and lightweight online metrics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import secrets
import sqlite3
from collections import Counter
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
                """WITH latest AS (
                       SELECT e.*,ROW_NUMBER() OVER (
                         PARTITION BY e.set_id,e.subject_id ORDER BY e.id DESC
                       ) rn
                       FROM recommendation_events e
                       WHERE e.username=? AND e.created_at>=?
                         AND e.event IN ('wishlist','started','dismiss','more','less','watched','undo')
                   )
                   SELECT s.subject_type,s.scenario,
                          COUNT(DISTINCT CASE WHEN e.event='impression'
                            THEN e.set_id || ':' || e.subject_id END) impressions,
                          COUNT(DISTINCT CASE WHEN p.event IN ('wishlist','started','more','watched')
                            THEN p.set_id || ':' || p.subject_id END) accepted,
                          COUNT(DISTINCT CASE WHEN p.event IN ('dismiss','less')
                            THEN p.set_id || ':' || p.subject_id END) dismissed
                   FROM recommendation_events e
                   JOIN recommendation_sets s ON s.id=e.set_id
                   LEFT JOIN latest p ON p.set_id=e.set_id AND p.subject_id=e.subject_id AND p.rn=1
                   WHERE e.username=? AND e.created_at>=?
                   GROUP BY s.subject_type,s.scenario ORDER BY impressions DESC""",
                (username, cutoff, username, cutoff),
            ).fetchall()
            positions = conn.execute(
                """WITH latest AS (
                       SELECT e.*,ROW_NUMBER() OVER (
                         PARTITION BY e.set_id,e.subject_id ORDER BY e.id DESC
                       ) rn
                       FROM recommendation_events e
                       WHERE e.username=? AND e.created_at>=?
                         AND e.event IN ('wishlist','started','dismiss','more','less','watched','undo')
                   )
                   SELECT i.position,
                          COUNT(DISTINCT CASE WHEN e.event='impression'
                            THEN e.set_id || ':' || e.subject_id END) impressions,
                          COUNT(DISTINCT CASE WHEN e.event='open'
                            THEN e.set_id || ':' || e.subject_id END) opens,
                          COUNT(DISTINCT CASE WHEN p.event IN ('wishlist','started','more','watched')
                            THEN p.set_id || ':' || p.subject_id END) accepted,
                          COUNT(DISTINCT CASE WHEN p.event IN ('dismiss','less')
                            THEN p.set_id || ':' || p.subject_id END) dismissed
                   FROM recommendation_events e
                   JOIN recommendation_items i ON i.set_id=e.set_id AND i.subject_id=e.subject_id
                   LEFT JOIN latest p ON p.set_id=e.set_id AND p.subject_id=e.subject_id AND p.rn=1
                   WHERE e.username=? AND e.created_at>=?
                   GROUP BY i.position ORDER BY i.position""",
                (username, cutoff, username, cutoff),
            ).fetchall()
            latest_preferences = conn.execute(
                """WITH latest AS (
                       SELECT e.*,ROW_NUMBER() OVER (
                         PARTITION BY e.subject_id ORDER BY e.id DESC
                       ) rn
                       FROM recommendation_events e
                       WHERE e.username=? AND e.created_at>=?
                         AND e.event IN ('wishlist','started','dismiss','more','less','watched','undo')
                   )
                   SELECT l.event,l.reason,l.aspect,i.payload_json
                   FROM latest l
                   JOIN recommendation_items i ON i.set_id=l.set_id AND i.subject_id=l.subject_id
                   WHERE l.rn=1 AND l.event!='undo'""",
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
        decision_count = len(latest_preferences)
        models: dict[str, int] = {}
        for row in model_rows:
            try:
                metadata = json.loads(row["request_json"]).get("_model_metadata") or {}
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            label = str(metadata.get("version") or metadata.get("built_at") or "无协同模型")
            models[label] = models.get(label, 0) + 1
        positive_tags: Counter[str] = Counter()
        negative_tags: Counter[str] = Counter()
        preference_aspects: Counter[str] = Counter()
        for row in latest_preferences:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            tags = [str(tag) for tag in payload.get("diversity_tags") or [] if str(tag).strip()][:12]
            if row["event"] in {"wishlist", "started", "more", "watched"}:
                positive_tags.update(tags)
            elif row["event"] in {"dismiss", "less"} and row["reason"] != "temporary":
                negative_tags.update(tags)
            if row["aspect"] and row["aspect"] != "item":
                preference_aspects[str(row["aspect"])] += 1
        confidence = "high" if decision_count >= 30 else "medium" if decision_count >= 10 else "low"
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
                    "impressions": int(row["impressions"]),
                    "accepted": int(row["accepted"]),
                    "dismissed": int(row["dismissed"]),
                    "acceptance_rate": (
                        int(row["accepted"]) / int(row["impressions"])
                        if row["impressions"] else 0.0
                    ),
                }
                for row in dimensions
            ],
            "positions": [
                {
                    "position": int(row["position"]),
                    "impressions": int(row["impressions"]),
                    "opens": int(row["opens"]),
                    "accepted": int(row["accepted"]),
                    "dismissed": int(row["dismissed"]),
                    "acceptance_rate": (
                        int(row["accepted"]) / int(row["impressions"])
                        if row["impressions"] else 0.0
                    ),
                }
                for row in positions
            ],
            "model_versions": models,
            "personalization": {
                "confidence": confidence,
                "decision_samples": decision_count,
                "positive_tags": [tag for tag, _count in positive_tags.most_common(8)],
                "negative_tags": [tag for tag, _count in negative_tags.most_common(8)],
                "scoped_feedback": dict(preference_aspects.most_common()),
                "note": (
                    "反馈样本仍少，当前画像会保守参与排序。"
                    if confidence == "low" else
                    "已有一定反馈样本，仍以你本轮明确要求为最高优先级。"
                    if confidence == "medium" else
                    "反馈样本较充分，但不会覆盖你本轮明确要求。"
                ),
            },
        }

    def _period_evaluation(
        self,
        username: str | None,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        where = "s.created_at>=? AND s.created_at<?"
        params: list[Any] = [start.isoformat(), end.isoformat()]
        if username:
            where += " AND s.username=?"
            params.append(username)
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT s.id,s.subject_type,s.scenario,s.request_json,s.created_at,
                            i.subject_id,i.position,i.payload_json
                     FROM recommendation_sets s JOIN recommendation_items i ON i.set_id=s.id
                     WHERE {where} ORDER BY s.created_at,i.position""",
                params,
            ).fetchall()
            event_where = "e.created_at>=? AND e.created_at<?"
            event_params: list[Any] = [start.isoformat(), end.isoformat()]
            if username:
                event_where += " AND e.username=?"
                event_params.append(username)
            events = conn.execute(
                f"""SELECT e.id,e.set_id,e.subject_id,e.event
                     FROM recommendation_events e WHERE {event_where} ORDER BY e.id""",
                event_params,
            ).fetchall()

        batches: dict[str, dict[str, Any]] = {}
        all_ids: list[int] = []
        supported_claims = 0
        checked_claims = 0
        supported_cards = 0
        total_cards = 0
        durations: list[float] = []
        cache_hits = 0
        cache_misses = 0
        strategies: Counter[str] = Counter()
        for row in rows:
            set_id = str(row["id"])
            batch = batches.setdefault(set_id, {
                "subject_type": str(row["subject_type"]),
                "scenario": str(row["scenario"]),
                "request_json": str(row["request_json"]),
                "positions": {},
                "impressions": set(),
                "latest": {},
            })
            subject_id = int(row["subject_id"])
            batch["positions"][subject_id] = int(row["position"])
            all_ids.append(subject_id)
            total_cards += 1
            try:
                payload = json.loads(str(row["payload_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            claims = [
                claim for claim in (payload.get("claims") or [])
                if claim.get("kind") in {"fit", "risk", "quality"}
            ]
            card_supported = any(claim.get("support") for claim in claims)
            checked_claims += len(claims)
            supported_claims += sum(1 for claim in claims if claim.get("support"))
            supported_cards += int(card_supported)

        for batch in batches.values():
            try:
                request = json.loads(batch["request_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                request = {}
            perf = request.get("_performance") or {}
            if float(perf.get("total_ms") or 0) > 0:
                durations.append(float(perf["total_ms"]))
            cache = perf.get("evidence_cache") or {}
            cache_hits += int(cache.get("hits") or 0)
            cache_misses += int(cache.get("misses") or 0)
            strategy = request.get("_strategy_metadata") or {}
            strategies[str(strategy.get("version") or "legacy")] += 1

        preference_events = {"wishlist", "started", "dismiss", "more", "less", "watched", "undo"}
        for event in events:
            batch = batches.get(str(event["set_id"]))
            if batch is None:
                continue
            subject_id = int(event["subject_id"])
            if event["event"] == "impression":
                batch["impressions"].add(subject_id)
            if event["event"] in preference_events:
                batch["latest"][subject_id] = str(event["event"])

        positive = {"wishlist", "started", "more", "watched"}
        visible = [batch for batch in batches.values() if batch["impressions"]]
        hit_counts = {1: 0, 3: 0, 5: 0}
        reciprocal_ranks: list[float] = []
        ndcgs: list[float] = []
        accepted_items = 0
        visible_items = 0
        segments: dict[tuple[str, str], dict[str, Any]] = {}
        for batch in visible:
            accepted_positions = sorted(
                batch["positions"][subject_id]
                for subject_id, event in batch["latest"].items()
                if event in positive
                and subject_id in batch["positions"]
                and subject_id in batch["impressions"]
            )
            visible_items += len(batch["impressions"])
            accepted_items += len({
                subject_id for subject_id, event in batch["latest"].items()
                if event in positive and subject_id in batch["impressions"]
            })
            for k in hit_counts:
                hit_counts[k] += int(any(position <= k for position in accepted_positions))
            reciprocal_ranks.append(1 / accepted_positions[0] if accepted_positions else 0.0)
            if accepted_positions:
                dcg = sum(1 / math.log2(position + 1) for position in accepted_positions)
                ideal = sum(1 / math.log2(position + 1) for position in range(1, len(accepted_positions) + 1))
                ndcgs.append(dcg / ideal if ideal else 0.0)
            else:
                ndcgs.append(0.0)
            key = (batch["subject_type"], batch["scenario"])
            segment = segments.setdefault(key, {
                "subject_type": key[0], "scenario": key[1],
                "visible_sets": 0, "accepted_sets": 0,
            })
            segment["visible_sets"] += 1
            segment["accepted_sets"] += int(bool(accepted_positions))

        denominator = len(visible)
        total = len(all_ids)
        unique = len(set(all_ids))
        sorted_durations = sorted(durations)
        p95_index = max(0, math.ceil(len(sorted_durations) * 0.95) - 1)
        cache_total = cache_hits + cache_misses
        return {
            "sets": len(batches),
            "visible_sets": denominator,
            "visible_items": visible_items,
            "accepted_items": accepted_items,
            "item_acceptance_rate": accepted_items / visible_items if visible_items else 0.0,
            "acceptance_at_k": {
                str(k): hit_counts[k] / denominator if denominator else 0.0
                for k in (1, 3, 5)
            },
            "mrr": sum(reciprocal_ranks) / denominator if denominator else 0.0,
            "ndcg": sum(ndcgs) / denominator if denominator else 0.0,
            "catalog": {
                "recommended_items": total,
                "unique_items": unique,
                "unique_ratio": unique / total if total else 0.0,
                "repeat_rate": 1 - unique / total if total else 0.0,
            },
            "explanations": {
                "cards": total_cards,
                "supported_cards": supported_cards,
                "card_support_coverage": supported_cards / total_cards if total_cards else 0.0,
                "claims": checked_claims,
                "supported_claims": supported_claims,
                "claim_support_coverage": supported_claims / checked_claims if checked_claims else 0.0,
            },
            "performance": {
                "measured_sets": len(durations),
                "average_ms": sum(durations) / len(durations) if durations else 0.0,
                "p95_ms": sorted_durations[p95_index] if sorted_durations else 0.0,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "cache_hit_rate": cache_hits / cache_total if cache_total else 0.0,
            },
            "strategy_versions": dict(strategies),
            "segments": [
                {
                    **segment,
                    "acceptance_rate": (
                        segment["accepted_sets"] / segment["visible_sets"]
                        if segment["visible_sets"] else 0.0
                    ),
                }
                for segment in segments.values()
            ],
        }

    def evaluation_report(
        self, username: str | None = None, days: int = 30,
    ) -> dict[str, Any]:
        bounded_days = min(max(int(days), 1), 365)
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=bounded_days)
        previous_start = start - timedelta(days=bounded_days)
        current = self._period_evaluation(username, start, now)
        previous = self._period_evaluation(username, previous_start, start)
        return {
            "days": bounded_days,
            "scope": f"user:{username}" if username else "global",
            "current": current,
            "previous": previous,
            "trend": {
                "acceptance_at_3": (
                    current["acceptance_at_k"]["3"] - previous["acceptance_at_k"]["3"]
                ),
                "ndcg": current["ndcg"] - previous["ndcg"],
                "explanation_support": (
                    current["explanations"]["claim_support_coverage"]
                    - previous["explanations"]["claim_support_coverage"]
                ),
                "average_ms": (
                    current["performance"]["average_ms"]
                    - previous["performance"]["average_ms"]
                ),
            },
        }

    def history(self, username: str, limit: int = 12) -> list[dict[str, Any]]:
        """Return recent recommendation sets with each card's latest explicit decision."""
        bounded_limit = min(max(limit, 1), 50)
        with self._connect() as conn:
            sets = conn.execute(
                """SELECT id,subject_type,scenario,request_json,created_at
                   FROM recommendation_sets WHERE username=?
                   ORDER BY created_at DESC LIMIT ?""",
                (username, bounded_limit),
            ).fetchall()
            history: list[dict[str, Any]] = []
            for row in sets:
                items = conn.execute(
                    """SELECT i.position,i.payload_json,
                              (SELECT e.event FROM recommendation_events e
                               WHERE e.set_id=i.set_id AND e.subject_id=i.subject_id
                                 AND e.event IN ('wishlist','started','dismiss','more','less','watched','undo')
                               ORDER BY e.id DESC LIMIT 1) latest_event,
                              (SELECT e.reason FROM recommendation_events e
                               WHERE e.set_id=i.set_id AND e.subject_id=i.subject_id
                                 AND e.event IN ('wishlist','started','dismiss','more','less','watched','undo')
                               ORDER BY e.id DESC LIMIT 1) latest_reason
                       FROM recommendation_items i WHERE i.set_id=? ORDER BY i.position""",
                    (row["id"],),
                ).fetchall()
                request = json.loads(row["request_json"])
                request.pop("username", None)
                payload_items = []
                for item in items:
                    payload = json.loads(item["payload_json"])
                    payload["position"] = int(item["position"])
                    payload["latest_event"] = item["latest_event"]
                    payload["latest_reason"] = item["latest_reason"]
                    payload_items.append(payload)
                history.append({
                    "id": row["id"],
                    "subject_type": row["subject_type"],
                    "scenario": row["scenario"],
                    "request": request,
                    "items": payload_items,
                    "created_at": row["created_at"],
                })
        return history


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
