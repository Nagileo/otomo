"""Unified active subscription rules.

This complements the older weekly-digest settings kept in long-term memory.
Rules here are product-level subscriptions: kind + filters + schedule +
channels + delivery records.  They are intentionally independent from chat
sessions so they can be moved to a server worker later.
"""
from __future__ import annotations

import asyncio
from calendar import monthrange
from datetime import datetime, timedelta
import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from .auth import AuthStore
from .config import settings
from .memory import LongTermMemory
from .memory.consolidate import now_iso
from .memory.models import InboxItem, WeeklyDigestSubscription
from .notifications import dispatch_weekly_digest_notifications
from .tools._concurrency import gather_limited
from .tools.bangumi.client import BangumiClient
from .tools.calendar.tool import AiringProgressArgs, AiringProgressTool
from .tools.discovery.tool import BirthdayArgs, EpisodeBuzzRadarTool, EpisodeRadarArgs, GetCharacterBirthdaysTool
from .tools.product_loop.tool import MonthlyWatchReportArgs, MonthlyWatchReportTool
from .tools.release.tool import fetch_release_items_from_url
from .tools.videos.tool import BiliGuideSearchArgs, SearchBiliGuideVideosTool
from .tools.watchorder.tool import WeeklyDigestArgs, WeeklyDigestTool, _digest_inbox_item

SubscriptionKind = Literal[
    "weekly_digest",
    "daily_airing",
    "monthly_report",
    "rss_release",
    "birthday",
    "bili_up_video",
    "rating_alert",
]
SubscriptionChannel = Literal["inbox", "email", "webhook"]
SubscriptionTemplate = Literal["brief", "normal", "detailed"]
DeliveryStatus = Literal["pending", "sent", "skipped", "failed"]
WebhookFormat = Literal["generic", "serverchan", "telegram", "discord", "feishu"]


class SubscriptionSchedule(BaseModel):
    timezone: str = "Asia/Shanghai"
    hour: int = Field(9, ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)
    weekday: int | None = Field(None, ge=0, le=6, description="0=Monday; None means daily/monthly depending kind")
    day_of_month: int | None = Field(None, ge=1, le=31)
    interval_minutes: int | None = Field(None, ge=5, le=10080)


class QuietHours(BaseModel):
    start: str = "23:00"
    end: str = "08:00"


class SubscriptionRule(BaseModel):
    id: str
    owner_key: str
    username: str = ""
    kind: SubscriptionKind
    enabled: bool = True
    title: str = ""
    filters: dict[str, Any] = Field(default_factory=dict)
    schedule: SubscriptionSchedule = Field(default_factory=SubscriptionSchedule)
    channels: list[SubscriptionChannel] = Field(default_factory=lambda: ["inbox"])
    template: SubscriptionTemplate = "normal"
    webhook_format: WebhookFormat = "generic"
    webhook_url: str = ""
    email: str = ""
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    last_run_at: str = ""
    last_hit_key: str = ""
    created_at: str = ""
    updated_at: str = ""


class CreateSubscriptionRuleRequest(BaseModel):
    kind: SubscriptionKind
    title: str = ""
    enabled: bool = True
    filters: dict[str, Any] = Field(default_factory=dict)
    schedule: SubscriptionSchedule = Field(default_factory=SubscriptionSchedule)
    channels: list[SubscriptionChannel] = Field(default_factory=lambda: ["inbox"])
    template: SubscriptionTemplate = "normal"
    webhook_format: WebhookFormat = "generic"
    webhook_url: str = ""
    email: str = ""
    quiet_hours: QuietHours = Field(default_factory=QuietHours)


class UpdateSubscriptionRuleRequest(BaseModel):
    enabled: bool | None = None
    title: str | None = None
    filters: dict[str, Any] | None = None
    schedule: SubscriptionSchedule | None = None
    channels: list[SubscriptionChannel] | None = None
    template: SubscriptionTemplate | None = None
    webhook_format: WebhookFormat | None = None
    webhook_url: str | None = None
    email: str | None = None
    quiet_hours: QuietHours | None = None


