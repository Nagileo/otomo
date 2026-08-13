"""The shared Today Cockpit domain service.

Chat tools, the fixed /today product page, and daily notifications all consume
this service.  Calendar-only preferences deliberately stay separate from taste
memory: hiding a seasonal title is not evidence that the user dislikes it.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import sqlite3
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from .config import settings
from .tools._concurrency import gather_limited
from .tools.bangumi.client import BangumiClient
from .tools.calendar.tool import (
    AiringProgressArgs,
    AiringProgressItem,
    AiringProgressTool,
    BroadcastCalendarArgs,
    BroadcastCalendarTool,
)


class TodayPreference(BaseModel):
    subject_id: int
    hidden_this_season: bool = False
    hidden_season: str = ""
    pinned: bool = False
    updated_at: str = ""


class TodayItem(BaseModel):
    id: int
    name: str
    name_cn: str = ""
    image: str | None = None
    url: str = ""
    weekday_id: int | None = None
    weekday_cn: str = ""
    broadcast: str = ""
    air_date: str = ""
    score: float | None = None
    doing: int | None = None
    collection_status: str = ""
    collection_label: str = ""
    my_ep: int = 0
    aired_ep: int = 0
    behind: int = 0
    next_air_date: str = ""
    next_episode: int | None = None
    hidden_this_season: bool = False
    pinned: bool = False
    is_today: bool = False
    is_yesterday: bool = False
    action: str = ""


class TodayCockpitResult(BaseModel):
    username: str
    date: str
    timezone: str = "Asia/Shanghai"
    today: list[TodayItem] = Field(default_factory=list)
    yesterday: list[TodayItem] = Field(default_factory=list)
    week: list["TodayDay"] = Field(default_factory=list)
    hidden: list[TodayItem] = Field(default_factory=list)
    backlog: list[TodayItem] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class TodayDay(BaseModel):
    weekday_id: int
    weekday_cn: str
    is_today: bool = False
    items: list[TodayItem] = Field(default_factory=list)


def _now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _season_key(day=None) -> str:
    current = day or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return f"{current.year}-Q{((current.month - 1) // 3) + 1}"


class TodayPreferenceStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.today_store_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS today_preferences (
                    username TEXT NOT NULL,
                    subject_id INTEGER NOT NULL,
                    hidden_this_season INTEGER NOT NULL DEFAULT 0,
                    hidden_season TEXT NOT NULL DEFAULT '',
                    pinned INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(username, subject_id)
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(today_preferences)").fetchall()}
            if "hidden_season" not in columns:
                conn.execute("ALTER TABLE today_preferences ADD COLUMN hidden_season TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """UPDATE today_preferences SET hidden_season=?
                   WHERE hidden_this_season=1 AND hidden_season=''""",
                (_season_key(),),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def list(self, username: str) -> dict[int, TodayPreference]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT subject_id,hidden_this_season,hidden_season,pinned,updated_at "
                "FROM today_preferences WHERE username=?",
                (username,),
            ).fetchall()
        season = _season_key()
        return {
            int(row["subject_id"]): TodayPreference(
                subject_id=int(row["subject_id"]),
                hidden_this_season=(
                    bool(row["hidden_this_season"])
                    and (not row["hidden_season"] or row["hidden_season"] == season)
                ),
                hidden_season=str(row["hidden_season"] or ""),
                pinned=bool(row["pinned"]),
                updated_at=row["updated_at"],
            )
            for row in rows
        }

    def update(
        self,
        username: str,
        subject_id: int,
        *,
        hidden_this_season: bool | None = None,
        pinned: bool | None = None,
    ) -> TodayPreference:
        current = self.list(username).get(subject_id) or TodayPreference(subject_id=subject_id)
        if hidden_this_season is not None:
            current.hidden_this_season = hidden_this_season
            current.hidden_season = _season_key() if hidden_this_season else ""
        if pinned is not None:
            current.pinned = pinned
        current.updated_at = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO today_preferences(
                    username,subject_id,hidden_this_season,hidden_season,pinned,updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(username,subject_id) DO UPDATE SET
                    hidden_this_season=excluded.hidden_this_season,
                    hidden_season=excluded.hidden_season,
                    pinned=excluded.pinned,
                    updated_at=excluded.updated_at
                """,
                (
                    username,
                    subject_id,
                    int(current.hidden_this_season),
                    current.hidden_season,
                    int(current.pinned),
                    current.updated_at,
                ),
            )
        return current


