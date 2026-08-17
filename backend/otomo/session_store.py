"""SQLite-backed chat session persistence."""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .agent.contracts import AgentState
from .config import settings


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class SessionStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.session_store_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    auth_session_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    state_json TEXT NOT NULL DEFAULT '{}',
                    source TEXT NOT NULL DEFAULT 'web',
                    source_label TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    trace_json TEXT NOT NULL DEFAULT '[]',
                    steps_json TEXT NOT NULL DEFAULT '[]',
                    turn_id TEXT NOT NULL DEFAULT '',
                    elapsed_ms INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
                """
            )
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
            if "source" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'web'")
            if "source_label" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN source_label TEXT NOT NULL DEFAULT ''")
            if "revision" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
            message_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(messages)").fetchall()
            }
            if "trace_json" not in message_columns:
                conn.execute("ALTER TABLE messages ADD COLUMN trace_json TEXT NOT NULL DEFAULT '[]'")
            if "steps_json" not in message_columns:
                conn.execute("ALTER TABLE messages ADD COLUMN steps_json TEXT NOT NULL DEFAULT '[]'")
            if "turn_id" not in message_columns:
                conn.execute("ALTER TABLE messages ADD COLUMN turn_id TEXT NOT NULL DEFAULT ''")
            if "elapsed_ms" not in message_columns:
                conn.execute("ALTER TABLE messages ADD COLUMN elapsed_ms INTEGER")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_handoffs (
                    code TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    source_owner TEXT NOT NULL,
                    source_label TEXT NOT NULL DEFAULT 'Discord',
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_auth_updated ON sessions(auth_session_id, updated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_handoffs_expiry ON session_handoffs(expires_at)")

    def ensure_session(
        self,
        session_id: str,
        auth_session_id: str,
        title: str = "",
        *,
        source: str = "web",
        source_label: str = "",
    ) -> dict[str, Any]:
        # auth_session_id 存的是"归属键"：匿名=cookie 会话 id，OAuth 登录后=user:<username>
        # （登录时由 migrate_owner 迁移，同一账号跨设备可见）。
        now = _now()
        clean_title = title.strip()[:80]
        with self._connect() as conn:
            # INSERT OR IGNORE 先行：并发首写同一 session_id 时 SELECT-then-INSERT 会撞 UNIQUE
            conn.execute(
                """
                INSERT OR IGNORE INTO sessions(
                    id, auth_session_id, title, state_json, source, source_label,
                    revision, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,0,?,?)
                """,
                (
                    session_id,
                    auth_session_id,
                    clean_title or "新对话",
                    "{}",
                    source.strip()[:24] or "web",
                    source_label.strip()[:80],
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row["auth_session_id"] != auth_session_id:
                raise PermissionError("session owner mismatch")
            if clean_title and not row["title"]:
                conn.execute(
                    "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
                    (clean_title, now, session_id),
                )
            return dict(row)

    def _existing_session(self, session_id: str, auth_session_id: str) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise FileNotFoundError("session not found")
        if row["auth_session_id"] != auth_session_id:
            raise PermissionError("session owner mismatch")
        return row

    def touch(self, session_id: str, auth_session_id: str) -> None:
        self.ensure_session(session_id, auth_session_id)
        with self._connect() as conn:
            conn.execute("UPDATE sessions SET updated_at=? WHERE id=? AND auth_session_id=?", (_now(), session_id, auth_session_id))

    def list_sessions(self, auth_session_id: str, limit: int = 40) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, created_at, updated_at,
                       source, source_label, revision,
                       (SELECT COUNT(*) FROM messages WHERE session_id=sessions.id) AS message_count
                FROM sessions
                WHERE auth_session_id=?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (auth_session_id, max(1, min(limit, 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def message_count(self, session_id: str, auth_session_id: str) -> int:
        self._existing_session(session_id, auth_session_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return int(row["n"] or 0) if row else 0

    def rename_session(self, session_id: str, auth_session_id: str, title: str) -> dict[str, Any]:
        self._existing_session(session_id, auth_session_id)
        clean = title.strip()[:80] or "新对话"
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET title=?, updated_at=?, revision=revision+1 WHERE id=? AND auth_session_id=?",
                (clean, now, session_id, auth_session_id),
            )
        return {"id": session_id, "title": clean, "updated_at": now}

    def delete_session(self, session_id: str, auth_session_id: str) -> None:
        self._existing_session(session_id, auth_session_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id=? AND auth_session_id=?", (session_id, auth_session_id))

    def clear_messages(self, session_id: str, auth_session_id: str) -> None:
        self._existing_session(session_id, auth_session_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            conn.execute(
                "UPDATE sessions SET revision=revision+1, updated_at=? WHERE id=?",
                (_now(), session_id),
            )

    def delete_owner_sessions(self, auth_session_id: str) -> int:
        """Delete every short-term conversation owned by one surface identity."""
        if not auth_session_id:
            return 0
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE auth_session_id=?", (auth_session_id,))
        return int(cur.rowcount or 0)

    def save_state(self, session_id: str, auth_session_id: str, state: AgentState | None) -> None:
        if state is None:
            return
        self.ensure_session(session_id, auth_session_id)
        payload = state.model_dump(mode="json", exclude_none=True)
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET state_json=?, updated_at=?, revision=revision+1 WHERE id=? AND auth_session_id=?",
                (_json_dump(payload), _now(), session_id, auth_session_id),
            )

    def load_state(self, session_id: str, auth_session_id: str) -> AgentState | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM sessions WHERE id=? AND auth_session_id=?",
                (session_id, auth_session_id),
            ).fetchone()
        if not row:
            return None
        payload = _json_load(row["state_json"], {})
        if not isinstance(payload, dict):
            return None
        try:
            return AgentState.model_validate(payload)
        except Exception:  # noqa: BLE001
            return None

    def append_message(
        self,
        session_id: str,
        auth_session_id: str,
        *,
        role: str,
        content: str,
        attachments: list[dict[str, Any]] | None = None,
        evidence: dict[str, Any] | None = None,
        sources: list[dict[str, Any]] | None = None,
        trace: list[dict[str, Any]] | None = None,
        steps: list[str] | None = None,
        turn_id: str = "",
        elapsed_ms: int | None = None,
    ) -> None:
        title = content.strip()[:40] if role == "user" else ""
        self.ensure_session(session_id, auth_session_id, title)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages(
                    session_id, role, content, attachments_json, evidence_json, sources_json,
                    trace_json, steps_json, turn_id, elapsed_ms, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    role,
                    content,
                    _json_dump(attachments or []),
                    _json_dump(evidence or {}),
                    _json_dump(sources or []),
                    _json_dump(trace or []),
                    _json_dump(steps or []),
                    turn_id.strip()[:96],
                    max(0, int(elapsed_ms)) if elapsed_ms is not None else None,
                    _now(),
                ),
            )
            conn.execute(
                """
                UPDATE sessions
                SET updated_at=?,
                    title=CASE WHEN title='新对话' AND ?!='' THEN ? ELSE title END,
                    revision=revision+1
                WHERE id=?
                """,
                (_now(), title, title, session_id),
            )

    def load_messages(self, session_id: str, auth_session_id: str) -> dict[str, Any]:
        self._existing_session(session_id, auth_session_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, attachments_json, evidence_json, sources_json,
                       trace_json, steps_json, turn_id, elapsed_ms, created_at
                FROM messages WHERE session_id=? ORDER BY id
                """,
                (session_id,),
            ).fetchall()
            session = conn.execute(
                """
                SELECT id, title, state_json, source, source_label, revision,
                       created_at, updated_at
                FROM sessions WHERE id=? AND auth_session_id=?
                """,
                (session_id, auth_session_id),
            ).fetchone()
        messages = []
        evidence: dict[str, list[Any]] = {}
        sources: list[dict[str, Any]] = []
        for row in rows:
            ev = _json_load(row["evidence_json"], {})
            message_sources = _json_load(row["sources_json"], [])
            msg = {
                "role": row["role"],
                "content": row["content"],
                "attachments": _json_load(row["attachments_json"], []),
                # per-message evidence：前端 inline 面板锚定需要知道每条回答自己的证据
                "evidence": ev if isinstance(ev, dict) else {},
                "sources": message_sources if isinstance(message_sources, list) else [],
                "trace": _json_load(row["trace_json"], []),
                "steps": _json_load(row["steps_json"], []),
                "turn_id": str(row["turn_id"] or ""),
                "elapsed_ms": int(row["elapsed_ms"]) if row["elapsed_ms"] is not None else None,
                "created_at": row["created_at"],
            }
            messages.append(msg)
            if isinstance(ev, dict):
                for key, values in ev.items():
                    if isinstance(values, list):
                        evidence.setdefault(key, []).extend(values)
            if isinstance(message_sources, list):
                sources.extend(x for x in message_sources if isinstance(x, dict))
        return {
            "session": {k: v for k, v in dict(session).items() if k != "state_json"} if session else {"id": session_id},
            "state": _json_load(session["state_json"], {}) if session else {},
            "messages": messages,
            "evidence": evidence,
            "sources": sources[-12:],
        }

    def create_handoff(
        self,
        source_session_id: str,
        source_owner: str,
        username: str,
        *,
        source_label: str = "Discord",
        ttl_seconds: int = 900,
    ) -> str:
        """Create a one-use, identity-bound handoff token for a conversation."""
        self._existing_session(source_session_id, source_owner)
        code = secrets.token_urlsafe(24)
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM session_handoffs WHERE expires_at<?", (now,))
            conn.execute(
                """
                INSERT INTO session_handoffs(
                    code, username, source_session_id, source_owner,
                    source_label, created_at, expires_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    code,
                    username,
                    source_session_id,
                    source_owner,
                    source_label.strip()[:80] or "Discord",
                    now,
                    now + max(60, min(ttl_seconds, 3600)),
                ),
            )
        return code

    def consume_handoff(self, code: str, username: str, target_owner: str) -> dict[str, Any]:
        """Atomically consume a handoff and clone its transcript into web ownership."""
        now = time.time()
        target_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            handoff = conn.execute(
                "SELECT * FROM session_handoffs WHERE code=?",
                (code,),
            ).fetchone()
            if not handoff or float(handoff["expires_at"] or 0) < now:
                if handoff:
                    conn.execute("DELETE FROM session_handoffs WHERE code=?", (code,))
                raise FileNotFoundError("handoff not found or expired")
            if str(handoff["username"]) != username:
                raise PermissionError("handoff identity mismatch")
            source = conn.execute(
                "SELECT * FROM sessions WHERE id=? AND auth_session_id=?",
                (handoff["source_session_id"], handoff["source_owner"]),
            ).fetchone()
            if not source:
                raise FileNotFoundError("source session not found")

            state = _json_load(source["state_json"], {})
            if isinstance(state, dict):
                short_term = state.get("short_term")
                if isinstance(short_term, dict):
                    short_term.pop("discord_identity", None)
                    short_term.pop("attachments", None)
            created_at = _now()
            source_label = str(handoff["source_label"] or "Discord")
            title = f"来自 {source_label} · {str(source['title'] or '对话')}"[:80]
            conn.execute(
                """
                INSERT INTO sessions(
                    id, auth_session_id, title, state_json, source, source_label,
                    revision, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,1,?,?)
                """,
                (
                    target_id,
                    target_owner,
                    title,
                    _json_dump(state if isinstance(state, dict) else {}),
                    "discord_import",
                    source_label,
                    created_at,
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO messages(
                    session_id, role, content, attachments_json,
                    evidence_json, sources_json, trace_json, steps_json,
                    turn_id, elapsed_ms, created_at
                )
                SELECT ?, role, content, attachments_json, evidence_json, sources_json,
                       trace_json, steps_json, turn_id, elapsed_ms, created_at
                FROM messages WHERE session_id=? ORDER BY id
                """,
                (target_id, source["id"]),
            )
            conn.execute("DELETE FROM session_handoffs WHERE code=?", (code,))
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=?",
                (target_id,),
            ).fetchone()[0]
            if not count and isinstance(state, dict):
                # Pre-handoff Discord sessions persisted AgentState only. Keep
                # their readable user/assistant turns available after upgrade.
                for message in state.get("messages") or []:
                    if not isinstance(message, dict):
                        continue
                    role = str(message.get("role") or "")
                    content = str(message.get("content") or "").strip()
                    if role not in {"user", "assistant"} or not content:
                        continue
                    conn.execute(
                        """
                        INSERT INTO messages(
                            session_id, role, content, attachments_json,
                            evidence_json, sources_json, trace_json, steps_json,
                            turn_id, elapsed_ms, created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            target_id, role, content, "[]", "{}", "[]",
                            "[]", "[]", "", None, created_at,
                        ),
                    )
                count = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id=?",
                    (target_id,),
                ).fetchone()[0]
        return {
            "id": target_id,
            "title": title,
            "source": "discord_import",
            "source_label": source_label,
            "message_count": int(count or 0),
            "created_at": created_at,
            "updated_at": created_at,
            "revision": 1,
        }

    def migrate_owner(self, old_owner: str, new_owner: str) -> int:
        """把匿名归属迁给登录身份（cookie 会话 id → user:<username>）。"""
        if not old_owner or not new_owner or old_owner == new_owner:
            return 0
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE sessions SET auth_session_id=?, updated_at=? WHERE auth_session_id=?",
                (new_owner, _now(), old_owner),
            )
        return int(cur.rowcount or 0)

    def cleanup_expired(self, ttl_seconds: int | None = None) -> int:
        ttl = ttl_seconds or settings.session_ttl_seconds
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - max(ttl, 60)))
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,))
            conn.execute("DELETE FROM session_handoffs WHERE expires_at<?", (time.time(),))
        return int(cur.rowcount or 0)
