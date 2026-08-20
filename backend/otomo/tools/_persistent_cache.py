"""Small cross-process JSON cache for rate-limited external APIs."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
from typing import Any


class PersistentJsonCache:
    def __init__(self, path: str | Path, namespace: str) -> None:
        self.path = Path(path)
        self.namespace = namespace

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=5)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            "CREATE TABLE IF NOT EXISTS external_cache ("
            "namespace TEXT NOT NULL, cache_key TEXT NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL, "
            "PRIMARY KEY(namespace, cache_key))"
        )
        return con

    def get(self, key: str, *, ttl: float | None) -> tuple[dict[str, Any], float] | None:
        try:
            with self._connect() as con:
                row = con.execute(
                    "SELECT created_at, payload FROM external_cache WHERE namespace=? AND cache_key=?",
                    (self.namespace, key),
                ).fetchone()
        except (OSError, sqlite3.Error):
            return None
        if not row:
            return None
        created_at = float(row[0])
        if ttl is not None and time.time() - created_at > ttl:
            return None
        try:
            payload = json.loads(str(row[1]))
        except (TypeError, ValueError):
            return None
        return (payload, created_at) if isinstance(payload, dict) else None

    def set(self, key: str, payload: dict[str, Any]) -> float:
        created_at = time.time()
        try:
            with self._connect() as con:
                con.execute(
                    "INSERT INTO external_cache(namespace, cache_key, created_at, payload) VALUES(?,?,?,?) "
                    "ON CONFLICT(namespace, cache_key) DO UPDATE SET created_at=excluded.created_at, payload=excluded.payload",
                    (self.namespace, key, created_at, json.dumps(payload, ensure_ascii=False)),
                )
        except (OSError, sqlite3.Error):
            pass
        return created_at
