"""Background runs with replayable SSE events and lightweight durability.

Workers intentionally remain process-local.  SQLite only persists ownership,
status and the bounded event log so a browser reconnect (or a service restart)
can explain what happened instead of turning a known run into a 404.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, AsyncIterator, Awaitable, Callable

from .config import settings


class RunStore:
    """Small SQLite journal shared by the chat and recommendation run hubs."""

    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.background_run_store_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS background_runs (
                    namespace TEXT NOT NULL, id TEXT NOT NULL, owner TEXT NOT NULL,
                    session_id TEXT NOT NULL, device_id TEXT NOT NULL,
                    started_at REAL NOT NULL, status TEXT NOT NULL,
                    finished_at REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                    cancel_reason TEXT NOT NULL DEFAULT '', sequence INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(namespace, id)
                );
                CREATE INDEX IF NOT EXISTS idx_background_runs_owner
                    ON background_runs(namespace, owner, started_at DESC);
                CREATE TABLE IF NOT EXISTS background_run_events (
                    namespace TEXT NOT NULL, run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    event TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(namespace, run_id, sequence),
                    FOREIGN KEY(namespace, run_id)
                        REFERENCES background_runs(namespace, id) ON DELETE CASCADE
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def recover(self, namespace: str) -> None:
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id,sequence FROM background_runs
                   WHERE namespace=? AND status IN ('queued','running')""",
                (namespace,),
            ).fetchall()
            for row in rows:
                sequence = int(row["sequence"] or 0) + 1
                conn.execute(
                    """INSERT OR REPLACE INTO background_run_events
                       (namespace,run_id,sequence,event,data) VALUES(?,?,?,?,?)""",
                    (
                        namespace, row["id"], sequence, "interrupted",
                        json.dumps({
                            "type": "interrupted",
                            "message": "服务重启，任务执行已中断",
                        }, ensure_ascii=False),
                    ),
                )
                conn.execute(
                    """UPDATE background_runs SET status='interrupted',finished_at=?,
                       error=CASE WHEN error='' THEN '服务重启，任务执行已中断' ELSE error END,
                       cancel_reason='restart',sequence=? WHERE namespace=? AND id=?""",
                    (now, sequence, namespace, row["id"]),
                )

    def create(self, namespace: str, run: "ChatRun") -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO background_runs
                   (namespace,id,owner,session_id,device_id,started_at,status,finished_at,
                    error,cancel_reason,sequence) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    namespace, run.id, run.owner, run.session_id, run.device_id,
                    run.started_at, run.status, run.finished_at, run.error,
                    run.cancel_reason, run.sequence,
                ),
            )
            conn.execute(
                "DELETE FROM background_run_events WHERE namespace=? AND run_id=?",
                (namespace, run.id),
            )

    def update(self, namespace: str, run: "ChatRun") -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE background_runs SET status=?,finished_at=?,error=?,
                   cancel_reason=?,sequence=? WHERE namespace=? AND id=?""",
                (
                    run.status, run.finished_at, run.error, run.cancel_reason,
                    run.sequence, namespace, run.id,
                ),
            )

    def append_event(self, namespace: str, run: "ChatRun", item: "ChatRunEvent") -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO background_run_events
                   (namespace,run_id,sequence,event,data) VALUES(?,?,?,?,?)""",
                (namespace, run.id, item.sequence, item.event, item.data),
            )
            conn.execute(
                "UPDATE background_runs SET sequence=? WHERE namespace=? AND id=?",
                (item.sequence, namespace, run.id),
            )
            # Match the in-memory deque bound without requiring a SQLite extension.
            conn.execute(
                """DELETE FROM background_run_events WHERE namespace=? AND run_id=?
                   AND sequence <= ?""",
                (namespace, run.id, max(0, item.sequence - 1200)),
            )

    def load(self, namespace: str, run_id: str) -> "ChatRun | None":
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM background_runs WHERE namespace=? AND id=?",
                (namespace, run_id),
            ).fetchone()
            if row is None:
                return None
            events = conn.execute(
                """SELECT sequence,event,data FROM background_run_events
                   WHERE namespace=? AND run_id=? ORDER BY sequence""",
                (namespace, run_id),
            ).fetchall()
        run = ChatRun(
            id=str(row["id"]), owner=str(row["owner"]),
            session_id=str(row["session_id"]), device_id=str(row["device_id"]),
            started_at=float(row["started_at"]), status=str(row["status"]),
            finished_at=float(row["finished_at"]), error=str(row["error"]),
            cancel_reason=str(row["cancel_reason"]), sequence=int(row["sequence"]),
        )
        run.events.extend(
            ChatRunEvent(int(event["sequence"]), str(event["event"]), str(event["data"]))
            for event in events
        )
        return run

    def recent(self, namespace: str, owner: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM background_runs WHERE namespace=?"
        params: list[Any] = [namespace]
        if owner is not None:
            query += " AND owner=?"
            params.append(owner)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def cleanup(self, namespace: str, cutoff: float) -> None:
        with self._connect() as conn:
            conn.execute(
                """DELETE FROM background_runs WHERE namespace=? AND finished_at>0
                   AND finished_at<?""",
                (namespace, cutoff),
            )


@dataclass(slots=True)
class ChatRunEvent:
    sequence: int
    event: str
    data: str


@dataclass(slots=True)
class ChatRun:
    id: str
    owner: str
    session_id: str
    device_id: str
    started_at: float = field(default_factory=time.time)
    status: str = "queued"
    finished_at: float = 0.0
    error: str = ""
    cancel_reason: str = ""
    sequence: int = 0
    events: deque[ChatRunEvent] = field(default_factory=lambda: deque(maxlen=1200))
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None
    _persist_event: Callable[["ChatRun", ChatRunEvent], None] | None = field(
        default=None, repr=False
    )
    _persist_state: Callable[["ChatRun"], None] | None = field(default=None, repr=False)

    @property
    def terminal(self) -> bool:
        return self.status in {"completed", "cancelled", "failed", "interrupted"}

    async def publish(self, event: str, payload: dict[str, Any] | str) -> ChatRunEvent:
        data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        async with self.condition:
            self.sequence += 1
            item = ChatRunEvent(self.sequence, event, data)
            self.events.append(item)
            if self._persist_event is not None:
                self._persist_event(self, item)
            self.condition.notify_all()
            return item

    async def mark_terminal(self, status: str, error: str = "") -> None:
        async with self.condition:
            self.status = status
            self.error = error[:500]
            self.finished_at = time.time()
            if self._persist_state is not None:
                self._persist_state(self)
            self.condition.notify_all()

    async def stream(self, after: int = 0) -> AsyncIterator[ChatRunEvent | None]:
        cursor = max(0, int(after))
        while True:
            pending: list[ChatRunEvent] = []
            terminal = False
            async with self.condition:
                pending = [event for event in self.events if event.sequence > cursor]
                terminal = self.terminal
                if not pending and not terminal:
                    try:
                        await asyncio.wait_for(self.condition.wait(), timeout=15)
                    except TimeoutError:
                        pass
                    pending = [event for event in self.events if event.sequence > cursor]
                    terminal = self.terminal
            if pending:
                for event in pending:
                    cursor = event.sequence
                    yield event
                continue
            if terminal:
                return
            yield None


RunWorker = Callable[[ChatRun], Awaitable[None]]


class ChatRunHub:
    """Keep chat execution alive when a browser stops consuming its event stream."""

    def __init__(self, path: str | None = None, namespace: str = "chat") -> None:
        self._guard = asyncio.Lock()
        self._runs: dict[str, ChatRun] = {}
        self._active_sessions: dict[tuple[str, str], str] = {}
        self.namespace = namespace
        self.store = RunStore(path)
        self.store.recover(namespace)

    def _attach(self, run: ChatRun) -> ChatRun:
        run._persist_event = lambda current, event: self.store.append_event(
            self.namespace, current, event
        )
        run._persist_state = lambda current: self.store.update(self.namespace, current)
        return run

    async def start(
        self,
        run_id: str,
        owner: str,
        session_id: str,
        device_id: str,
        worker: RunWorker,
    ) -> ChatRun:
        key = (owner, session_id)
        async with self._guard:
            current_id = self._active_sessions.get(key)
            current = self._runs.get(current_id or "")
            if current is not None and not current.terminal:
                raise RuntimeError("session already has an active chat run")
            run = self._attach(ChatRun(run_id, owner, session_id, device_id))
            self._runs[run.id] = run
            self._active_sessions[key] = run.id
            self.store.create(self.namespace, run)
            run.task = asyncio.create_task(self._drive(run, worker), name=f"chat-run:{run.id}")
        # Let the driver enter its guarded worker before an immediate cancel can land.
        await asyncio.sleep(0)
        return run

    async def _drive(self, run: ChatRun, worker: RunWorker) -> None:
        run.status = "running"
        self.store.update(self.namespace, run)
        try:
            await worker(run)
        except asyncio.CancelledError:
            if run.cancel_reason == "shutdown":
                message = "服务关闭，任务执行已中断"
                await run.publish("interrupted", {"type": "interrupted", "message": message})
                await run.mark_terminal("interrupted", message)
            else:
                await run.mark_terminal("cancelled")
        except Exception as exc:  # noqa: BLE001 - the run must expose failure without killing the app
            message = f"{type(exc).__name__}: {str(exc)[:400]}"
            await run.publish("error", {"type": "error", "message": message})
            await run.mark_terminal("failed", message)
        else:
            await run.mark_terminal("completed")
        finally:
            async with self._guard:
                key = (run.owner, run.session_id)
                if self._active_sessions.get(key) == run.id:
                    self._active_sessions.pop(key, None)

    async def get(self, owner: str, run_id: str) -> ChatRun | None:
        async with self._guard:
            run = self._runs.get(run_id)
            if run is None:
                loaded = self.store.load(self.namespace, run_id)
                if loaded is not None:
                    run = self._attach(loaded)
                    self._runs[run_id] = run
            return run if run is not None and run.owner == owner else None

    async def active_for_session(self, owner: str, session_id: str) -> ChatRun | None:
        async with self._guard:
            run_id = self._active_sessions.get((owner, session_id), "")
            run = self._runs.get(run_id)
            return run if run is not None and not run.terminal else None

    async def cancel(self, owner: str, run_id: str, reason: str = "user") -> bool:
        run = await self.get(owner, run_id)
        if run is None or run.terminal or run.task is None:
            return False
        run.cancel_reason = reason
        run.task.cancel()
        return True

    async def cleanup(self, max_age_seconds: float = 3600) -> None:
        cutoff = time.time() - max(60.0, max_age_seconds)
        async with self._guard:
            stale = [
                run_id for run_id, run in self._runs.items()
                if run.terminal and run.finished_at and run.finished_at < cutoff
            ]
            for run_id in stale:
                self._runs.pop(run_id, None)
        self.store.cleanup(self.namespace, cutoff)

    async def recent(self, owner: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        async with self._guard:
            return self.store.recent(self.namespace, owner, limit)

    async def shutdown(self) -> None:
        async with self._guard:
            runs = [
                run for run in self._runs.values()
                if run.task is not None and not run.task.done()
            ]
        tasks = []
        for run in runs:
            run.cancel_reason = "shutdown"
            run.task.cancel()
            tasks.append(run.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
