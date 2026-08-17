"""Process-local background chat runs with replayable SSE event buffers."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import json
import time
from typing import Any, AsyncIterator, Awaitable, Callable


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

    @property
    def terminal(self) -> bool:
        return self.status in {"completed", "cancelled", "failed", "interrupted"}

    async def publish(self, event: str, payload: dict[str, Any] | str) -> ChatRunEvent:
        data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        async with self.condition:
            self.sequence += 1
            item = ChatRunEvent(self.sequence, event, data)
            self.events.append(item)
            self.condition.notify_all()
            return item

    async def mark_terminal(self, status: str, error: str = "") -> None:
        async with self.condition:
            self.status = status
            self.error = error[:500]
            self.finished_at = time.time()
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

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._runs: dict[str, ChatRun] = {}
        self._active_sessions: dict[tuple[str, str], str] = {}

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
            run = ChatRun(run_id, owner, session_id, device_id)
            self._runs[run.id] = run
            self._active_sessions[key] = run.id
            run.task = asyncio.create_task(self._drive(run, worker), name=f"chat-run:{run.id}")
        # Let the driver enter its guarded worker before an immediate cancel can land.
        await asyncio.sleep(0)
        return run

    async def _drive(self, run: ChatRun, worker: RunWorker) -> None:
        run.status = "running"
        try:
            await worker(run)
        except asyncio.CancelledError:
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
