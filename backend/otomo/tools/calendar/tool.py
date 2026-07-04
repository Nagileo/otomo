"""Bangumi broadcast calendar and airing-progress tools."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from ...agent.contracts import Citation, Tool, ToolResult
from .._concurrency import gather_limited
from ..bangumi.client import SUBJECT_TYPE, BangumiClient


_COLLECTION_STATUS = {
    1: "wishlist",
    2: "completed",
    3: "watching",
    4: "on_hold",
    5: "dropped",
}
_STATUS_LABEL = {
    "wishlist": "想看",
    "completed": "看过",
    "watching": "在看",
    "on_hold": "搁置",
    "dropped": "抛弃",
}


class BroadcastCalendarArgs(BaseModel):
    day: Literal["today", "week"] = Field("today", description="today=今天更新；week=本周放送表")
    only_mine: bool = Field(False, description="只看当前用户在看/想看的当季作品")
    username: str | None = Field(None, description="Bangumi 用户名；only_mine=true 且不传时使用当前 token 账号")
    include_wishlist: bool = Field(True, description="only_mine 时是否包含想看列表")


class BroadcastCalendarItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    name_cn: str = ""
    air_date: str = ""
    air_weekday: int | None = None
    weekday_cn: str = ""
    score: float | None = None
    rank: int | None = None
    doing: int | None = None
    image: str | None = None
    my_collection: str = ""
    my_collection_label: str = ""
    ep_status: int | None = None
    url: str = ""
    note: str = ""


class BroadcastCalendarDay(BaseModel):
    weekday_id: int
    weekday_cn: str
    is_today: bool = False
    items: list[BroadcastCalendarItem] = Field(default_factory=list)


class BroadcastCalendarResult(BaseModel):
    scope: Literal["today", "week"]
    today: str
    timezone: str = "Asia/Shanghai"
    days: list[BroadcastCalendarDay] = Field(default_factory=list)
    only_mine: bool = False
    username: str = ""
    count: int = 0
    notes: list[str] = Field(default_factory=list)


class AiringProgressArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则使用当前 token 账号")
    include_wishlist: bool = Field(False, description="是否把想看列表里本季在播作品纳入进度提示")
    limit: int = Field(30, ge=1, le=80)


class AiringProgressItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    name: str
    image: str | None = None
    status: Literal["watching", "wishlist"] = "watching"
    status_label: str = "在看"
    my_ep: int = 0
    aired_ep: int = 0
    behind: int = 0
    total_eps: int | None = None
    next_air_date: str = ""
    next_episode_sort: int | None = None
    action: str = ""
    score: float | None = None
    url: str = ""


class AiringProgressResult(BaseModel):
    username: str
    today: str
    timezone: str = "Asia/Shanghai"
    items: list[AiringProgressItem] = Field(default_factory=list)
    behind_count: int = 0
    notes: list[str] = Field(default_factory=list)


def _today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


async def _username(client: BangumiClient, username: str | None) -> str | None:
    if username:
        return username
    try:
        me = await client.get_me()
    except Exception:  # noqa: BLE001
        return None
    return str(me.get("username") or me.get("id") or "") or None


def _subject(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("subject") if isinstance(row.get("subject"), dict) else row


def _subject_id(row: dict[str, Any]) -> int | None:
    sid = _subject(row).get("id")
    try:
        return int(sid)
    except (TypeError, ValueError):
        return None


def _name(row: dict[str, Any]) -> str:
    subj = _subject(row)
    return str(subj.get("name_cn") or subj.get("name") or f"subject {subj.get('id')}")


def _image(row: dict[str, Any]) -> str | None:
    images = _subject(row).get("images") or {}
    return images.get("common") or images.get("medium") or images.get("grid")


def _score(row: dict[str, Any]) -> tuple[float | None, int | None]:
    rating = _subject(row).get("rating") or {}
    score = rating.get("score") or row.get("score")
    rank = rating.get("rank")
    try:
        score_f = float(score) if score else None
    except (TypeError, ValueError):
        score_f = None
    try:
        rank_i = int(rank) if rank else None
    except (TypeError, ValueError):
        rank_i = None
    return score_f, rank_i


def _ep_status(row: dict[str, Any]) -> int:
    try:
        return max(0, int(row.get("ep_status") or 0))
    except (TypeError, ValueError):
        return 0


async def _collection_map(
    client: BangumiClient,
    username: str | None,
    *,
    include_wishlist: bool,
) -> dict[int, dict[str, Any]]:
    if not username:
        return {}
    statuses = [3]
    if include_wishlist:
        statuses.append(1)
    out: dict[int, dict[str, Any]] = {}
    for status in statuses:
        try:
            rows = await client.get_all_user_collections(
                username,
                SUBJECT_TYPE["anime"],
                collection_type=status,
                max_items=1000 if status == 3 else 500,
            )
        except Exception:  # noqa: BLE001
            continue
        for row in rows:
            sid = _subject_id(row)
            if not sid:
                continue
            out[sid] = {
                "status": _COLLECTION_STATUS.get(status, str(status)),
                "label": _STATUS_LABEL.get(_COLLECTION_STATUS.get(status, ""), str(status)),
                "ep_status": _ep_status(row),
                "raw": row,
            }
    return out


def _calendar_item(raw: dict[str, Any], weekday_cn: str, coll: dict[str, Any] | None) -> BroadcastCalendarItem | None:
    sid = _subject_id(raw)
    if not sid:
        return None
    score, rank = _score(raw)
    collection = raw.get("collection") or {}
    return BroadcastCalendarItem(
        id=sid,
        name=str(raw.get("name") or ""),
        name_cn=str(raw.get("name_cn") or raw.get("name") or ""),
        air_date=str(raw.get("air_date") or raw.get("date") or ""),
        air_weekday=raw.get("air_weekday"),
        weekday_cn=weekday_cn,
        score=score,
        rank=rank,
        doing=collection.get("doing") if isinstance(collection, dict) else None,
        image=_image(raw),
        my_collection=str((coll or {}).get("status") or ""),
        my_collection_label=str((coll or {}).get("label") or ""),
        ep_status=(coll or {}).get("ep_status"),
        url=f"https://bgm.tv/subject/{sid}",
        note="日本放送日；国内平台上架时间可能有时差。",
    )


class BroadcastCalendarTool(Tool):
    name = "get_broadcast_calendar"
    description = (
        "查询 Bangumi 当季每日/本周放送日历。用于『今天有什么番更新』『本周哪天更新』；"
        "only_mine=true 时会与当前用户在看/想看列表对齐。"
    )
    args_model = BroadcastCalendarArgs
    result_model = BroadcastCalendarResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: BroadcastCalendarArgs) -> ToolResult[BroadcastCalendarResult]:
        today = _today()
        today_weekday = today.weekday() + 1
        username = await _username(self.client, args.username) if args.only_mine else (args.username or "")
        coll = await _collection_map(self.client, username, include_wishlist=args.include_wishlist) if args.only_mine else {}
        raw_days = await self.client.get_calendar()
        days: list[BroadcastCalendarDay] = []
        for raw_day in raw_days or []:
            weekday = raw_day.get("weekday") or {}
            wid = int(weekday.get("id") or 0)
            if args.day == "today" and wid != today_weekday:
                continue
            weekday_cn = str(weekday.get("cn") or weekday.get("en") or f"weekday {wid}")
            items: list[BroadcastCalendarItem] = []
            for raw in raw_day.get("items") or []:
                sid = _subject_id(raw)
                if args.only_mine and sid not in coll:
                    continue
                item = _calendar_item(raw, weekday_cn, coll.get(sid) if sid else None)
                if item:
                    items.append(item)
            items.sort(key=lambda x: (0 if x.my_collection == "watching" else 1, -(x.doing or 0), x.name_cn))
            if items or not args.only_mine:
                days.append(BroadcastCalendarDay(
                    weekday_id=wid,
                    weekday_cn=weekday_cn,
                    is_today=wid == today_weekday,
                    items=items,
                ))
        days.sort(key=lambda d: (0 if d.is_today else 1, (d.weekday_id - today_weekday) % 7))
        count = sum(len(d.items) for d in days)
        notes = [
            "Bangumi /calendar 是当季放送表，日期以日本放送日为主。",
            "only_mine 只 join 当前可见的在看/想看收藏；收藏私有或未登录会导致命中为空。",
        ]
        data = BroadcastCalendarResult(
            scope=args.day,
            today=today.isoformat(),
            days=days,
            only_mine=args.only_mine,
            username=username or "",
            count=count,
            notes=notes,
        )
        sources = [
            Citation(title=item.name_cn or item.name, url=item.url, source="bangumi", image=item.image)
            for day in days for item in day.items[:4]
        ][:8]
        return ToolResult(ok=True, data=data, sources=sources)


def _aired_episode_count(rows: list[dict[str, Any]], today: date) -> tuple[int, str, int | None]:
    aired = 0
    next_air = ""
    next_sort: int | None = None
    future: list[tuple[date, int]] = []
    for ep in rows:
        sort = ep.get("sort") or ep.get("ep")
        try:
            sort_i = int(float(sort))
        except (TypeError, ValueError):
            continue
        airdate = _parse_date(ep.get("airdate"))
        if airdate and airdate <= today:
            aired = max(aired, sort_i)
        elif airdate and airdate > today:
            future.append((airdate, sort_i))
    if future:
        future.sort(key=lambda x: (x[0], x[1]))
        next_air = future[0][0].isoformat()
        next_sort = future[0][1]
    return aired, next_air, next_sort


class AiringProgressTool(Tool):
    name = "get_airing_progress"
    description = (
        "计算用户在看番剧的放送进度：已播到第几集、用户看到第几集、落后几集、下一集日期。"
        "用于『我在追的番落后几集』『这周怎么追』。"
    )
    args_model = AiringProgressArgs
    result_model = AiringProgressResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def _progress_for(self, row: dict[str, Any], status: Literal["watching", "wishlist"]) -> AiringProgressItem | None:
        sid = _subject_id(row)
        if not sid:
            return None
        try:
            raw = await self.client.get_episodes(sid, ep_type=0, limit=200)
        except Exception:  # noqa: BLE001
            return None
        episodes = raw.get("data") if isinstance(raw, dict) else raw
        if not isinstance(episodes, list):
            episodes = []
        today = _today()
        aired, next_air, next_sort = _aired_episode_count(episodes, today)
        my_ep = _ep_status(row) if status == "watching" else 0
        eps = _subject(row).get("eps") or _subject(row).get("total_episodes")
        try:
            eps_i = int(eps) if eps else None
        except (TypeError, ValueError):
            eps_i = None
        behind = max(aired - my_ep, 0) if status == "watching" else 0
        if status == "watching":
            action = f"继续看第 {my_ep + 1} 集" if behind else (f"等第 {next_sort} 集更新" if next_sort else "当前没有落后")
        else:
            action = f"本季已播到第 {aired} 集，可从第 1 集试开" if aired else "尚未开播或暂无正片 airdate"
        score, _rank = _score(row)
        return AiringProgressItem(
            id=sid,
            name=_name(row),
            image=_image(row),
            status=status,
            status_label=_STATUS_LABEL[status],
            my_ep=my_ep,
            aired_ep=aired,
            behind=behind,
            total_eps=eps_i,
            next_air_date=next_air,
            next_episode_sort=next_sort,
            action=action,
            score=score,
            url=f"https://bgm.tv/subject/{sid}",
        )

    async def run(self, args: AiringProgressArgs) -> ToolResult[AiringProgressResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error="需要 username 或有效 Bangumi 登录态")
        rows = await self.client.get_all_user_collections(
            username, SUBJECT_TYPE["anime"], collection_type=3, max_items=200
        )
        jobs = [self._progress_for(row, "watching") for row in rows[: args.limit]]
        if args.include_wishlist:
            wish = await self.client.get_all_user_collections(
                username, SUBJECT_TYPE["anime"], collection_type=1, max_items=120
            )
            jobs.extend(self._progress_for(row, "wishlist") for row in wish[: max(0, args.limit - len(jobs))])
        results = await gather_limited(jobs, host="bangumi")
        items = [x for x in results if isinstance(x, AiringProgressItem)]
        items.sort(key=lambda x: (-x.behind, x.next_air_date or "9999-99-99", -float(x.score or 0.0)))
        items = items[: args.limit]
        data = AiringProgressResult(
            username=username,
            today=_today().isoformat(),
            items=items,
            behind_count=sum(1 for x in items if x.behind > 0),
            notes=[
                "aired_ep 依据 Bangumi 正片分集 airdate <= 今天计算。",
                "my_ep 取用户收藏 ep_status；若用户没有更新 Bangumi 进度，会显示偏低。",
            ],
        )
        return ToolResult(
            ok=True,
            data=data,
            sources=[
                Citation(title=i.name, url=i.url, source="bangumi", image=i.image)
                for i in items[:8]
            ],
        )


def build_calendar_tools(client: BangumiClient) -> list[Tool]:
    return [BroadcastCalendarTool(client), AiringProgressTool(client)]