class DeliveryRecord(BaseModel):
    id: str
    rule_id: str
    owner_key: str
    kind: str
    hit_key: str = ""
    status: DeliveryStatus = "pending"
    title: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    deliveries: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""
    created_at: str = ""


class SubscriptionStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.subscription_store_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subscription_rules (
                    id TEXT PRIMARY KEY,
                    owner_key TEXT NOT NULL,
                    username TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    title TEXT NOT NULL DEFAULT '',
                    filters_json TEXT NOT NULL DEFAULT '{}',
                    schedule_json TEXT NOT NULL DEFAULT '{}',
                    channels_json TEXT NOT NULL DEFAULT '["inbox"]',
                    template TEXT NOT NULL DEFAULT 'normal',
                    webhook_format TEXT NOT NULL DEFAULT 'generic',
                    webhook_url TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    quiet_hours_json TEXT NOT NULL DEFAULT '{}',
                    last_run_at TEXT NOT NULL DEFAULT '',
                    last_hit_key TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subscription_deliveries (
                    id TEXT PRIMARY KEY,
                    rule_id TEXT NOT NULL,
                    owner_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    hit_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    deliveries_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_rules_owner ON subscription_rules(owner_key, updated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_rules_enabled ON subscription_rules(enabled, kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_deliveries_rule ON subscription_deliveries(rule_id, created_at DESC)")

    def create(self, req: CreateSubscriptionRuleRequest, *, owner_key: str, username: str = "") -> SubscriptionRule:
        now = now_iso()
        rule = SubscriptionRule(
            id=f"sub_{secrets.token_urlsafe(16).replace('-', '').replace('_', '')[:22]}",
            owner_key=owner_key,
            username=username,
            kind=req.kind,
            enabled=req.enabled,
            title=req.title.strip()[:120] or default_subscription_title(req.kind),
            filters=req.filters,
            schedule=req.schedule,
            channels=_normalize_channels(req.channels),
            template=req.template,
            webhook_format=req.webhook_format,
            webhook_url=req.webhook_url.strip(),
            email=req.email.strip(),
            quiet_hours=req.quiet_hours,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subscription_rules(
                    id,owner_key,username,kind,enabled,title,filters_json,schedule_json,channels_json,
                    template,webhook_format,webhook_url,email,quiet_hours_json,last_run_at,last_hit_key,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                _rule_values(rule),
            )
        return rule

    def list_rules(self, owner_key: str, limit: int = 100) -> list[SubscriptionRule]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM subscription_rules WHERE owner_key=? ORDER BY updated_at DESC LIMIT ?",
                (owner_key, max(1, min(limit, 200))),
            ).fetchall()
        return [_row_to_rule(row) for row in rows]

    def list_enabled_rules(self) -> list[SubscriptionRule]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM subscription_rules WHERE enabled=1").fetchall()
        return [_row_to_rule(row) for row in rows]

    def get(self, rule_id: str, owner_key: str | None = None) -> SubscriptionRule | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM subscription_rules WHERE id=?", (rule_id,)).fetchone()
        if not row:
            return None
        rule = _row_to_rule(row)
        if owner_key is not None and rule.owner_key != owner_key:
            return None
        return rule

    def update(self, rule_id: str, owner_key: str, req: UpdateSubscriptionRuleRequest) -> SubscriptionRule | None:
        rule = self.get(rule_id, owner_key)
        if not rule:
            return None
        updates = req.model_dump(exclude_unset=True)
        for key, value in updates.items():
            if value is None:
                continue
            if key == "channels":
                value = _normalize_channels(value)
            elif isinstance(value, str):
                value = value.strip()
            setattr(rule, key, value)
        rule.updated_at = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE subscription_rules SET
                    enabled=?, title=?, filters_json=?, schedule_json=?, channels_json=?,
                    template=?, webhook_format=?, webhook_url=?, email=?, quiet_hours_json=?,
                    last_run_at=?, last_hit_key=?, updated_at=?
                WHERE id=? AND owner_key=?
                """,
                (
                    1 if rule.enabled else 0,
                    rule.title,
                    _dump(rule.filters),
                    _dump(rule.schedule.model_dump(mode="json")),
                    _dump(rule.channels),
                    rule.template,
                    rule.webhook_format,
                    rule.webhook_url,
                    rule.email,
                    _dump(rule.quiet_hours.model_dump(mode="json")),
                    rule.last_run_at,
                    rule.last_hit_key,
                    rule.updated_at,
                    rule.id,
                    owner_key,
                ),
            )
        return rule

    def delete(self, rule_id: str, owner_key: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM subscription_rules WHERE id=? AND owner_key=?", (rule_id, owner_key))
        return bool(cur.rowcount)

    def touch_run(self, rule: SubscriptionRule, hit_key: str) -> SubscriptionRule:
        rule.last_run_at = now_iso()
        rule.last_hit_key = hit_key
        rule.updated_at = rule.last_run_at
        with self._connect() as conn:
            conn.execute(
                "UPDATE subscription_rules SET last_run_at=?, last_hit_key=?, updated_at=? WHERE id=?",
                (rule.last_run_at, rule.last_hit_key, rule.updated_at, rule.id),
            )
        return rule

    def add_delivery(self, record: DeliveryRecord) -> DeliveryRecord:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subscription_deliveries(
                    id,rule_id,owner_key,kind,hit_key,status,title,payload_json,deliveries_json,error,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.id,
                    record.rule_id,
                    record.owner_key,
                    record.kind,
                    record.hit_key,
                    record.status,
                    record.title,
                    _dump(record.payload),
                    _dump(record.deliveries),
                    record.error,
                    record.created_at,
                ),
            )
        return record

    def list_deliveries(self, owner_key: str, rule_id: str | None = None, limit: int = 80) -> list[DeliveryRecord]:
        query = "SELECT * FROM subscription_deliveries WHERE owner_key=?"
        params: list[Any] = [owner_key]
        if rule_id:
            query += " AND rule_id=?"
            params.append(rule_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [_row_to_delivery(row) for row in rows]


class SubscriptionService:
    def __init__(
        self,
        store: SubscriptionStore,
        ltm: LongTermMemory,
        auth: AuthStore,
        client_factory: Callable[[str, str | None], Any] | None = None,
    ) -> None:
        self.store = store
        self.ltm = ltm
        self.auth = auth
        self.client_factory = client_factory
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def run_due_once(self, now: datetime | None = None) -> int:
        count = 0
        for rule in self.store.list_enabled_rules():
            if not is_rule_due(rule, now=now):
                continue
            record = await self.run_rule(rule, test=False)
            if record.status == "sent":
                count += 1
        return count

    async def run_rule(self, rule: SubscriptionRule, *, test: bool = False) -> DeliveryRecord:
        hit_key = due_hit_key(rule) if not test else f"test-{now_iso()}"
        if not test and hit_key and rule.last_hit_key == hit_key:
            return self._record(rule, hit_key, "skipped", title=rule.title, payload={"reason": "duplicate hit_key"})
        try:
            payload = await self._materialize(rule, test=test)
            if not _payload_has_content(payload):
                if not test:
                    self.store.touch_run(rule, hit_key)
                return self._record(rule, hit_key, "skipped", title=rule.title, payload={"reason": "empty payload", **payload})
            item = InboxItem(
                id=secrets.token_urlsafe(14),
                kind=_inbox_kind(rule.kind),
                title=rule.title or default_subscription_title(rule.kind),
                payload={**payload, "subscription_id": rule.id, "subscription_kind": rule.kind, "push_grading": rule.template, "test": test},
                unread=("inbox" in rule.channels) and not test,
                created_at=now_iso(),
            )
            sub = weekly_subscription_from_rule(rule)
            if test:
                sub.channels = [c for c in sub.channels if c != "inbox"]
            deliveries = (
                await dispatch_weekly_digest_notifications(rule.username or rule.owner_key, sub, item)
                if sub.channels else [{"channel": "test", "ok": True, "note": "no external channels configured", "ts": now_iso()}]
            )
            if "inbox" in rule.channels and rule.username and not test:
                mem = self.ltm.load_user(rule.username)
                mem.inbox.append(item)
                mem.inbox = mem.inbox[-60:]
                self.ltm.save_user(mem)
            if not test:
                self.store.touch_run(rule, hit_key)
            return self._record(rule, hit_key, "sent", title=item.title, payload=item.payload, deliveries=deliveries)
        except Exception as e:  # noqa: BLE001
            if not test:
                self.store.touch_run(rule, hit_key)
            return self._record(rule, hit_key, "failed", title=rule.title, error=f"{type(e).__name__}: {str(e)[:240]}")

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_due_once()
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=settings.subscription_scheduler_interval_seconds)
            except TimeoutError:
                continue

    async def _materialize(self, rule: SubscriptionRule, *, test: bool = False) -> dict[str, Any]:
        if rule.kind == "birthday":
            res = await GetCharacterBirthdaysTool().run(BirthdayArgs(limit=int(rule.filters.get("limit") or 8), moegirl_limit=16))
            if not res.ok or not res.data:
                return {"sections": [], "caveats": [res.error or "birthday source unavailable"]}
            return {
                "sections": [
                    {
                        "title": "今日生日",
                        "items": [
                            x.model_dump(mode="json", exclude_none=True) for x in res.data.characters[:8]
                        ] + [
                            x.model_dump(mode="json", exclude_none=True) for x in res.data.moegirl_entries[:12]
                        ],
                        "notes": res.data.caveats,
                    }
                ],
            }
        token = self.auth.token_for_username(rule.username) if rule.username else None
        client = self.client_factory(rule.username, token.access_token if token else None) if self.client_factory else BangumiClient(token=token.access_token if token else None)
        try:
            if rule.kind == "monthly_report":
                today = datetime.now(_zone(rule.schedule.timezone))
                res = await MonthlyWatchReportTool(client).run(
                    MonthlyWatchReportArgs(
                        username=rule.username or None,
                        year=int(rule.filters.get("year") or today.year),
                        month=int(rule.filters.get("month") or today.month),
                        subject_type=rule.filters.get("subject_type") or "anime",
                        limit=int(rule.filters.get("limit") or 12),
                    )
                )
                return res.data.model_dump(mode="json", exclude_none=True) if res.ok and res.data else {"sections": [], "caveats": [res.error or "monthly report failed"]}
            if rule.kind == "weekly_digest":
                res = await WeeklyDigestTool(client).run(
                    WeeklyDigestArgs(
                        username=rule.username or None,
                        limit=int(rule.filters.get("limit") or 8),
                        include_on_hold=bool(rule.filters.get("include_on_hold", True)),
                    )
                )
                if res.ok and res.data:
                    return _digest_inbox_item(res.data).payload
                return {"sections": [], "caveats": [res.error or "weekly digest failed"]}
            if rule.kind == "daily_airing":
                return await self._daily_airing_payload(rule, client, mutate=not test)
            if rule.kind == "rss_release":
                return await self._rss_payload(rule)
            if rule.kind == "bili_up_video":
                return await self._bili_payload(rule)
            if rule.kind == "rating_alert":
                return await self._rating_alert_payload(rule, client)
            raise ValueError(f"unsupported subscription kind: {rule.kind}")
        finally:
            if hasattr(client, "aclose"):
                await client.aclose()

    async def _rating_alert_payload(self, rule: SubscriptionRule, client: Any) -> dict[str, Any]:
        """口碑哨兵：我的在看/想看条目命中 netaba.re 近30天涨跌榜时提醒。
        docs/19 曾把"即时告警"划为不做——这是**日报级**的克制版，归入现有订阅节奏。"""
        from .tools.netabare.tool import RatingMoversArgs, RatingMoversTool

        username = rule.username
        if not username:
            return {"sections": [], "caveats": ["rating_alert 需要绑定用户名"]}
        movers = await RatingMoversTool().run(RatingMoversArgs(direction="all", limit=10))
        if not movers.ok or movers.data is None:
            return {"sections": [], "caveats": [movers.error or "涨跌榜获取失败"]}
        items = await client.get_all_user_collections(username, 2, None, max_items=1000)
        mine: dict[int, str] = {}
        _STATUS = {1: "想看", 2: "看过", 3: "在看", 4: "搁置"}
        watch_types = set(rule.filters.get("watch_types") or [1, 3])  # 默认盯 想看+在看
        for item in items:
            subj = item.get("subject") or {}
            sid = subj.get("id")
            if sid and int(item.get("type") or 0) in watch_types:
                mine[int(sid)] = _STATUS.get(int(item.get("type") or 0), "")
        lines: list[dict[str, Any]] = []
        for board, label, emoji in ((movers.data.up, "评分上涨", "📈"), (movers.data.down, "评分下跌", "📉")):
            for m in board:
                if m.subject_id in mine:
                    lines.append({
                        "id": m.subject_id,
                        "name": m.title,
                        "summary": f"{emoji} 你{mine[m.subject_id]}的《{m.title}》近30天{label} {abs(m.delta_score)} 分"
                                   + (f"（现 {m.current_score}，{m.rating_total} 人评分）" if m.current_score is not None else ""),
                        "url": f"https://bgm.tv/subject/{m.subject_id}",
                    })
        return {
            "sections": [{"title": "口碑异动", "items": lines}] if lines else [],
            "caveats": ["异动数据来自 netaba.re 近30天快照；无命中时不推送。"],
        }

    async def _daily_airing_payload(self, rule: SubscriptionRule, client: BangumiClient, *, mutate: bool) -> dict[str, Any]:
        if not rule.username:
            return {"sections": [], "caveats": ["daily_airing 需要登录用户。"]}
        local_now = datetime.now(_zone(rule.schedule.timezone))
        limit = int(rule.filters.get("limit") or 12)
        include_wishlist = bool(rule.filters.get("include_wishlist", True))
        include_radar = bool(rule.filters.get("include_radar", True))
        include_rss = bool(rule.filters.get("include_rss", True))
        include_birthday = bool(rule.filters.get("include_birthday", True))
        airing_items: list[Any] = []
        radar_section: dict[str, Any] | None = None
        res = await AiringProgressTool(client).run(
            AiringProgressArgs(username=rule.username, include_wishlist=include_wishlist, limit=max(limit, 8))
        )
        if res.ok and res.data:
            airing_items = res.data.items
        if include_radar and airing_items:
            radar_section = await self._daily_episode_radar_section(client, airing_items)
        rss_updates = await self._watch_plan_rss_updates(rule.username, mutate=mutate) if include_rss else []
        birthday_section = await self._birthday_section(limit=6) if include_birthday else None
        sections = [
            {
                "title": "今日追番进度",
                "items": [x.model_dump(mode="json", exclude_none=True) for x in airing_items[:limit]],
                "notes": ["基于 Bangumi 正片 airdate 与 ep_status；国内平台上架可能有时差。"],
            },
            *([radar_section] if radar_section else []),
            {
                "title": "RSS 新资源",
                "items": rss_updates[: int(rule.filters.get("rss_limit") or 12)],
                "notes": ["只检查计划板 rss_url；Otomo 不下载、不托管资源。"],
            },
            *([birthday_section] if birthday_section else []),
        ]
        return {
            "username": rule.username,
            "date": local_now.date().isoformat(),
            "sections": sections,
            "next_actions": [
                "如果某部番确定追，可以把 release RSS 写入计划板，用每日提醒检查更新。",
                "如果需要正版观看入口，询问“这部番在哪看”。",
            ],
            "caveats": [
                "每日提醒由新版订阅系统统一调度；需要 backend/worker 常驻运行。",
            ],
        }

    async def _watch_plan_rss_updates(self, username: str, *, mutate: bool) -> list[dict[str, Any]]:
        mem = self.ltm.load_user(username)
        updates: list[dict[str, Any]] = []
        changed = False
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
            if mutate:
                plan.last_seen_pub_date = latest.pub_date
                plan.updated_at = now_iso()
                changed = True
        if changed:
            self.ltm.save_user(mem)
        return updates

    async def _birthday_section(self, *, limit: int) -> dict[str, Any] | None:
        try:
            res = await GetCharacterBirthdaysTool().run(BirthdayArgs(limit=limit, moegirl_limit=12))
        except Exception:  # noqa: BLE001
            return None
        if not res.ok or res.data is None or not res.data.count:
            return None
        rows = [
            {"name": c.name_native or c.name, "title": c.from_media, "url": c.anilist_url or c.bangumi_search_url}
            for c in res.data.characters[:limit]
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

    async def _daily_episode_radar_section(
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

    async def _rss_payload(self, rule: SubscriptionRule) -> dict[str, Any]:
        urls = []
        if rule.filters.get("rss_url"):
            urls.append({"name": rule.filters.get("title") or "RSS", "rss_url": rule.filters["rss_url"]})
        if rule.filters.get("include_watch_plan", True) and rule.username:
            mem = self.ltm.load_user(rule.username)
            urls.extend(
                {"name": item.name, "rss_url": item.rss_url, "subject_id": item.subject_id, "subgroup": item.subgroup}
                for item in mem.watch_plan
                if item.rss_url
            )
        updates = []
        for row in urls[: int(rule.filters.get("max_feeds") or 20)]:
            try:
                items = await fetch_release_items_from_url(row["rss_url"], "subscription_rss")
            except Exception as e:  # noqa: BLE001
                updates.append({"name": row.get("name"), "error": f"{type(e).__name__}: {str(e)[:120]}"})
                continue
            items.sort(key=lambda x: x.pub_date, reverse=True)
            if items:
                latest = items[0]
                updates.append({
                    **row,
                    "title": latest.title,
                    "url": latest.page_url or latest.torrent_url or latest.magnet,
                    "pub_date": latest.pub_date,
                    "quality": latest.quality,
                    "source": latest.source,
                })
        return {
            "sections": [
                {
                    "title": "RSS 新资源",
                    "items": updates,
                    "notes": ["只聚合公开 RSS 元数据；Otomo 不下载、不托管、不代理资源。"],
                }
            ],
        }

    async def _bili_payload(self, rule: SubscriptionRule) -> dict[str, Any]:
        query = str(rule.filters.get("query") or rule.filters.get("up_name") or "新番导视").strip()
        tags = rule.filters.get("tags") if isinstance(rule.filters.get("tags"), list) else []
        res = await SearchBiliGuideVideosTool().run(
            BiliGuideSearchArgs(query=query, tags=tags, whitelist_only=bool(rule.filters.get("whitelist_only", True)), limit=int(rule.filters.get("limit") or 8))
        )
        items = [x.model_dump(mode="json", exclude_none=True) for x in (res.data.videos if res.ok and res.data else [])]
        return {
            "sections": [
                {
                    "title": "B站导视 / 漫评新视频",
                    "items": items,
                    "notes": ["仅读取公开搜索元数据；评论/字幕仍按需读取，不长期缓存。"],
                }
            ],
            "caveats": [res.error] if not res.ok and res.error else [],
        }

    def _record(
        self,
        rule: SubscriptionRule,
        hit_key: str,
        status: DeliveryStatus,
        *,
        title: str = "",
        payload: dict[str, Any] | None = None,
        deliveries: list[dict[str, Any]] | None = None,
        error: str = "",
    ) -> DeliveryRecord:
        return self.store.add_delivery(
            DeliveryRecord(
                id=f"delivery_{secrets.token_urlsafe(14).replace('-', '').replace('_', '')[:20]}",
                rule_id=rule.id,
                owner_key=rule.owner_key,
                kind=rule.kind,
                hit_key=hit_key,
                status=status,
                title=title,
                payload=payload or {},
                deliveries=deliveries or [],
                error=error,
                created_at=now_iso(),
            )
        )


def default_subscription_title(kind: str) -> str:
    return {
        "weekly_digest": "每周 Otomo 周报",
        "daily_airing": "每日追番提醒",
        "monthly_report": "每月 ACGN 月报",
        "rss_release": "RSS 新资源提醒",
        "birthday": "今日角色生日",
        "bili_up_video": "B站导视/漫评新视频",
        "rating_alert": "口碑哨兵：你的番评分异动",
    }.get(kind, "Otomo 订阅")


def _localize(now: datetime | None, tzname: str) -> datetime:
    """把 now 归一到目标时区。naive datetime 视为"已是该时区的墙钟时间"——不能用
    .astimezone()，那会按运行机器系统时区解释（本地 UTC+8 vs CI UTC 不一致，曾致 CI 失败）。"""
    tz = _zone(tzname)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def due_hit_key(rule: SubscriptionRule, now: datetime | None = None) -> str:
    local_now = _localize(now, rule.schedule.timezone)
    if rule.schedule.interval_minutes:
        interval = max(5, int(rule.schedule.interval_minutes))
        bucket = int(local_now.timestamp()) // (interval * 60)
        return f"interval-{interval}-{bucket}"
    if rule.kind == "monthly_report":
        return f"{local_now:%Y-%m}-{_scheduled_month_day(rule, local_now):02d}-{rule.schedule.hour:02d}{rule.schedule.minute:02d}"
    if rule.schedule.weekday is not None:
        return f"{local_now:%G-W%V}-{rule.schedule.weekday}-{rule.schedule.hour:02d}{rule.schedule.minute:02d}"
    return f"{local_now:%Y-%m-%d}-{rule.schedule.hour:02d}{rule.schedule.minute:02d}"


def is_rule_due(rule: SubscriptionRule, now: datetime | None = None) -> bool:
    local_now = _localize(now, rule.schedule.timezone)
    if not rule.enabled:
        return False
    if _inside_quiet_hours(local_now, rule.quiet_hours):
        return False
    if rule.schedule.interval_minutes:
        interval = max(5, int(rule.schedule.interval_minutes))
        if rule.last_hit_key == due_hit_key(rule, local_now):
            return False
        last_run = _parse_datetime(rule.last_run_at)
        if last_run is not None:
            last_local = last_run.astimezone(local_now.tzinfo) if last_run.tzinfo else last_run.replace(tzinfo=local_now.tzinfo)
            if local_now - last_local < timedelta(minutes=interval):
                return False
        return True
    if rule.schedule.weekday is not None and local_now.weekday() != rule.schedule.weekday:
        return False
    if rule.kind == "monthly_report" and local_now.day != _scheduled_month_day(rule, local_now):
        return False
    if local_now.hour != rule.schedule.hour:
        return False
    if local_now.minute < rule.schedule.minute:
        return False
    if rule.last_hit_key == due_hit_key(rule, local_now):
        return False
    return True


def _scheduled_month_day(rule: SubscriptionRule, local_now: datetime) -> int:
    requested = rule.schedule.day_of_month or 1
    last_day = monthrange(local_now.year, local_now.month)[1]
    return min(max(1, requested), last_day)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _payload_has_content(payload: dict[str, Any]) -> bool:
    sections = payload.get("sections")
    if not isinstance(sections, list):
        return bool(payload.get("items") or payload.get("summary") or payload.get("title"))
    for section in sections:
        if not isinstance(section, dict):
            continue
        items = section.get("items")
        if isinstance(items, list) and items:
            return True
        if section.get("summary") or section.get("text"):
            return True
    return False


def _inbox_kind(kind: str) -> Literal["weekly_digest", "daily_airing", "system"]:
    if kind == "weekly_digest":
        return "weekly_digest"
    if kind == "daily_airing":
        return "daily_airing"
    return "system"


def weekly_subscription_from_rule(rule: SubscriptionRule) -> WeeklyDigestSubscription:
    return WeeklyDigestSubscription(
        enabled=rule.enabled,
        weekday=rule.schedule.weekday if rule.schedule.weekday is not None else 0,
        hour=rule.schedule.hour,
        timezone=rule.schedule.timezone,
        push_grading=rule.template,
        channels=rule.channels,
        email=rule.email,
        webhook_url=rule.webhook_url,
        webhook_format=rule.webhook_format,
    )


def _normalize_channels(channels: list[str]) -> list[SubscriptionChannel]:
    allowed = {"inbox", "email", "webhook"}
    out = [c for c in dict.fromkeys(channels or ["inbox"]) if c in allowed]
    return out or ["inbox"]


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def _inside_quiet_hours(now: datetime, quiet: QuietHours) -> bool:
    def parse(value: str) -> tuple[int, int]:
        try:
            hour, minute = value.split(":", 1)
            return max(0, min(23, int(hour))), max(0, min(59, int(minute)))
        except Exception:  # noqa: BLE001
            return 0, 0

    s_h, s_m = parse(quiet.start)
    e_h, e_m = parse(quiet.end)
    current = now.hour * 60 + now.minute
    start = s_h * 60 + s_m
    end = e_h * 60 + e_m
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _rule_values(rule: SubscriptionRule) -> tuple[Any, ...]:
    return (
        rule.id,
        rule.owner_key,
        rule.username,
        rule.kind,
        1 if rule.enabled else 0,
        rule.title,
        _dump(rule.filters),
        _dump(rule.schedule.model_dump(mode="json")),
        _dump(rule.channels),
        rule.template,
        rule.webhook_format,
        rule.webhook_url,
        rule.email,
        _dump(rule.quiet_hours.model_dump(mode="json")),
        rule.last_run_at,
        rule.last_hit_key,
        rule.created_at,
        rule.updated_at,
    )


def _row_to_rule(row: sqlite3.Row) -> SubscriptionRule:
    return SubscriptionRule(
        id=row["id"],
        owner_key=row["owner_key"],
        username=row["username"],
        kind=row["kind"],
        enabled=bool(row["enabled"]),
        title=row["title"],
        filters=_load(row["filters_json"], {}),
        schedule=SubscriptionSchedule.model_validate(_load(row["schedule_json"], {})),
        channels=_normalize_channels(_load(row["channels_json"], ["inbox"])),
        template=row["template"],
        webhook_format=row["webhook_format"],
        webhook_url=row["webhook_url"],
        email=row["email"],
        quiet_hours=QuietHours.model_validate(_load(row["quiet_hours_json"], {})),
        last_run_at=row["last_run_at"],
        last_hit_key=row["last_hit_key"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_delivery(row: sqlite3.Row) -> DeliveryRecord:
    return DeliveryRecord(
        id=row["id"],
        rule_id=row["rule_id"],
        owner_key=row["owner_key"],
        kind=row["kind"],
        hit_key=row["hit_key"],
        status=row["status"],
        title=row["title"],
        payload=_load(row["payload_json"], {}),
        deliveries=_load(row["deliveries_json"], []),
        error=row["error"],
        created_at=row["created_at"],
    )


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback
