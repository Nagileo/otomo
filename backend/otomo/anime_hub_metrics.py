"""Persistent Anime Hub latency and module-health observations."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from .config import settings


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(max(round((len(ordered) - 1) * ratio), 0), len(ordered) - 1)
    return int(ordered[index])


class AnimeHubMetricStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.anime_hub_metrics_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS anime_hub_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    subject_id INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    total_ms INTEGER NOT NULL,
                    modules_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_anime_hub_runs_created
                    ON anime_hub_runs(created_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def record(
        self,
        *,
        subject_id: int,
        stage: str,
        total_ms: int,
        modules: dict[str, Any],
    ) -> None:
        payload = {
            name: (
                state.model_dump(mode="json", exclude_none=True)
                if hasattr(state, "model_dump") else state
            )
            for name, state in modules.items()
        }
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO anime_hub_runs(created_at,subject_id,stage,total_ms,modules_json) VALUES(?,?,?,?,?)",
                (now, subject_id, stage, max(int(total_ms), 0), json.dumps(payload, ensure_ascii=False)),
            )
            conn.execute("DELETE FROM anime_hub_runs WHERE created_at<?", (now - 90 * 86400,))

    def summary(self, days: int = 30) -> dict[str, Any]:
        cutoff = time.time() - min(max(days, 1), 90) * 86400
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at,subject_id,stage,total_ms,modules_json FROM anime_hub_runs WHERE created_at>=? ORDER BY created_at DESC",
                (cutoff,),
            ).fetchall()
        totals = [int(row["total_ms"] or 0) for row in rows]
        module_rows: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            try:
                modules = json.loads(str(row["modules_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            for name, state in modules.items():
                if isinstance(state, dict):
                    module_rows.setdefault(name, []).append(state)
        modules_summary: dict[str, Any] = {}
        for name, states in module_rows.items():
            durations = [int(state.get("duration_ms") or 0) for state in states]
            cache_known = [bool(state.get("cache_hit")) for state in states if state.get("cache_hit") is not None]
            failures = sum(state.get("status") == "failed" for state in states)
            modules_summary[name] = {
                "runs": len(states),
                "p50_ms": _percentile(durations, 0.5),
                "p95_ms": _percentile(durations, 0.95),
                "failure_rate": round(failures / len(states), 4) if states else 0,
                "cache_hit_rate": round(sum(cache_known) / len(cache_known), 4) if cache_known else None,
            }
        return {
            "days": min(max(days, 1), 90),
            "runs": len(rows),
            "p50_ms": _percentile(totals, 0.5),
            "p95_ms": _percentile(totals, 0.95),
            "modules": modules_summary,
            "recent_slow": [
                {
                    "subject_id": int(row["subject_id"]),
                    "stage": str(row["stage"]),
                    "total_ms": int(row["total_ms"]),
                    "created_at": float(row["created_at"]),
                }
                for row in sorted(rows, key=lambda item: int(item["total_ms"]), reverse=True)[:8]
            ],
        }
