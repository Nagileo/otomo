"""圣地巡礼工具（anitabi.cn 开放 API，key 原生为 Bangumi subject id）。

数据含贡献者署名（origin 字段），展示时保留来源；截图为 anitabi 图床外链，不转存。
"""
from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent._common import emit_tool_progress
from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from .._cache import acached
from .._concurrency import gather_limited
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

_ANITABI_LITE = "https://api.anitabi.cn/bangumi/{sid}/lite"
_ANITABI_POINTS = "https://api.anitabi.cn/bangumi/{sid}/points/detail"
_ANITABI_MAP = "https://anitabi.cn/map?bangumiId={sid}"


class PilgrimageArgs(BaseModel):
    subject_id: int | None = Field(None, description="Bangumi 动画 subject_id；优先使用")
    title: str = Field("", description="作品名；subject_id 为空时用于搜索")
    limit: int = Field(16, ge=1, le=40, description="最多返回多少个巡礼点")


class PilgrimagePoint(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    episode: int | None = None
    second: int | None = None
    lat: float | None = None
    lng: float | None = None
    image: str = ""
    origin: str = ""
    google_maps_url: str = ""


class PilgrimageResult(BaseModel):
    subject_id: int
    title: str
    city: str = ""
    map_url: str = ""
    count: int = 0
    points: list[PilgrimagePoint] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class PilgrimageTripArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传用当前账号")
    city: str = Field("", description="可选：目的地城市关键词（如 东京/京都），过滤有该城市圣地的作品")
    max_subjects: int = Field(40, ge=5, le=80, description="最多检查多少部看过/在看的作品")


class PilgrimageTripEntry(BaseModel):
    subject_id: int
    title: str
    city: str = ""
    point_count: int = 0
    map_url: str = ""
    cover: str = ""
    sample_points: list[str] = Field(default_factory=list)


class PilgrimageTripResult(BaseModel):
    username: str
    city_filter: str = ""
    entries: list[PilgrimageTripEntry] = Field(default_factory=list)
    checked: int = 0
    caveats: list[str] = Field(default_factory=list)


def _gmaps(lat: Any, lng: Any) -> str:
    try:
        return f"https://www.google.com/maps?q={float(lat)},{float(lng)}"
    except (TypeError, ValueError):
        return ""


@acached(ttl=settings.cache_ttl * 24)
async def _fetch_anitabi(url: str) -> Any:
    async def fetch() -> Any:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.bangumi_user_agent},
        ) as client:
            res = await client.get(url)
            if res.status_code == 404:
                return None
            res.raise_for_status()
            text = res.text.strip()
            return res.json() if text else None

    result = await gather_limited([fetch()], host="anitabi")
    first = result[0]
    if isinstance(first, BaseException):
        raise first
    return first


async def _resolve_anime(client: BangumiClient, subject_id: int | None, title: str) -> tuple[int | None, str]:
    if subject_id:
        try:
            raw = await client.get_subject(subject_id)
            return subject_id, str(raw.get("name_cn") or raw.get("name") or subject_id)
        except Exception:  # noqa: BLE001
            return subject_id, title or str(subject_id)
    if title.strip():
        raw = await client.search_subjects(title, SUBJECT_TYPE["anime"], limit=3)
        rows = raw.get("data") or []
        if rows:
            return int(rows[0]["id"]), str(rows[0].get("name_cn") or rows[0].get("name") or title)
    return None, title.strip()


class GetPilgrimageMapTool(Tool):
    name = "get_pilgrimage_map"
    description = (
        "查询动画的圣地巡礼地点（anitabi.cn 社区数据）：取景地名称、对应集数与画面时间、坐标、对比截图与地图链接。"
        "用于『圣地巡礼 / 取景地 / 在哪取景 / 打卡地点』。"
    )
    args_model = PilgrimageArgs
    result_model = PilgrimageResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: PilgrimageArgs) -> ToolResult[PilgrimageResult]:
        sid, title = await _resolve_anime(self.client, args.subject_id, args.title)
        if not sid:
            return ToolResult(ok=False, error="需要 subject_id 或可解析的动画标题")
        await emit_tool_progress(tool=self.name, summary=f"查询《{title}》巡礼点", current=1, total=2)
        lite = await _fetch_anitabi(_ANITABI_LITE.format(sid=sid))
        if not lite:
            return ToolResult(
                ok=False,
                error=f"anitabi 未收录《{title}》的巡礼数据（社区库覆盖以有实地取景的作品为主）",
            )
        detail = await _fetch_anitabi(_ANITABI_POINTS.format(sid=sid)) or []
        rows = detail if isinstance(detail, list) and detail else (lite.get("litePoints") or [])
        points: list[PilgrimagePoint] = []
        for p in rows[: args.limit]:
            geo = p.get("geo") or [None, None]
            points.append(
                PilgrimagePoint(
                    name=str(p.get("name") or "未命名地点"),
                    episode=p.get("ep"),
                    second=p.get("s"),
                    lat=geo[0] if len(geo) > 0 else None,
                    lng=geo[1] if len(geo) > 1 else None,
                    image=str(p.get("image") or ""),
                    origin=str(p.get("origin") or ""),
                    google_maps_url=_gmaps(geo[0] if geo else None, geo[1] if len(geo) > 1 else None),
                )
            )
        await emit_tool_progress(tool=self.name, summary=f"巡礼点 {len(points)} 个（共 {len(rows)}）", current=2, total=2)
        result = PilgrimageResult(
            subject_id=sid,
            title=str(lite.get("cn") or title),
            city=str(lite.get("city") or ""),
            map_url=_ANITABI_MAP.format(sid=sid),
            count=len(rows) if isinstance(rows, list) else len(points),
            points=points,
            caveats=[
                "数据来自 anitabi.cn 社区共建（各点含贡献者署名）；实地探访请遵守当地秩序、不打扰居民。",
                "画面时间(s)为该取景出现在对应集的秒数，可对照截图核对。",
            ],
        )
        sources = [Citation(title=f"anitabi — {result.title} 巡礼地图", url=result.map_url, source="anitabi", image=lite.get("cover"))]
        sources.extend(
            Citation(title=f"{pt.name}（ep{pt.episode}）", url=pt.google_maps_url or result.map_url, source="anitabi", image=pt.image)
            for pt in points[:4]
        )
        return ToolResult(ok=True, data=result, sources=sources)


