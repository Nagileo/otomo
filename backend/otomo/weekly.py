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
from .tools._concurrency import gather_limited
from .tools.bangumi.client import BangumiClient
from .tools.calendar.tool import AiringProgressArgs, AiringProgressTool
from .tools.discovery.tool import EpisodeBuzzRadarTool, EpisodeRadarArgs
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
            item.payload["push_grading"] = sub.push_grading
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

    async def _birthday_section(self) -> dict[str, Any] | None:
        """今日生日（AniList 图卡 + 萌娘名单）——所有用户共享，循环外取一次（内部有缓存）。"""
        try:
            from .tools.discovery.tool import BirthdayArgs, GetCharacterBirthdaysTool

            res = await GetCharacterBirthdaysTool().run(BirthdayArgs(limit=6, moegirl_limit=12))
        except Exception:  # noqa: BLE001
            return None
        if not res.ok or res.data is None or not res.data.count:
            return None
        rows = [
            {"name": c.name_native or c.name, "title": c.from_media, "url": c.anilist_url or c.bangumi_search_url}
            for c in res.data.characters[:6]
        ]
        rows.extend(
            {"name": m.name, "title": m.from_media, "url": m.url}
            for m in res.data.moegirl_entries[:8]
        )
        return {
            "title": "今日生日",
            "items": rows,
            "notes": ["来自 AniList 人气榜与萌娘百科生日分类；两源口径不同可能有重复。"],
        }

    async def _episode_radar_section(
        self,
        client: BangumiClient,
        airing_items: list[Any],
    ) -> dict[str, Any] | None:
        targets = [x for x in airing_items if getattr(x, "id", None)][:4]
        if not targets:
            return None
        radar_tool = EpisodeBuzzRadarTool(client)
        jobs = [
            radar_tool.run(
                EpisodeRadarArgs(
                    subject_id=x.id,
                    progress_episode=getattr(x, "my_ep", None) or None,
                    top=3,
                    with_summary=False,
                )
            )
            for x in targets
        ]
        results = await gather_limited(jobs, host="bangumi")
        rows: list[dict[str, Any]] = []
        for item, res in zip(targets, results, strict=False):
            if isinstance(res, Exception) or not res.ok or not res.data:
                continue
            peaks = [
                {
                    "episode": p.ep or p.sort,
                    "sort": p.sort,
                    "comments": p.comments,
                    "airdate": p.airdate,
                    "name": p.name,
                }
                for p in res.data.peaks[:3]
                if p.comments > 0
            ]
            if not peaks:
                continue
            rows.append({
                "subject_id": item.id,
                "name": item.name,
                "my_ep": getattr(item, "my_ep", 0),
                "peaks": peaks,
                "why": [f"分集讨论峰值 {peaks[0]['comments']} 条，供追番节奏参考"],
            })
        if not rows:
            return None
        return {
            "title": "分集热度雷达",
            "items": rows,
            "notes": ["讨论数是话题度，不等于质量；已按你的当前进度过滤后续集。"],
        }

    async def run_due_once(self, now: datetime | None = None) -> int:
        if not settings.daily_airing_enabled:
            return 0
        count = 0
        birthday_section = await self._birthday_section()
        for username in self.ltm.list_users():
            mem = self.ltm.load_user(username)
            sub = mem.weekly_digest_subscription
            user_tz = _zone(sub.daily_timezone or settings.daily_airing_timezone)
            local_now = (now or datetime.now(user_tz)).astimezone(user_tz)
            if local_now.hour != (sub.daily_hour if sub.daily_enabled else settings.daily_airing_hour):
                continue
            run_key = local_now.strftime("%Y-%m-%d-%H")
            if sub.daily_last_run_key == run_key:
                continue
            if not sub.daily_enabled and not any(plan.rss_url for plan in mem.watch_plan):
                continue
            token = self.auth.token_for_username(username)
            client = (
                self.client_factory(username, token.access_token if token else None)
                if self.client_factory else BangumiClient(token=token.access_token if token else None)
            )
            airing_items: list[Any] = []
            radar_section: dict[str, Any] | None = None
            try:
                airing = await AiringProgressTool(client).run(
                    AiringProgressArgs(username=username, include_wishlist=True, limit=20)
                )
                airing_items = airing.data.items if airing.ok and airing.data else []
                radar_section = await self._episode_radar_section(client, airing_items)
            finally:
                if hasattr(client, "aclose"):
                    await client.aclose()
            rss_updates = await self._rss_updates(mem)
            payload = {
                "username": username,
                "date": local_now.date().isoformat(),
                "push_grading": sub.push_grading,
                "sections": [
                    {
                        "title": "今日追番进度",
                        "items": [x.model_dump(mode="json", exclude_none=True) for x in airing_items[:8]],
                        "notes": ["基于 Bangumi 正片 airdate 与 ep_status；国内平台上架可能有时差。"],
                    },
                    *([radar_section] if radar_section else []),
                    {
                        "title": "RSS 新资源",
                        "items": rss_updates[:12],
                        "notes": ["只检查计划板 rss_url；Otomo 不下载、不托管资源。"],
                    },
                    *([birthday_section] if birthday_section else []),
                ],
                "next_actions": [
                    "如果某部番确定追，可以把 release RSS 写入计划板，用每日提醒检查更新。",
                    "如果需要正版观看入口，询问“这部番在哪看”。",
                ],
                "caveats": [
                    "每日提醒需要后台 worker 常驻运行；本地电脑关机后不会推送。",
                ],
            }
            if not airing_items and not rss_updates and not radar_section:
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
