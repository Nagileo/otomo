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
from .tools.calendar.tool import AiringProgressArgs, AiringProgressTool
from .tools.release.tool import fetch_release_items_from_url
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


def _daily_inbox_item(username: str, payload: dict[str, Any], *, unread: bool = True) -> Any:
    from .memory.models import InboxItem
    import secrets

    return InboxItem(
        id=secrets.token_urlsafe(14),
        kind="daily_airing",
        title=f"{payload.get('date') or ''} 每日追番提醒".strip(),
        payload=payload,
        unread=unread,
        created_at=now_iso(),
    )


class DailyAiringService:
    """Daily airing/RSS reminder service.

    It intentionally reuses `WeeklyDigestSubscription.channels` as the delivery
    configuration so users do not need to configure another channel matrix.
    """

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

    async def _rss_updates(self, mem) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        for plan in mem.watch_plan:
            if not plan.rss_url:
                continue
            try:
                items = await fetch_release_items_from_url(plan.rss_url, "watch_plan_rss")
            except Exception:  # noqa: BLE001
                continue
            items.sort(key=lambda x: x.pub_date, reverse=True)
            latest = items[0] if items else None
            if not latest:
                continue
            if plan.last_seen_pub_date and latest.pub_date <= plan.last_seen_pub_date:
                continue
            updates.append({
                "subject_id": plan.subject_id,
                "name": plan.name,
                "subgroup": plan.subgroup or latest.subgroup,
                "rss_url": plan.rss_url,
                "title": latest.title,
                "url": latest.page_url or latest.torrent_url or latest.magnet,
                "pub_date": latest.pub_date,
                "quality": latest.quality,
                "source": latest.source,
            })
            plan.last_seen_pub_date = latest.pub_date
            plan.updated_at = now_iso()
        return updates

    async def run_due_once(self, now: datetime | None = None) -> int:
        if not settings.daily_airing_enabled:
            return 0
        count = 0
        tz = _zone(settings.daily_airing_timezone)
        local_now = (now or datetime.now(tz)).astimezone(tz)
        if local_now.hour != settings.daily_airing_hour:
            return 0
        run_key = local_now.strftime("%Y-%m-%d-%H")
        for username in self.ltm.list_users():
            mem = self.ltm.load_user(username)
            sub = mem.weekly_digest_subscription
            if sub.daily_last_run_key == run_key:
                continue
            if not sub.enabled and not any(plan.rss_url for plan in mem.watch_plan):
                continue
            token = self.auth.token_for_username(username)
            client = (
                self.client_factory(username, token.access_token if token else None)
                if self.client_factory else BangumiClient(token=token.access_token if token else None)
            )
            try:
                airing = await AiringProgressTool(client).run(
                    AiringProgressArgs(username=username, include_wishlist=True, limit=20)
                )
            finally:
                if hasattr(client, "aclose"):
                    await client.aclose()
            rss_updates = await self._rss_updates(mem)
            airing_items = airing.data.items if airing.ok and airing.data else []
            payload = {
                "username": username,
                "date": local_now.date().isoformat(),
                "sections": [
                    {
                        "title": "今日追番进度",
                        "items": [x.model_dump(mode="json", exclude_none=True) for x in airing_items[:8]],
                        "notes": ["基于 Bangumi 正片 airdate 与 ep_status；国内平台上架可能有时差。"],
                    },
                    {
                        "title": "RSS 新资源",
                        "items": rss_updates[:12],
                        "notes": ["只检查计划板 rss_url；Otomo 不下载、不托管资源。"],
                    },
                ],
                "next_actions": [
                    "如果某部番确定追，可以把 release RSS 写入计划板，用每日提醒检查更新。",
                    "如果需要正版观看入口，询问“这部番在哪看”。",
                ],
                "caveats": [
                    "每日提醒需要后台 worker 常驻运行；本地电脑关机后不会推送。",
                ],
            }
            if not airing_items and not rss_updates:
                mem.weekly_digest_subscription.daily_last_run_key = run_key
                mem.weekly_digest_subscription.updated_at = now_iso()
                self.ltm.save_user(mem)
                continue
            item = _daily_inbox_item(username, payload, unread="inbox" in (sub.channels or ["inbox"]))
            deliveries = await dispatch_weekly_digest_notifications(username, sub, item)
            item.payload["deliveries"] = deliveries
            mem.inbox.append(item)
            mem.inbox = mem.inbox[-30:]
            mem.weekly_digest_subscription.last_delivery = deliveries[-8:]
            mem.weekly_digest_subscription.daily_last_run_key = run_key
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
                await asyncio.wait_for(self._stop.wait(), timeout=settings.daily_airing_interval_seconds)
            except TimeoutError:
                continue