class PlanPilgrimageTripTool(Tool):
    name = "plan_pilgrimage_trip"
    description = (
        "旅行模式：扫描用户看过/在看的动画，聚合出有圣地巡礼数据的作品清单（可按目的地城市过滤）。"
        "用于『我要去东京，看过的番有哪些圣地 / 帮我规划圣地巡礼』。"
    )
    args_model = PilgrimageTripArgs
    result_model = PilgrimageTripResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: PilgrimageTripArgs) -> ToolResult[PilgrimageTripResult]:
        username = args.username
        if not username:
            try:
                me = await self.client.get_me()
                username = str(me.get("username") or me.get("id") or "")
            except Exception:  # noqa: BLE001
                username = ""
        if not username:
            return ToolResult(ok=False, error="需要 username 或有效 Bangumi 登录态")
        await emit_tool_progress(tool=self.name, summary=f"读取 @{username} 看过/在看列表", current=1, total=3)
        rows: list[dict[str, Any]] = []
        for ctype in (2, 3):  # 看过 / 在看
            try:
                part = await self.client.get_all_user_collections(
                    username, SUBJECT_TYPE["anime"], collection_type=ctype, max_items=args.max_subjects
                )
                rows.extend(part)
            except Exception:  # noqa: BLE001
                continue
        subjects: list[tuple[int, str]] = []
        seen: set[int] = set()
        for row in rows:
            subj = row.get("subject") or {}
            sid = subj.get("id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            subjects.append((int(sid), str(subj.get("name_cn") or subj.get("name") or sid)))
        subjects = subjects[: args.max_subjects]
        await emit_tool_progress(tool=self.name, summary=f"检查 {len(subjects)} 部作品的巡礼收录", current=2, total=3)
        lites = await gather_limited(
            [_fetch_anitabi(_ANITABI_LITE.format(sid=sid)) for sid, _ in subjects],
            host="anitabi_batch",  # 独立 host：_fetch_anitabi 内部走 "anitabi" 信号量，嵌套同名会死锁
        )
        entries: list[PilgrimageTripEntry] = []
        city_key = args.city.strip()
        for (sid, name), lite in zip(subjects, lites, strict=False):
            if isinstance(lite, BaseException) or not lite:
                continue
            city = str(lite.get("city") or "")
            if city_key and city_key not in city:
                continue
            pts = lite.get("litePoints") or []
            entries.append(
                PilgrimageTripEntry(
                    subject_id=sid,
                    title=str(lite.get("cn") or name),
                    city=city,
                    point_count=int(lite.get("pointsLength") or len(pts) or 0),
                    map_url=_ANITABI_MAP.format(sid=sid),
                    cover=str(lite.get("cover") or ""),
                    sample_points=[str(p.get("name")) for p in pts[:3] if p.get("name")],
                )
            )
        entries.sort(key=lambda e: -e.point_count)
        await emit_tool_progress(tool=self.name, summary=f"命中 {len(entries)} 部有巡礼数据的作品", current=3, total=3)
        result = PilgrimageTripResult(
            username=username,
            city_filter=city_key,
            entries=entries[:20],
            checked=len(subjects),
            caveats=[
                "城市字段为 anitabi 标注的主要取景城市；跨城作品可能未被城市过滤命中，可去掉过滤重查。",
                "数据来自 anitabi.cn 社区共建；实地探访请遵守当地秩序。",
            ],
        )
        sources = [
            Citation(title=f"anitabi — {e.title}（{e.point_count} 点）", url=e.map_url, source="anitabi", image=e.cover)
            for e in entries[:6]
        ]
        return ToolResult(ok=True, data=result, sources=sources)


def build_pilgrimage_tools(client: BangumiClient) -> list[Tool]:
    return [GetPilgrimageMapTool(client), PlanPilgrimageTripTool(client)]
