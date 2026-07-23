"""Transactional long-term memory store.

SQLite/WAL is shared by the API, scheduler and Discord processes. Legacy JSON
records are imported lazily. User records carry an in-memory baseline so a
stale save can be three-way merged instead of silently losing concurrent
updates from another process.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..config import settings
from ..security_context import assert_private_user
from .consolidate import now_iso
from .models import UserMemory


def _safe_key(key: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]", "_", key)[:80] or "default"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _stable_id(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("id", "subject_id", "action_id", "rule_id"):
        if value.get(key) is not None:
            return f"{key}:{value[key]}"
    if value.get("aspect") and value.get("polarity"):
        return f"aspect:{value['aspect']}:{value['polarity']}"
    if value.get("value"):
        return f"value:{value.get('source', '')}:{value['value']}"
    if value.get("ts"):
        return (
            f"event:{value.get('signal', value.get('kind', ''))}:"
            f"{value.get('name', '')}:{value['ts']}"
        )
    return None


def _merge_list(base: list[Any], local: list[Any], remote: list[Any]) -> list[Any]:
    """Three-way merge model lists while respecting local removals.

    Lists without stable identifiers are treated as a scalar. Most mutable LTM
    collections use id/subject_id, so concurrent appends are preserved.
    """
    if local == base:
        return remote
    if remote == base:
        return local
    if not all(_stable_id(x) for x in [*base, *local, *remote]):
        return local
    base_map = {_stable_id(x): x for x in base}
    local_map = {_stable_id(x): x for x in local}
    remote_map = {_stable_id(x): x for x in remote}
    # Deletion wins over an unrelated concurrent edit/append. Otherwise a
    # stale API worker can resurrect an inbox item or watch-plan row that the
    # user just removed in another process.
    removed = (set(base_map) - set(local_map)) | (set(base_map) - set(remote_map))
    order = [key for x in remote if (key := _stable_id(x)) not in removed]
    for x in local:
        key = _stable_id(x)
        if key not in removed and key not in order:
            order.append(key)
    out: list[Any] = []
    for key in order:
        if key in local_map and key in remote_map and key in base_map:
            out.append(_three_way(base_map[key], local_map[key], remote_map[key]))
        elif key in local_map:
            out.append(local_map[key])
        elif key in remote_map:
            out.append(remote_map[key])
    return out


def _three_way(base: Any, local: Any, remote: Any) -> Any:
    if local == base:
        return remote
    if remote == base or local == remote:
        return local
    if isinstance(base, dict) and isinstance(local, dict) and isinstance(remote, dict):
        out: dict[str, Any] = {}
        for key in set(base) | set(local) | set(remote):
            if key not in local and key in base:
                continue
            if key not in local:
                out[key] = remote.get(key)
            elif key not in remote:
                out[key] = local[key]
            else:
                out[key] = _three_way(base.get(key), local[key], remote[key])
        return out
    if isinstance(base, list) and isinstance(local, list) and isinstance(remote, list):
        return _merge_list(base, local, remote)
    return local


class LongTermMemory:
    def __init__(self, base_dir: Path | None = None) -> None:
        self._explicit_base = base_dir is not None
        self.base = Path(base_dir) if base_dir is not None else Path(settings.ltm_store_path).parent
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "ltm.sqlite3" if base_dir is not None else Path(settings.ltm_store_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_kv (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(namespace, key)
                )
                """
            )

    def _legacy_path(self, namespace: str, key: str) -> Path:
        return self.base / f"{_safe_key(namespace)}__{_safe_key(key)}.json"

    def _read_row(self, namespace: str, key: str) -> tuple[Any, int] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload, version FROM memory_kv WHERE namespace=? AND key=?",
                (namespace, key),
            ).fetchone()
        if row:
            try:
                return json.loads(str(row["payload"])), int(row["version"])
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
        legacy = self._legacy_path(namespace, key)
        if not legacy.exists():
            return None
        try:
            value = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        self.set(namespace, key, value)
        legacy.rename(legacy.with_suffix(".json.migrated"))
        return value, 1

    def get(self, namespace: str, key: str) -> Any | None:
        if namespace == "user_memory":
            assert_private_user(key)
        row = self._read_row(namespace, key)
        return row[0] if row else None

    def set(self, namespace: str, key: str, value: Any) -> None:
        if namespace == "user_memory":
            assert_private_user(key)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO memory_kv(namespace, key, payload, version, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    payload=excluded.payload,
                    version=memory_kv.version + 1,
                    updated_at=excluded.updated_at
                """,
                (namespace, key, _json(value), 1, now_iso()),
            )

    def load_user(self, username: str) -> UserMemory:
        assert_private_user(username)
        row = self._read_row("user_memory", username)
        raw, version = row if row else ({"username": username}, 0)
        try:
            mem = UserMemory.model_validate(raw)
        except Exception:  # noqa: BLE001
            mem = UserMemory(username=username)
            raw = mem.model_dump(mode="json", exclude_none=True)
            version = 0
        mem._store_revision = version
        mem._store_baseline = json.loads(_json(raw))
        return mem

    def save_user(self, mem: UserMemory) -> None:
        assert_private_user(mem.username)
        mem.updated_at = now_iso()
        local = mem.model_dump(mode="json", exclude_none=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload, version FROM memory_kv WHERE namespace='user_memory' AND key=?",
                (mem.username,),
            ).fetchone()
            remote = json.loads(str(row["payload"])) if row else {"username": mem.username}
            version = int(row["version"]) if row else 0
            payload = local
            if version != mem._store_revision:
                payload = _three_way(mem._store_baseline or {"username": mem.username}, local, remote)
                payload["username"] = mem.username
                payload["updated_at"] = mem.updated_at
            conn.execute(
                """
                INSERT INTO memory_kv(namespace, key, payload, version, updated_at)
                VALUES('user_memory',?,?,?,?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    payload=excluded.payload,
                    version=excluded.version,
                    updated_at=excluded.updated_at
                """,
                (mem.username, _json(payload), version + 1, mem.updated_at),
            )
        merged = UserMemory.model_validate(payload)
        for field in type(mem).model_fields:
            setattr(mem, field, getattr(merged, field))
        mem._store_revision = version + 1
        mem._store_baseline = json.loads(_json(payload))

    def list_users(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key FROM memory_kv WHERE namespace='user_memory' ORDER BY key"
            ).fetchall()
        legacy = [
            p.stem.removeprefix("user_memory__")
            for p in self.base.glob("user_memory__*.json")
        ]
        return sorted({str(row["key"]) for row in rows} | {x for x in legacy if x})
