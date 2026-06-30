"""Weekly digest scheduler and inbox writer."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .auth import AuthStore
from .config import settings
from .memory import LongTermMemory
from .memory.consolidate import now_iso
from .notifications import dispatch_weekly_digest_notifications
from .tools.bangumi.client import BangumiClient
from .tools.watchorder.tool import WeeklyDigestArgs, WeeklyDigestTool, _digest_inbox_item


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


class WeeklyDigestService:
    def __init__(
        self,
        ltm: LongTermMemory,
        auth: AuthStore,
        client_factory: Callable[[str, str | None], Any] | None = None,
    ) -> None:
        self.ltm = ltm
        self.auth = auth
        self.client_factory = client_factory
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def run_due_once(self, now: datetime | None = None) -> int:
        """Generate due weekly digests. Returns number of inbox writes."""
        count = 0
        for username in self.ltm.list_users():
            mem = self.ltm.load_user(username)
            sub = mem.weekly_digest_subscription
            if not sub.enabled:
                continue
            local_now = (now or datetime.now(_zone(sub.timezone))).astimezone(_zone(sub.timezone))
            if local_now.weekday() != sub.weekday or local_now.hour != sub.hour:
                continue
            run_key = local_now.strftime("%G-W%V-%u-%H")
            if sub.last_run_key == run_key:
                continue
            token = self.auth.token_for_username(username)
            client = (
                self.client_factory(username, token.access_token if token else None)
                if self.client_factory else BangumiClient(token=token.access_token if token else None)
            )
            try:
                tool = WeeklyDigestTool(client)
                res = await tool.run(
                    WeeklyDigestArgs(
                        username=username,
                        limit=sub.limit,
                        include_on_hold=sub.include_on_hold,
                    )
                )
            finally:
                if hasattr(client, "aclose"):
                    await client.aclose()
            if not res.ok or res.data is None:
                continue
            mem = self.ltm.load_user(username)
            item = _digest_inbox_item(res.data, unread="inbox" in (sub.channels or ["inbox"]))
            deliveries = await dispatch_weekly_digest_notifications(username, sub, item)
            item.payload["deliveries"] = deliveries
            mem.inbox.append(item)
            mem.inbox = mem.inbox[-30:]
            mem.weekly_digest_subscription.last_delivery = deliveries[-8:]
            mem.weekly_digest_subscription.last_run_key = run_key
            mem.weekly_digest_subscription.updated_at = now_iso()
            self.ltm.save_user(mem)
            count += 1
        return count

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_due_once()
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=settings.weekly_scheduler_interval_seconds)
            except TimeoutError:
                continue
