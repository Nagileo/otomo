"""Account-scoped product workspace state.

Saved views and editorial lists are product state, not preference evidence. They
live outside long-term memory so creating a list cannot accidentally change a
recommendation profile.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config import settings
from .memory.consolidate import now_iso


class SavedViewCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    surface: Literal["discover", "library", "today"]
    params: dict[str, Any] = Field(default_factory=dict)


class SavedView(SavedViewCreate):
    id: str
    created_at: str
    updated_at: str


class WorkspaceListCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)
    description: str = Field("", max_length=300)


class WorkspaceListItemRequest(BaseModel):
    subject_id: int = Field(..., ge=1)
    name: str = Field("", max_length=160)
    subject_type: str = Field("anime", max_length=20)
    image: str = Field("", max_length=1000)
    note: str = Field("", max_length=300)


class WorkspaceListItem(WorkspaceListItemRequest):
    created_at: str


class WorkspaceList(WorkspaceListCreate):
    id: str
    items: list[WorkspaceListItem] = Field(default_factory=list)
    created_at: str
    updated_at: str


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class WorkspaceStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.workspace_store_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS saved_views (
                    id TEXT PRIMARY KEY,
                    owner_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_saved_views_owner
                    ON saved_views(owner_key, updated_at DESC);
                CREATE TABLE IF NOT EXISTS workspace_lists (
                    id TEXT PRIMARY KEY,
                    owner_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workspace_lists_owner
                    ON workspace_lists(owner_key, updated_at DESC);
                CREATE TABLE IF NOT EXISTS workspace_list_items (
                    list_id TEXT NOT NULL,
                    subject_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    image TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(list_id, subject_id),
                    FOREIGN KEY(list_id) REFERENCES workspace_lists(id) ON DELETE CASCADE
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def list_views(self, owner_key: str) -> list[SavedView]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM saved_views WHERE owner_key=? ORDER BY updated_at DESC", (owner_key,)
            ).fetchall()
        return [self._view(row) for row in rows]

    def create_view(self, owner_key: str, req: SavedViewCreate) -> SavedView:
        now = now_iso()
        view = SavedView(id=str(uuid.uuid4()), **req.model_dump(), created_at=now, updated_at=now)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO saved_views VALUES(?,?,?,?,?,?,?)",
                (view.id, owner_key, view.name, view.surface, _dump(view.params), now, now),
            )
        return view

    def delete_view(self, owner_key: str, view_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM saved_views WHERE id=? AND owner_key=?", (view_id, owner_key))
        return cur.rowcount > 0

    def list_lists(self, owner_key: str) -> list[WorkspaceList]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workspace_lists WHERE owner_key=? ORDER BY updated_at DESC", (owner_key,)
            ).fetchall()
            return [self._list(conn, row) for row in rows]

    def create_list(self, owner_key: str, req: WorkspaceListCreate) -> WorkspaceList:
        now = now_iso()
        result = WorkspaceList(
            id=str(uuid.uuid4()), **req.model_dump(), items=[], created_at=now, updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO workspace_lists VALUES(?,?,?,?,?,?)",
                (result.id, owner_key, result.title, result.description, now, now),
            )
        return result

    def delete_list(self, owner_key: str, list_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM workspace_lists WHERE id=? AND owner_key=?", (list_id, owner_key))
        return cur.rowcount > 0

    def upsert_item(
        self, owner_key: str, list_id: str, req: WorkspaceListItemRequest,
    ) -> WorkspaceList | None:
        now = now_iso()
        with self._connect() as conn:
            owner = conn.execute(
                "SELECT * FROM workspace_lists WHERE id=? AND owner_key=?", (list_id, owner_key)
            ).fetchone()
            if not owner:
                return None
            conn.execute(
                """INSERT INTO workspace_list_items(
                    list_id,subject_id,name,subject_type,image,note,created_at
                ) VALUES(?,?,?,?,?,?,?) ON CONFLICT(list_id,subject_id) DO UPDATE SET
                    name=excluded.name,subject_type=excluded.subject_type,
                    image=excluded.image,note=excluded.note""",
                (list_id, req.subject_id, req.name, req.subject_type, req.image, req.note, now),
            )
            conn.execute("UPDATE workspace_lists SET updated_at=? WHERE id=?", (now, list_id))
            row = conn.execute("SELECT * FROM workspace_lists WHERE id=?", (list_id,)).fetchone()
            return self._list(conn, row)

    def delete_item(self, owner_key: str, list_id: str, subject_id: int) -> bool:
        with self._connect() as conn:
            owner = conn.execute(
                "SELECT 1 FROM workspace_lists WHERE id=? AND owner_key=?", (list_id, owner_key)
            ).fetchone()
            if not owner:
                return False
            cur = conn.execute(
                "DELETE FROM workspace_list_items WHERE list_id=? AND subject_id=?",
                (list_id, subject_id),
            )
            if cur.rowcount:
                conn.execute("UPDATE workspace_lists SET updated_at=? WHERE id=?", (now_iso(), list_id))
        return cur.rowcount > 0

    @staticmethod
    def _view(row: sqlite3.Row) -> SavedView:
        return SavedView(
            id=row["id"], name=row["name"], surface=row["surface"],
            params=json.loads(row["params_json"]), created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _list(conn: sqlite3.Connection, row: sqlite3.Row) -> WorkspaceList:
        items = conn.execute(
            "SELECT * FROM workspace_list_items WHERE list_id=? ORDER BY created_at DESC", (row["id"],)
        ).fetchall()
        return WorkspaceList(
            id=row["id"], title=row["title"], description=row["description"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            items=[WorkspaceListItem(
                subject_id=x["subject_id"], name=x["name"], subject_type=x["subject_type"],
                image=x["image"], note=x["note"], created_at=x["created_at"],
            ) for x in items],
        )
