"""圣地巡礼工具（anitabi.cn 开放 API，key 原生为 Bangumi subject id）。

数据含贡献者署名（origin 字段），展示时保留来源；截图为 anitabi 图床外链，不转存。
对公益站保持克制：持久磁盘缓存 7 天 + 低并发 + 请求间隔（曾因 300 连发触发 403）。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
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
    # anitabi may return either numeric episode sorts or labels like "EP3".
    episode: int | str | None = None
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
    city: str = Field("", description="可选：目的地简名，支持城市（东京/京都/热海/沼津）和区域（关西/关东/九州），按都市圈分层")
    lat: float | None = Field(None, description="目的地纬度：city 不在支持列表时（工具会在 caveat 提示），用你知道的该地坐标重调")
    lng: float | None = Field(None, description="目的地经度，与 lat 配对")
    max_subjects: int = Field(300, ge=5, le=1000, description="最多检查多少部（按用户评分从高到低截断；结果有 24h 缓存，重复查询很快）")


class PilgrimageTripEntry(BaseModel):
    subject_id: int
    title: str
    city: str = ""
    point_count: int = 0
    map_url: str = ""
    cover: str = ""
    tier: str = "core"  # core=目的地同城 / nearby=顺路近郊 / bonus=稍远的惊喜添头
    distance_km: int | None = None
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


def _episode_label(value: int | str | None) -> str:
    if value is None or value == "":
        return ""
    raw = str(value).strip()
    return raw if raw.lower().startswith("ep") else f"ep{raw}"


def _city_match(query: str, city: str) -> bool:
    """城市过滤用双向前缀匹配，不能用子串——"东京都"包含子串"京都"，
    朴素 `in` 会让东京作品穿透京都过滤（2026-07-05 用户实测踩坑）。
    前缀语义下："京都府/京都市".startswith("京都")=True，"东京都".startswith("京都")=False。"""
    q = query.strip()
    c = city.strip()
    if not q or not c:
        return False
    return c.startswith(q) or q.startswith(c)


# 目的地中心坐标 + 圈层半径 (lat, lng, nearby_km, bonus_km)：
# anitabi 的 city 是市级文本（"宇治市/秩父市"），名称匹配盖不住都市圈——
# "去东京玩"该带出饭能/鹫宫/秩父（埼玉），"飞大阪"该带出京都/神户，稍远的
# 和歌山/冈山作惊喜添头。geo 圈层按番剧中心坐标算距离分档，比枚举地名可靠。
_REGION_CENTERS: dict[str, tuple[float, float, float, float]] = {
    "东京": (35.681, 139.767, 85, 180),
    "关东": (35.681, 139.767, 130, 250),
    "首都圈": (35.681, 139.767, 130, 250),
    "大阪": (34.694, 135.502, 85, 180),
    "关西": (34.80, 135.60, 130, 250),
    "京阪神": (34.80, 135.60, 130, 250),
    "京都": (35.012, 135.768, 85, 180),
    "神户": (34.690, 135.196, 85, 180),
    "奈良": (34.685, 135.805, 60, 150),
    "名古屋": (35.181, 136.906, 85, 180),
    "横滨": (35.444, 139.638, 60, 150),
    "镰仓": (35.319, 139.547, 40, 120),
    "札幌": (43.062, 141.354, 80, 250),
    "北海道": (43.20, 142.50, 300, 500),
    "福冈": (33.590, 130.402, 70, 200),
    "九州": (32.80, 130.90, 220, 350),
    "仙台": (38.268, 140.869, 70, 200),
    "广岛": (34.385, 132.455, 70, 200),
    "金泽": (36.561, 136.656, 70, 200),
    "冲绳": (26.212, 127.679, 100, 300),
    # ACGN 旅游热点小城（半径小：本地 + 顺路圈）
    "热海": (35.096, 139.072, 40, 120),
    "箱根": (35.233, 139.107, 40, 120),
    "沼津": (35.096, 138.864, 40, 120),
    "静冈": (34.976, 138.383, 70, 180),
    "大洗": (36.313, 140.575, 40, 150),
    "宇治": (34.884, 135.800, 30, 100),
    "秩父": (35.992, 139.085, 40, 120),
    "饭能": (35.856, 139.328, 35, 120),
    "川越": (35.925, 139.486, 30, 100),
    "江之岛": (35.300, 139.480, 30, 100),
    "长野": (36.651, 138.181, 80, 200),
    "松本": (36.238, 137.972, 60, 150),
}


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    rlat1, rlng1, rlat2, rlng2 = map(radians, (lat1, lng1, lat2, lng2))
    a = sin((rlat2 - rlat1) / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin((rlng2 - rlng1) / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


def _classify_entry(
    query: str,
    city: str,
    geo: Any,
    center: tuple[float, float, float, float] | None = None,
) -> tuple[str, float | None] | None:
    """返回 (tier, distance_km)：core=同城/名称命中，nearby=顺路近郊，bonus=惊喜添头；None=不在圈内。

    center 可由调用方注入（LLM 传坐标兜内置表之外的长尾目的地）。"""
    if query and _city_match(query, city):
        return "core", None
    if center is None:
        center = _REGION_CENTERS.get(query.strip())
    if not center:
        return None  # 未知目的地且无坐标：只能名称匹配
    try:
        lat, lng = float(geo[0]), float(geo[1])
    except (TypeError, ValueError, IndexError):
        return None
    dist = _haversine_km(center[0], center[1], lat, lng)
    if dist <= 25:
        return "core", round(dist)
    if dist <= center[2]:
        return "nearby", round(dist)
    if dist <= center[3]:
        return "bonus", round(dist)
    return None


class AnitabiRateLimited(RuntimeError):
    """anitabi 返回 403/429（批量请求触发防护）。调用方应如实告知结果不完整。"""


# 持久磁盘缓存：anitabi 是公益站，巡礼数据变化慢——lite/points 结果落盘 7 天，
# miss 才发请求（含"未收录"也缓存，避免对同一部反复打）。批量扫描只有首次有成本。
_DISK_CACHE_PATH = Path("cache/anitabi_cache.json")
_DISK_TTL = 7 * 24 * 3600
_disk_cache: dict[str, Any] | None = None


def _disk_load() -> dict[str, Any]:
    global _disk_cache
    if _disk_cache is None:
        try:
            _disk_cache = json.loads(_DISK_CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _disk_cache = {}
    return _disk_cache


def _disk_get(url: str) -> Any:
    entry = _disk_load().get(url)
    if not isinstance(entry, dict) or (time.time() - entry.get("ts", 0)) > _DISK_TTL:
        return _MISS
    return entry.get("data")


def _disk_put(url: str, data: Any) -> None:
    cache = _disk_load()
    cache[url] = {"ts": time.time(), "data": data}
    try:
        _DISK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DISK_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


_MISS = object()


@acached(ttl=settings.cache_ttl * 24)
async def _fetch_anitabi(url: str) -> Any:
    cached = _disk_get(url)
    if cached is not _MISS:
        return cached

    async def fetch() -> Any:
        await asyncio.sleep(0.15)  # 公益站礼貌间隔：并发 2 × 间隔 ≈ 每秒 ~5 请求上限
        async with httpx.AsyncClient(
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.bangumi_user_agent},
        ) as client:
            res = await client.get(url)
            if res.status_code == 404:
                return None
            if res.status_code in {403, 429}:
                raise AnitabiRateLimited(f"anitabi {res.status_code}")
            res.raise_for_status()
            text = res.text.strip()
            return res.json() if text else None

    result = await gather_limited([fetch()], host="anitabi")
    first = result[0]
    if isinstance(first, BaseException):
        raise first
    _disk_put(url, first)  # 命中与"未收录(None)"都落盘；被限流的不缓存
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
        try:
            lite = await _fetch_anitabi(_ANITABI_LITE.format(sid=sid))
        except AnitabiRateLimited:
            return ToolResult(ok=False, error="anitabi 暂时限流（访问过于频繁），请过一会儿重试；不代表该作品没有巡礼数据")
        if not lite:
            return ToolResult(
                ok=False,
                error=f"anitabi 未收录《{title}》的巡礼数据（社区库覆盖以有实地取景的作品为主）",
            )
        try:
            detail = await _fetch_anitabi(_ANITABI_POINTS.format(sid=sid)) or []
        except AnitabiRateLimited:
            detail = []
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
            Citation(
                title=f"{pt.name}（{_episode_label(pt.episode)}）" if _episode_label(pt.episode) else pt.name,
                url=pt.google_maps_url or result.map_url,
                source="anitabi",
                image=pt.image,
            )
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
        # 按用户自己的评分从高到低排（无评分垫底），让截断留下的是"最爱的番"
        # 而不是"收藏最晚的番"（API 默认按收藏时间倒序返回）
        rows.sort(key=lambda r: -(r.get("rate") or 0))
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
        custom_center: tuple[float, float, float, float] | None = None
        if args.lat is not None and args.lng is not None:
            custom_center = (float(args.lat), float(args.lng), 60, 160)
        known_region = not city_key or city_key in _REGION_CENTERS or custom_center is not None
        rate_limited = sum(1 for x in lites if isinstance(x, AnitabiRateLimited))
        other_errors = sum(1 for x in lites if isinstance(x, BaseException) and not isinstance(x, AnitabiRateLimited))
        for (sid, name), lite in zip(subjects, lites, strict=False):
            if isinstance(lite, BaseException) or not lite:
                continue
            city = str(lite.get("city") or "")
            tier: str = "core"
            distance: int | None = None
            if city_key or custom_center:
                classified = _classify_entry(city_key, city, lite.get("geo"), center=custom_center)
                if classified is None:
                    continue
                tier, distance = classified
            pts = lite.get("litePoints") or []
            entries.append(
                PilgrimageTripEntry(
                    subject_id=sid,
                    title=str(lite.get("cn") or name),
                    city=city,
                    point_count=int(lite.get("pointsLength") or len(pts) or 0),
                    map_url=_ANITABI_MAP.format(sid=sid),
                    cover=str(lite.get("cover") or ""),
                    tier=tier,
                    distance_km=distance,
                    sample_points=[str(p.get("name")) for p in pts[:3] if p.get("name")],
                )
            )
        tier_rank = {"core": 0, "nearby": 1, "bonus": 2}
        entries.sort(key=lambda e: (tier_rank.get(e.tier, 3), -e.point_count))
        progress_note = (
            f"⚠ anitabi 限流（{rate_limited}/{len(subjects)}），结果不完整"
            if rate_limited else f"命中 {len(entries)} 部有巡礼数据的作品"
        )
        await emit_tool_progress(tool=self.name, summary=progress_note, current=3, total=3)
        result = PilgrimageTripResult(
            username=username,
            city_filter=city_key,
            entries=entries[:24],
            checked=len(subjects),
            caveats=[
                *(
                    [f"⚠ anitabi 访问受限（{rate_limited}/{len(subjects)} 个请求被限流），本次结果**不完整**，请过一会儿重试；不代表这些作品没有巡礼数据。"]
                    if rate_limited else []
                ),
                *(
                    [f"{other_errors} 个请求失败（网络波动），结果可能略有遗漏。"]
                    if other_errors > len(subjects) // 5 else []
                ),
                "候选按你的评分从高到低检查（最爱优先）。",
                (
                    "目的地按都市圈分层：core=同城、nearby=顺路近郊（如东京→饭能/秩父）、bonus=稍远惊喜（如大阪→冈山），距离为番剧取景中心到目的地的直线距离。"
                    if known_region and city_key else
                    "该目的地不在内置都市圈表中，仅做了城市名前缀匹配——**请带上该地经纬度（lat/lng 参数）重调本工具**即可获得圈层推荐。"
                    if city_key else
                    "未指定目的地，列出全部有巡礼数据的作品。"
                ),
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
