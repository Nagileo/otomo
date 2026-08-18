"""Persistent, bounded cache for expensive recommendation evidence artifacts."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from .config import settings


class RecommendationArtifactCache:
    def __init__(self, path: str | None = None, ttl: float | None = None) -> None:
        self.path = Path(path or settings.recommendation_artifact_cache_path)
        self.ttl = max(60.0, float(ttl or settings.recommendation_review_cache_ttl))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS recommendation_artifacts (
                    cache_key TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL, created_at REAL NOT NULL,
                    expires_at REAL NOT NULL, accessed_at REAL NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_recommendation_artifacts_expiry
                    ON recommendation_artifacts(expires_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json,expires_at FROM recommendation_artifacts WHERE cache_key=?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) <= now:
                conn.execute("DELETE FROM recommendation_artifacts WHERE cache_key=?", (key,))
                return None
            conn.execute(
                """UPDATE recommendation_artifacts SET accessed_at=?,hit_count=hit_count+1
                   WHERE cache_key=?""",
                (now, key),
            )
        try:
            value = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def set(self, key: str, payload: dict[str, Any], *, kind: str = "review") -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO recommendation_artifacts
                   (cache_key,kind,payload_json,created_at,expires_at,accessed_at,hit_count)
                   VALUES(?,?,?,?,?,?,0)
                   ON CONFLICT(cache_key) DO UPDATE SET kind=excluded.kind,
                     payload_json=excluded.payload_json,created_at=excluded.created_at,
                     expires_at=excluded.expires_at,accessed_at=excluded.accessed_at""",
                (
                    key, kind, json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    now, now + self.ttl, now,
                ),
            )
            conn.execute("DELETE FROM recommendation_artifacts WHERE expires_at<=?", (now,))
            # Keep disk use predictable even if many users explore long-tail items.
            conn.execute(
                """DELETE FROM recommendation_artifacts WHERE cache_key IN (
                     SELECT cache_key FROM recommendation_artifacts
                     ORDER BY accessed_at DESC LIMIT -1 OFFSET 5000
                   )"""
            )

    def stats(self) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) entries,COALESCE(SUM(hit_count),0) hits,
                   COALESCE(SUM(LENGTH(payload_json)),0) bytes
                   FROM recommendation_artifacts WHERE expires_at>?""",
                (now,),
            ).fetchone()
        return {
            "entries": int(row["entries"] or 0),
            "hits": int(row["hits"] or 0),
            "bytes": int(row["bytes"] or 0),
            "ttl_seconds": int(self.ttl),
        }