class TodayCockpitService:
    def __init__(self, client: BangumiClient, store: TodayPreferenceStore | None = None) -> None:
        self.client = client
        self.store = store or TodayPreferenceStore()

    async def build(
        self,
        username: str,
        *,
        include_wishlist: bool = True,
        include_hidden: bool = True,
        limit: int = 80,
    ) -> TodayCockpitResult:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        yesterday = today - timedelta(days=1)
        # Calendar and progress are independent upstream calls and share no mutable state.
        cal_result, progress_result = await gather_limited(
            [
                BroadcastCalendarTool(self.client).run(
                    BroadcastCalendarArgs(
                        day="week", only_mine=True, username=username,
                        include_wishlist=include_wishlist,
                    )
                ),
                AiringProgressTool(self.client).run(
                    AiringProgressArgs(
                        username=username, include_wishlist=include_wishlist, limit=limit,
                    )
                ),
            ],
            host="bangumi",
        )
        calendar_ok = not isinstance(cal_result, Exception) and cal_result.ok and cal_result.data is not None
        progress_ok = (
            not isinstance(progress_result, Exception)
            and progress_result.ok
            and progress_result.data is not None
        )
        days = cal_result.data.days if calendar_ok else []
        progress = (
            progress_result.data.items
            if progress_ok
            else []
        )
        progress_map = {item.id: item for item in progress}
        calendar_personalized = bool(calendar_ok and cal_result.data.only_mine)
        if not calendar_personalized:
            # ``only_mine`` can degrade to the public calendar when collection
            # reads fail. Fail closed instead of showing every broadcast title.
            days = [
                day.model_copy(update={"items": [item for item in day.items if item.id in progress_map]})
                for day in days
            ]
        prefs = self.store.list(username)

        visible_today: list[TodayItem] = []
        visible_yesterday: list[TodayItem] = []
        hidden: list[TodayItem] = []
        today_wid = today.weekday() + 1
        yesterday_wid = yesterday.weekday() + 1
        for day in days:
            for raw in day.items:
                pref = prefs.get(raw.id) or TodayPreference(subject_id=raw.id)
                p = progress_map.get(raw.id)
                item = self._merge(raw, p, pref, day.weekday_id, day.weekday_cn)
                item.is_today = day.weekday_id == today_wid
                item.is_yesterday = day.weekday_id == yesterday_wid
                if pref.hidden_this_season:
                    hidden.append(item)
                elif item.is_today:
                    visible_today.append(item)
                elif item.is_yesterday:
                    visible_yesterday.append(item)

        backlog = [
            self._from_progress(p, prefs.get(p.id) or TodayPreference(subject_id=p.id))
            for p in progress
            if p.behind > 0
        ]
        def sort_key(item: TodayItem):
            return (0 if item.pinned else 1, -item.behind, -(item.score or 0), item.name)

        for rows in (visible_today, visible_yesterday, hidden, backlog):
            rows.sort(key=sort_key)
        visible_week = [
            TodayDay(
                weekday_id=day.weekday_id,
                weekday_cn=day.weekday_cn,
                is_today=day.is_today,
                items=sorted(
                    [
                        self._merge(
                            raw,
                            progress_map.get(raw.id),
                            prefs.get(raw.id) or TodayPreference(subject_id=raw.id),
                            day.weekday_id,
                            day.weekday_cn,
                        )
                        for raw in day.items
                        if not (prefs.get(raw.id) or TodayPreference(subject_id=raw.id)).hidden_this_season
                    ],
                    key=lambda item: (
                        0 if item.pinned else 1,
                        item.name_cn or item.name,
                    ),
                ),
            )
            for day in days
        ]
        notes = [
            "放送日来自 Bangumi calendar，档期补充来自 yuc；均以日本放送为主。",
            "隐藏仅影响本季日历，不会写入推荐雷区或 Bangumi 收藏。",
            "国内平台上架可能晚于日本电视放送，观看入口应单独核验。",
        ]
        if not calendar_ok:
            notes.insert(0, "Bangumi 放送日历本轮读取失败；今天/本周视图可能为空。")
        elif not calendar_personalized:
            notes.insert(0, "个性化日历读取降级；已用可确认的追番进度过滤全站条目。")
        if not progress_ok:
            notes.insert(0, "分集进度本轮读取失败；放送条目仍保留，但落后集数可能暂缺。")
        return TodayCockpitResult(
            username=username,
            date=today.isoformat(),
            today=visible_today,
            yesterday=visible_yesterday,
            week=visible_week,
            hidden=hidden if include_hidden else [],
            backlog=backlog[:20],
            counts={
                "today": len(visible_today),
                "yesterday": len(visible_yesterday),
                "week": sum(len(day.items) for day in visible_week),
                "hidden": len(hidden),
                "backlog": len(backlog),
                "behind": sum(1 for p in progress if p.behind > 0),
            },
            notes=notes,
        )

    @staticmethod
    def _merge(raw: Any, p: AiringProgressItem | None, pref: TodayPreference, wid: int, wcn: str) -> TodayItem:
        my_ep = p.my_ep if p else int(raw.ep_status or 0)
        aired = p.aired_ep if p else 0
        behind = p.behind if p else max(aired - my_ep, 0)
        action = p.action if p else (f"继续看第 {my_ep + 1} 集" if my_ep else "从第 1 集试开")
        return TodayItem(
            id=raw.id, name=raw.name, name_cn=raw.name_cn, image=raw.image, url=raw.url,
            weekday_id=wid, weekday_cn=wcn, broadcast=raw.broadcast, air_date=raw.air_date,
            score=raw.score, doing=raw.doing, collection_status=raw.my_collection,
            collection_label=raw.my_collection_label, my_ep=my_ep, aired_ep=aired,
            behind=behind, next_air_date=p.next_air_date if p else "",
            next_episode=p.next_episode_sort if p else None,
            hidden_this_season=pref.hidden_this_season, pinned=pref.pinned, action=action,
        )

    @staticmethod
    def _from_progress(p: AiringProgressItem, pref: TodayPreference) -> TodayItem:
        return TodayItem(
            id=p.id, name=p.name, name_cn=p.name, image=p.image, url=p.url,
            score=p.score, collection_status=p.status, collection_label=p.status_label,
            my_ep=p.my_ep, aired_ep=p.aired_ep, behind=p.behind,
            next_air_date=p.next_air_date, next_episode=p.next_episode_sort,
            hidden_this_season=pref.hidden_this_season, pinned=pref.pinned, action=p.action,
        )
