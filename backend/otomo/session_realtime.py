"""Process-local realtime activity leases and owner-scoped session events."""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator


@dataclass(slots=True)
class SessionActivity:
    owner: str
    session_id: str
    request_id: str
    device_id: str
    surface: str
    started_at: float


class SessionRealtimeHub:
    """Coordinate concurrent writers and notify browsers connected to one API process."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._activities: dict[tuple[str, str], SessionActivity] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._sequence = 0

    def _event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self._sequence += 1
        return {"type": event_type, "sequence": self._sequence, **payload}

    def _broadcast(self, owner: str, event: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers.get(owner, set())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    async def claim(
        self,
        owner: str,
        session_id: str,
        request_id: str,
        device_id: str,
        *,
        surface: str = "web",
    ) -> tuple[bool, SessionActivity]:
        key = (owner, session_id)
        async with self._guard:
            current = self._activities.get(key)
            if current is not None:
                return False, current
            activity = SessionActivity(
                owner=owner,
                session_id=session_id,
                request_id=request_id,
                device_id=device_id,
                surface=surface,
                started_at=time.time(),
            )
            self._activities[key] = activity
            self._broadcast(owner, self._event("session_activity", **asdict(activity), running=True))
            return True, activity

    async def release(self, activity: SessionActivity) -> None:
        key = (activity.owner, activity.session_id)
        async with self._guard:
            current = self._activities.get(key)
            if current is None or current.request_id != activity.request_id:
                return
            self._activities.pop(key, None)
            self._broadcast(
                activity.owner,
                self._event(
                    "session_activity",
                    session_id=activity.session_id,
                    request_id=activity.request_id,
                    device_id=activity.device_id,
                    surface=activity.surface,
                    started_at=activity.started_at,
                    running=False,
                ),
            )

    async def notify(self, owner: str, event_type: str, **payload: Any) -> None:
        async with self._guard:
            self._broadcast(owner, self._event(event_type, **payload))

    async def decorate_sessions(
        self,
        owner: str,
        sessions: list[dict[str, Any]],
        device_id: str = "",
    ) -> list[dict[str, Any]]:
        async with self._guard:
            decorated: list[dict[str, Any]] = []
            for row in sessions:
                item = dict(row)
                activity = self._activities.get((owner, str(row.get("id") or "")))
                item["running"] = activity is not None
                if activity is not None:
                    item["activity_surface"] = activity.surface
                    item["activity_started_at"] = activity.started_at
                    item["activity_is_current_device"] = bool(
                        device_id and activity.device_id == device_id
                    )
                decorated.append(item)
            return decorated

    async def activity(self, owner: str, session_id: str) -> SessionActivity | None:
        async with self._guard:
            return self._activities.get((owner, session_id))

    async def stream(self, owner: str, device_id: str) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        async with self._guard:
            self._subscribers.setdefault(owner, set()).add(queue)
            activities = [
                {
                    **asdict(activity),
                    "running": True,
                    "activity_is_current_device": bool(
                        device_id and activity.device_id == device_id
                    ),
                }
                for (activity_owner, _), activity in self._activities.items()
                if activity_owner == owner
            ]
            initial = self._event("session_sync", activities=activities)
        try:
            yield initial
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    enriched = dict(event)
                    actor_device = str(event.get("device_id") or "")
                    if actor_device:
                        enriched["activity_is_current_device"] = bool(
                            device_id and actor_device == device_id
                        )
                    yield enriched
                except TimeoutError:
                    yield self._event("ping", at=time.time())
        finally:
            async with self._guard:
                subscribers = self._subscribers.get(owner)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(owner, None)
