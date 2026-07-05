"""Offline release/feed aggregation tools.

These tools only aggregate public links/RSS metadata. Otomo does not proxy,
download, host, seed, or play any release content.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
import html as html_lib
import json
from pathlib import Path
import re
import secrets
import time
from typing import Any, Literal
from urllib.parse import quote
import xml.etree.ElementTree as ET

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...agent._common import emit_tool_progress
from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from ...memory import LongTermMemory
from ...memory.consolidate import now_iso
from ...memory.models import DecisionLogItem, MemorySummary, PendingWriteAction, memory_summary
from .._cache import acached
from .._concurrency import gather_limited
from ..bangumi.client import SUBJECT_TYPE, BangumiClient

_MIKAN_MAP_URLS = [
    "https://raw.githubusercontent.com/xiaoyvyv/bangumi-data/main/data/mikan/bangumi-mikan.json",
    "https://cdn.jsdelivr.net/gh/xiaoyvyv/bangumi-data@main/data/mikan/bangumi-mikan.json",
]
_MIKAN_RSS = "https://mikanani.me/RSS/Bangumi?bangumiId={id}"
_MIKAN_RSS_SUB = "https://mikanani.me/RSS/Bangumi?bangumiId={id}&subgroupid={sub}"
_MIKAN_SEARCH = "https://mikanani.me/Home/Search?searchstr={q}"
# 站内搜索 RSS（2026-07-05 实测）：新番蜜柑常先有发布记录、后建番组页，
# 番组路径全 miss 时用它兜底，URL 本身即可订阅
_MIKAN_SEARCH_RSS = "https://mikanani.me/RSS/Search?searchstr={q}"
_MIKAN_BANGUMI_PAGE = "https://mikanani.me/Home/Bangumi/{id}"
# 搜索页：Home/Bangumi/{id} 链接与 an-text 标题之间隔着封面 <span>（2026-07-05 实测），
# 用 0~600 字符的非贪婪窗口跨过去，同时防止吞到下一张卡片的标题
_MIKAN_SEARCH_RE = re.compile(r'Home/Bangumi/(\d+)"[^>]*>.{0,600}?<div class="an-text"[^>]*title="([^"]+)"', re.S)
# 番剧页：subgroup-text" id="1230" ...>字幕组名（同日实测；页内自带 subgroupid RSS 链接）
_MIKAN_SUBGROUP_RE = re.compile(r'class="subgroup-text"\s+id="(\d+)"[^>]*>\s*<a[^>]*>([^<]+)</a>', re.S)
_DMHY_RSS = "https://share.dmhy.org/topics/rss/rss.xml?keyword={q}"
_DMHY_SEARCH = "https://share.dmhy.org/topics/list?keyword={q}"
_ACGNX_RSS = "https://share.acgnx.se/rss.xml?keyword={q}"
_ACGNX_SEARCH = "https://share.acgnx.se/search.php?keyword={q}"
_VCB_SEARCH = "https://vcb-s.com/?s={q}"
_TORRENT_NS = {"torrent": "https://mikanani.me/0.1/"}


class ReleaseItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    source: str
    page_url: str = ""
    torrent_url: str = ""
    magnet: str = ""
    size_bytes: int | None = None
    pub_date: str = ""
    subgroup: str = ""
    quality: str = "tv"
    note: str = ""


class ReleaseGroup(BaseModel):
    source: str
    subgroup: str = ""
    rss_url: str = ""
    quality: str = "tv"
    latest_items: list[ReleaseItem] = Field(default_factory=list)


class ReleaseSearchLink(BaseModel):
    label: str
    url: str
    source: str
    note: str = ""


class AnimeReleaseFeedsArgs(BaseModel):
    subject_id: int | None = Field(None, description="Bangumi 动画 subject_id；优先使用")
    title: str = Field("", description="动画标题；subject_id 为空时用于搜索 Bangumi / RSS")
    prefer: Literal["auto", "mikan", "bt", "bd"] = Field("auto", description="auto/mikan/bt/bd；BD 收藏优先设 bd")
    subgroup_filter: str = Field("", description="可选字幕组过滤，如 喵萌")
    limit: int = Field(12, ge=1, le=30)


class AnimeReleaseFeedsResult(BaseModel):
    subject_id: int | None = None
    title: str
    mikan_ids: list[int] = Field(default_factory=list)
    mapping_confidence: float = 0.0
    groups: list[ReleaseGroup] = Field(default_factory=list)
    fallback_items: list[ReleaseItem] = Field(default_factory=list)
    search_links: list[ReleaseSearchLink] = Field(default_factory=list)
    offline_hint: bool = True
    caveats: list[str] = Field(default_factory=list)


class PrepareDownloaderPushArgs(BaseModel):
    username: str | None = Field(None, description="Bangumi 用户名；不传则使用当前 token 账号")
    torrent_url: str = Field("", description="torrent URL")
    magnet: str = Field("", description="magnet 链接")
    title: str = Field("", description="用于确认弹窗的资源标题")
    subject_id: int | None = None
    subject_name: str = ""
    category: str = ""
    save_path: str = ""
    paused: bool = False
    reason: str = "用户确认后推送到自己的 qBittorrent"


class DownloaderPushActionResult(BaseModel):
    username: str
    action: PendingWriteAction
    requires_confirmation: bool = True
    warning: str
    memory: MemorySummary


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


async def _username(client: BangumiClient, username: str | None) -> str | None:
    if username:
        return username
    try:
        me = await client.get_me()
    except Exception:  # noqa: BLE001
        return None
    return str(me.get("username") or me.get("id") or "") or None


def _map_cache_path() -> Path:
    path = Path(settings.mikan_mapping_cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _fresh(path: Path, ttl: float) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) <= ttl


async def _download_mikan_map() -> Any:
    async def fetch(url: str) -> Any:
        async with httpx.AsyncClient(
            timeout=settings.release_feed_timeout,
            headers={"User-Agent": settings.bangumi_user_agent},
            follow_redirects=True,
        ) as client:
            res = await client.get(url)
            res.raise_for_status()
            return res.json()

    last: Exception | None = None
    for url in _MIKAN_MAP_URLS:
        result = await gather_limited([fetch(url)], host="mikan")
        first = result[0]
        if not isinstance(first, BaseException):
            return first
        last = first
    if last:
        raise last
    return []


def _iter_mapping_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for key in ("data", "items", "mapping", "mikan"):
            value = raw.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        # 平面 dict 的方向是 {mikan_id: bangumi_subject_id}（2026-07-05 实测：
        # key 全部为 183~4042 的 mikan 番剧 id，value 为万~几十万级的 subject_id）。
        rows: list[dict[str, Any]] = []
        for mikan_id, value in raw.items():
            if isinstance(value, dict):
                row = dict(value)
                row.setdefault("mikan_id", mikan_id)
                rows.append(row)
            elif isinstance(value, list):
                for bid in value:
                    rows.append({"mikan_id": mikan_id, "bangumi_id": bid})
            elif isinstance(value, (str, int)):
                rows.append({"mikan_id": mikan_id, "bangumi_id": value})
        return rows
    return []


def _int_values(value: Any) -> list[int]:
    vals = value if isinstance(value, list) else [value]
    out: list[int] = []
    for item in vals:
        try:
            n = int(item)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.append(n)
    return out


def _reverse_mikan_map(raw: Any) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for row in _iter_mapping_rows(raw):
        bgm = (
            row.get("bangumi_id")
            or row.get("bangumiId")
            or row.get("bgm")
            or row.get("bgm_id")
            or row.get("subject_id")
            or row.get("subjectId")
        )
        mids = (
            row.get("mikan_id")
            or row.get("mikanId")
            or row.get("mikan")
            or row.get("id")
            or row.get("ids")
        )
        bgm_ids = _int_values(bgm)
        mikan_ids = _int_values(mids)
        for bid in bgm_ids:
            out.setdefault(bid, [])
            for mid in mikan_ids:
                if mid not in out[bid]:
                    out[bid].append(mid)
    return out


async def load_mikan_mapping() -> dict[int, list[int]]:
    path = _map_cache_path()
    if _fresh(path, settings.mikan_mapping_cache_ttl):
        try:
            return _reverse_mikan_map(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    try:
        raw = await _download_mikan_map()
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        return _reverse_mikan_map(raw)
    except Exception:
        if path.exists():
            return _reverse_mikan_map(json.loads(path.read_text(encoding="utf-8")))
        raise


@acached(ttl=settings.release_feed_cache_ttl)
async def _fetch_text(url: str, host: str) -> str:
    async def fetch() -> str:
        async with httpx.AsyncClient(
            timeout=settings.release_feed_timeout,
            headers={"User-Agent": settings.bangumi_user_agent},
            follow_redirects=True,
        ) as client:
            res = await client.get(url)
            res.raise_for_status()
            return res.text

    result = await gather_limited([fetch()], host=host)
    first = result[0]
    if isinstance(first, BaseException):
        raise first
    return first


def _subgroup(title: str) -> str:
    # 字幕组前缀有半角 [喵萌奶茶屋] 和全角 【TSDM字幕组】 两种惯例
    m = re.match(r"\s*(?:\[([^\]]+)\]|【([^】]+)】)", title)
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()


def _quality(title: str) -> str:
    lower = title.lower()
    if any(k in lower for k in ("bdrip", "blu-ray", "bluray", "bd 1080", "bd1080", "bdmv")):
        return "bd"
    if "web" in lower or "webrip" in lower:
        return "web"
    if "1080" in lower or "2160" in lower or "4k" in lower:
        return "hd"
    return "tv"


def _iso_pub(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return parsedate_to_datetime(text).isoformat()
    except Exception:  # noqa: BLE001
        return text


def _text(node: ET.Element, name: str) -> str:
    found = node.find(name)
    return (found.text or "").strip() if found is not None and found.text else ""


def _parse_rss(xml: str, source: str) -> list[ReleaseItem]:
    root = ET.fromstring(xml)
    rows: list[ReleaseItem] = []
    for item in root.findall(".//item"):
        title = _text(item, "title")
        link = _text(item, "link")
        pub = _iso_pub(_text(item, "pubDate") or (item.findtext("torrent:pubDate", namespaces=_TORRENT_NS) or ""))
        enclosure = item.find("enclosure")
        torrent_url = ""
        magnet = ""
        size: int | None = None
        if enclosure is not None:
            url = str(enclosure.attrib.get("url") or "")
            if url.startswith("magnet:"):
                magnet = url
            else:
                torrent_url = url
            try:
                size = int(enclosure.attrib.get("length") or 0) or None
            except ValueError:
                size = None
        if not size:
            value = item.findtext("torrent:contentLength", namespaces=_TORRENT_NS) or ""
            try:
                size = int(value) if value else None
            except ValueError:
                size = None
        if not torrent_url:
            torrent_url = item.findtext("torrent:link", namespaces=_TORRENT_NS) or ""
        rows.append(
            ReleaseItem(
                title=title,
                source=source,
                page_url=link,
                torrent_url=torrent_url,
                magnet=magnet,
                size_bytes=size,
                pub_date=pub,
                subgroup=_subgroup(title),
                quality=_quality(title),
            )
        )
    return rows


async def fetch_release_items_from_url(url: str, source: str) -> list[ReleaseItem]:
    return _parse_rss(await _fetch_text(url, source), source)


def _filter_items(items: list[ReleaseItem], subgroup_filter: str, limit: int) -> list[ReleaseItem]:
    if subgroup_filter.strip():
        key = subgroup_filter.strip().lower()
        items = [x for x in items if key in x.subgroup.lower() or key in x.title.lower()]
    items.sort(key=lambda x: x.pub_date, reverse=True)
    return items[:limit]


def _group_items(items: list[ReleaseItem], source: str, rss_url: str) -> list[ReleaseGroup]:
    groups: dict[tuple[str, str], ReleaseGroup] = {}
    for item in items:
        key = (item.subgroup or "未分组", item.quality)
        group = groups.setdefault(
            key,
            ReleaseGroup(source=source, subgroup=item.subgroup or "未分组", rss_url=rss_url, quality=item.quality),
        )
        group.latest_items.append(item)
    for group in groups.values():
        group.latest_items.sort(key=lambda x: x.pub_date, reverse=True)
        group.latest_items = group.latest_items[:8]
    return sorted(groups.values(), key=lambda g: (g.quality != "bd", g.subgroup))[:12]


def _norm_cmp(value: str) -> str:
    return "".join(ch.lower() for ch in html_lib.unescape(value or "") if ch.isalnum())


async def _search_mikan_ids(title: str, limit: int = 2) -> list[tuple[int, str, float]]:
    """映射表 miss 时的实时兜底：蜜柑站内搜索 → 标题相似度匹配 → (mikan_id, 命中标题, 相似度)。

    映射表只覆盖挂了 bangumi 链接的番剧（实测 3120/约3900，缺口 ~19%），
    实时搜索能把这部分找回来，而不是直接退到搜索页外链。"""
    query = _norm_cmp(title)
    if not query:
        return []
    html = await _fetch_text(_MIKAN_SEARCH.format(q=quote(title)), "mikan")
    scored: dict[int, tuple[str, float]] = {}
    for mid_str, raw_title in _MIKAN_SEARCH_RE.findall(html):
        mid = int(mid_str)
        cand = html_lib.unescape(raw_title).strip()
        score = SequenceMatcher(None, query, _norm_cmp(cand)).ratio()
        if mid not in scored or score > scored[mid][1]:
            scored[mid] = (cand, score)
    ranked = sorted(
        ((mid, name, score) for mid, (name, score) in scored.items() if score >= 0.5),
        key=lambda x: -x[2],
    )
    return ranked[:limit]


async def _subgroup_rss_map(mikan_id: int) -> dict[str, str]:
    """番剧页解析字幕组 → 精确 RSS（页内自带 bangumiId+subgroupid 链接结构）。"""
    try:
        html = await _fetch_text(_MIKAN_BANGUMI_PAGE.format(id=mikan_id), "mikan")
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for sid, name in _MIKAN_SUBGROUP_RE.findall(html):
        clean = html_lib.unescape(name).strip()
        if clean:
            out[clean] = _MIKAN_RSS_SUB.format(id=mikan_id, sub=sid)
    return out


def _attach_precise_rss(groups: list[ReleaseGroup], subgroup_rss: dict[str, str]) -> None:
    """把条目级混合 RSS 升级为字幕组精确 RSS：组名与番剧页字幕组名互相包含即命中。"""
    for group in groups:
        gname = _norm_cmp(group.subgroup)
        if not gname or group.source != "mikan":
            continue
        for name, rss in subgroup_rss.items():
            n = _norm_cmp(name)
            if n and (gname in n or n in gname):
                group.rss_url = rss
                break


async def _resolve_subject(client: BangumiClient, subject_id: int | None, title: str) -> tuple[int | None, str, str | None]:
    if subject_id:
        raw = await client.get_subject(subject_id)
        return subject_id, str(raw.get("name_cn") or raw.get("name") or title or subject_id), (raw.get("images") or {}).get("common")
    if title.strip():
        raw = await client.search_subjects(title, SUBJECT_TYPE["anime"], limit=5)
        rows = raw.get("data") or []
        if rows:
            best = rows[0]
            return int(best["id"]), str(best.get("name_cn") or best.get("name") or title), (best.get("images") or {}).get("common")
    return None, title.strip(), None


class GetAnimeReleaseFeedsTool(Tool):
    name = "get_anime_release_feeds"
    description = (
        "聚合动画离线 release/RSS 入口：Mikan 映射与 RSS、DMHY/末日资源库 RSS 兜底、VCB-Studio/BD 搜索入口。"
        "用于『动画下载/RSS/字幕组/BD/资源/订阅喵萌』。只返回链接与元数据，不下载、不托管、不代理内容。"
    )
    args_model = AnimeReleaseFeedsArgs
    result_model = AnimeReleaseFeedsResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: AnimeReleaseFeedsArgs) -> ToolResult[AnimeReleaseFeedsResult]:
        await emit_tool_progress(tool=self.name, summary="解析 Bangumi 动画条目", current=1, total=4)
        subject_id, title, image = await _resolve_subject(self.client, args.subject_id, args.title)
        if not title:
            return ToolResult(ok=False, error="需要 subject_id 或 title")
        q = quote(title)
        groups: list[ReleaseGroup] = []
        fallback_items: list[ReleaseItem] = []
        mikan_ids: list[int] = []
        mapping_confidence = 0.0
        await emit_tool_progress(tool=self.name, summary="读取 Mikan 映射与 RSS", current=2, total=4)
        search_matched = ""
        if args.prefer in {"auto", "mikan"}:
            try:
                if subject_id:
                    mapping = await load_mikan_mapping()
                    mikan_ids = mapping.get(subject_id, [])[:5]
                    mapping_confidence = 0.95 if mikan_ids else 0.0
                if not mikan_ids:
                    # 映射表缺口 ~19%：实时搜索蜜柑站内找回，标低置信
                    matches = await _search_mikan_ids(title)
                    mikan_ids = [mid for mid, _name, _score in matches]
                    if matches:
                        mapping_confidence = round(0.6 * matches[0][2] + 0.3, 2)
                        search_matched = matches[0][1]
                jobs = [fetch_release_items_from_url(_MIKAN_RSS.format(id=mid), "mikan") for mid in mikan_ids]
                # 外层用普通 gather：内层 _fetch_text 已按 host 信号量限流，
                # 这里再套 gather_limited(host="mikan") 会在 mikan_ids >= 上限(2)时嵌套死锁。
                rss_results = await asyncio.gather(*jobs, return_exceptions=True) if jobs else []
                for mid, rows in zip(mikan_ids, rss_results, strict=False):
                    if isinstance(rows, BaseException):
                        continue
                    rss_url = _MIKAN_RSS.format(id=mid)
                    items = _filter_items(rows, args.subgroup_filter, args.limit)
                    groups.extend(_group_items(items, "mikan", rss_url))
                if groups and mikan_ids:
                    # 条目级混合 RSS → 字幕组精确 RSS（番剧页自带 subgroupid 链接）
                    subgroup_rss = await _subgroup_rss_map(mikan_ids[0])
                    _attach_precise_rss(groups, subgroup_rss)
                if not groups:
                    # 第三级：番组页还没建（新番常见）但站内已有发布记录 → 搜索 RSS
                    search_rss_url = _MIKAN_SEARCH_RSS.format(q=q)
                    rows = await fetch_release_items_from_url(search_rss_url, "mikan")
                    items = _filter_items(rows, args.subgroup_filter, args.limit)
                    if items:
                        groups.extend(_group_items(items, "mikan", search_rss_url))
                        mapping_confidence = max(mapping_confidence, 0.5)
                        search_matched = search_matched or f"站内搜索 RSS（{len(items)} 条发布记录）"
            except Exception:  # noqa: BLE001
                mapping_confidence = mapping_confidence or 0.0
        await emit_tool_progress(tool=self.name, summary="读取 DMHY / ACGNX RSS 兜底", current=3, total=4)
        if args.prefer in {"auto", "bt", "bd"} or not groups:
            # BD 收藏意图：VCB-Studio 的发布本来就走 dmhy/acgnx 等 BT 站（2026-07-05 实测
            # acgnx 搜 "VCB K-ON" 直接命中），带前缀检索即可磁力直出；无果再退 BDRip 通用词。
            bt_queries = [f"VCB-Studio {title}", f"{title} BDRip"] if args.prefer == "bd" else [title]
            for bt_query in bt_queries:
                bt_q = quote(bt_query)
                for source, url in (("dmhy", _DMHY_RSS.format(q=bt_q)), ("acgnx", _ACGNX_RSS.format(q=bt_q))):
                    try:
                        rows = await fetch_release_items_from_url(url, source)
                    except Exception:  # noqa: BLE001
                        continue
                    fallback_items.extend(_filter_items(rows, args.subgroup_filter, args.limit))
                if fallback_items:
                    break
        if args.prefer == "bd":
            vcb_q = quote(f"VCB-Studio {title}")
            groups.append(
                ReleaseGroup(
                    source="vcb",
                    subgroup="VCB-Studio",
                    rss_url="",
                    quality="bd",
                    latest_items=[
                        ReleaseItem(
                            title=f"VCB-Studio {title} / BDRip 搜索入口",
                            source="vcb",
                            page_url=_VCB_SEARCH.format(q=vcb_q),
                            quality="bd",
                            note="VCB-Studio 是 BD/BDRip 搜索入口；Otomo 不抓取详情、不下载内容。",
                        )
                    ],
                )
            )
        search_links = [
            ReleaseSearchLink(label="Mikan 搜索", url=_MIKAN_SEARCH.format(q=q), source="mikan", note="优先用于当季 TV/RSS"),
            ReleaseSearchLink(label="DMHY 搜索", url=_DMHY_SEARCH.format(q=q), source="dmhy", note="BT/RSS 兜底"),
            ReleaseSearchLink(label="末日资源库搜索", url=_ACGNX_SEARCH.format(q=q), source="acgnx", note="BT/RSS 兜底"),
            ReleaseSearchLink(label="VCB-Studio 搜索", url=_VCB_SEARCH.format(q=quote(title)), source="vcb", note="BD/收藏版入口"),
        ]
        # dmhy 与 acgnx 内容高度重叠（互相搬运），按磁力 btih / 标题去重
        deduped: dict[str, ReleaseItem] = {}
        for item in fallback_items:
            m = re.search(r"btih:([0-9a-zA-Z]+)", item.magnet)
            key = m.group(1).lower() if m else item.title.strip().lower()
            deduped.setdefault(key, item)
        fallback_items = sorted(deduped.values(), key=lambda x: x.pub_date, reverse=True)[: args.limit]
        await emit_tool_progress(tool=self.name, summary=f"资源聚合完成：{len(groups)} 组 / {len(fallback_items)} 条兜底", current=4, total=4)
        result = AnimeReleaseFeedsResult(
            subject_id=subject_id,
            title=title,
            mikan_ids=mikan_ids,
            mapping_confidence=mapping_confidence,
            groups=groups[:12],
            fallback_items=fallback_items,
            search_links=search_links,
            offline_hint=True,
            caveats=[
                "Otomo 只聚合公开 RSS/搜索链接，不代理、不下载、不托管、不播放任何资源内容。",
                "字幕组标题和资源质量来自 RSS 标题启发式解析，最终以源站页面为准。",
                "BD/VCB-Studio 入口默认只给搜索链接；用户自行确认版权、地区和个人使用合规性。",
                *(
                    [
                        f"蜜柑番组页暂未收录该番，结果来自{search_matched}；此 RSS 按标题检索，可能混入同名条目。"
                        if search_matched.startswith("站内搜索 RSS")
                        else f"蜜柑映射表未收录，已用站内搜索匹配到《{search_matched}》（弱关联，请确认是同一部作品）。"
                    ]
                    if search_matched else []
                ),
            ],
        )
        sources = [Citation(title=title, url=f"https://bgm.tv/subject/{subject_id}", source="bangumi", image=image)] if subject_id else []
        sources.extend(Citation(title=x.label, url=x.url, source=x.source) for x in search_links[:4])
        return ToolResult(ok=True, data=result, sources=sources[:8])


class PrepareDownloaderPushTool(Tool):
    name = "prepare_downloader_push"
    description = (
        "把用户选中的 torrent URL 或 magnet 准备为 qBittorrent 推送动作。只生成待确认动作，不会立即推送；"
        "最终必须由前端确认按钮执行。"
    )
    args_model = PrepareDownloaderPushArgs
    result_model = DownloaderPushActionResult

    def __init__(self, client: BangumiClient, ltm: LongTermMemory) -> None:
        self.client = client
        self.ltm = ltm

    async def run(self, args: PrepareDownloaderPushArgs) -> ToolResult[DownloaderPushActionResult]:
        username = await _username(self.client, args.username)
        if not username:
            return ToolResult(ok=False, error="需要先绑定 Bangumi 账号，才能记录并确认下载器推送动作")
        url = args.magnet.strip() or args.torrent_url.strip()
        if not url:
            return ToolResult(ok=False, error="需要 torrent_url 或 magnet")
        if not settings.qbittorrent_url.strip():
            return ToolResult(ok=False, error="qBittorrent 未配置，无法准备推送动作")
        mem = self.ltm.load_user(username)
        title = args.title.strip() or args.subject_name.strip() or "未命名资源"
        action = PendingWriteAction(
            id=_new_id("dl"),
            operation="push_downloader",
            summary=f"推送《{title}》到 qBittorrent",
            subject_id=args.subject_id,
            subject_name=args.subject_name.strip() or title,
            payload={
                "url": url,
                "category": args.category or settings.qbittorrent_category,
                "save_path": args.save_path or settings.qbittorrent_save_path,
                "paused": args.paused,
                "title": title,
            },
            status="pending",
            created_at=now_iso(),
            source="release_feed",
        )
        mem.pending_write_actions.append(action)
        mem.pending_write_actions = mem.pending_write_actions[-80:]
        mem.decision_log.append(
            DecisionLogItem(
                id=_new_id("dec"),
                kind="defer",
                subject_id=args.subject_id,
                subject_name=action.subject_name,
                operation="push_downloader:prepare",
                reason=args.reason,
                action_id=action.id,
                confirmed=False,
                source="release_feed",
                ts=now_iso(),
            )
        )
        self.ltm.save_user(mem)
        return ToolResult(
            ok=True,
            data=DownloaderPushActionResult(
                username=username,
                action=action,
                warning="这是对你自己 qBittorrent WebUI 的真实写操作；前端确认前不会执行。",
                memory=memory_summary(mem),
            ),
        )


def build_release_tools(client: BangumiClient, ltm: LongTermMemory | None = None) -> list[Tool]:
    tools: list[Tool] = [GetAnimeReleaseFeedsTool(client)]
    if ltm is not None:
        tools.append(PrepareDownloaderPushTool(client, ltm))
    return tools
