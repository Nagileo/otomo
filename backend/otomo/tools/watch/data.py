"""bangumi-data integration for official streaming/source links."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from ...config import settings
from .._concurrency import gather_limited


@dataclass
class BangumiDataSite:
    site: str
    site_name: str
    url: str
    regions: list[str]
    type: str = ""
    source: str = "bangumi-data"
    official: bool = True


_MEM: tuple[float, dict[str, Any]] | None = None


def _cache_path() -> Path:
    path = Path(settings.bangumi_data_cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) <= settings.bangumi_data_cache_ttl


async def _download_data() -> dict[str, Any]:
    async def fetch() -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.bangumi_user_agent, "Accept": "application/json"},
            follow_redirects=True,
        ) as client:
            res = await client.get(settings.bangumi_data_url)
            res.raise_for_status()
            return res.json()

    result = await gather_limited([fetch()], host="bangumi_data")
    first = result[0]
    if isinstance(first, BaseException):
        raise first
    return first


async def load_bangumi_data(*, force_refresh: bool = False) -> dict[str, Any]:
    global _MEM
    if _MEM and not force_refresh and (time.monotonic() - _MEM[0]) < settings.bangumi_data_cache_ttl:
        return _MEM[1]
    path = _cache_path()
    if not force_refresh and _fresh(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _MEM = (time.monotonic(), data)
            return data
        except (OSError, json.JSONDecodeError):
            pass
    try:
        data = await _download_data()
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _MEM = (time.monotonic(), data)
        return data
    except Exception:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            _MEM = (time.monotonic(), data)
            return data
        raise


def _ids_for_item(item: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("bgm", "bangumi", "bangumiId", "subject_id"):
        value = item.get(key)
        if value is not None:
            ids.add(str(value))
    sites = item.get("sites")
    if isinstance(sites, list):
        for site in sites:
            if isinstance(site, dict):
                sid = site.get("site") or site.get("siteId") or ""
                if str(sid).lower() in {"bangumi", "bgm"} and site.get("id") is not None:
                    ids.add(str(site.get("id")))
    return ids


def _titles_for_item(item: dict[str, Any]) -> set[str]:
    values = item.get("title")
    titles: set[str] = set()
    if isinstance(values, str):
        titles.add(values)
    elif isinstance(values, dict):
        titles.update(str(v) for v in values.values() if v)
    for key in ("titleTranslate", "title_translate", "aliases"):
        val = item.get(key)
        if isinstance(val, dict):
            for arr in val.values():
                if isinstance(arr, list):
                    titles.update(str(x) for x in arr if x)
                elif arr:
                    titles.add(str(arr))
        elif isinstance(val, list):
            titles.update(str(x) for x in val if x)
    return titles


def norm_title(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _render(template: str, site_id: str, item: dict[str, Any]) -> str:
    values = {
        "id": site_id,
        "siteId": site_id,
        "site_id": site_id,
    }
    return re.sub(r"\{\{\s*([a-zA-Z_][\w-]*)\s*\}\}", lambda m: str(values.get(m.group(1), site_id)), template)


def _site_meta(data: dict[str, Any], site: str) -> dict[str, Any]:
    meta = data.get("siteMeta") or data.get("sites") or {}
    if isinstance(meta, dict):
        val = meta.get(site) or meta.get(str(site))
        if isinstance(val, dict):
            return val
    return {}


def _site_is_onair(site_row: dict[str, Any], meta: dict[str, Any]) -> bool:
    row_type = str(site_row.get("type") or meta.get("type") or "").lower()
    return not row_type or row_type == "onair"


def _regions(site_row: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    val = site_row.get("regions") or site_row.get("region") or meta.get("regions") or meta.get("region") or []
    if isinstance(val, str):
        return [val]
    if isinstance(val, list):
        return [str(x) for x in val if x]
    return []


def _site_url(data: dict[str, Any], site_row: dict[str, Any], item: dict[str, Any]) -> str:
    if site_row.get("url"):
        return str(site_row["url"])
    meta = _site_meta(data, str(site_row.get("site") or ""))
    template = str(meta.get("urlTemplate") or meta.get("url_template") or "")
    if not template:
        return ""
    site_id = str(site_row.get("id") or site_row.get("siteId") or "")
    return _render(template, site_id, item)


def official_sites_for_item(data: dict[str, Any], item: dict[str, Any]) -> list[BangumiDataSite]:
    rows = item.get("sites") or []
    out: list[BangumiDataSite] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        site = str(row.get("site") or "")
        if not site:
            continue
        meta = _site_meta(data, site)
        if not _site_is_onair(row, meta):
            continue
        url = _site_url(data, row, item)
        if not url:
            continue
        out.append(
            BangumiDataSite(
                site=site,
                site_name=str(meta.get("title") or meta.get("name") or site),
                url=url,
                regions=_regions(row, meta),
                type=str(row.get("type") or meta.get("type") or "onair"),
            )
        )
    out.sort(key=lambda x: (0 if "CN" in x.regions or "cn" in [r.lower() for r in x.regions] else 1, x.site_name))
    return out


def find_item(data: dict[str, Any], *, subject_id: int | None = None, title: str = "") -> tuple[dict[str, Any] | None, str]:
    items = data.get("items") or data.get("data") or []
    if not isinstance(items, list):
        return None, "invalid_data"
    if subject_id is not None:
        sid = str(subject_id)
        for item in items:
            if isinstance(item, dict) and sid in _ids_for_item(item):
                return item, "bangumi_id"
    ntitle = norm_title(title)
    if ntitle:
        for item in items:
            if not isinstance(item, dict):
                continue
            titles = {norm_title(x) for x in _titles_for_item(item)}
            if ntitle and ntitle in titles:
                return item, "title_exact"
        for item in items:
            if not isinstance(item, dict):
                continue
            titles = {norm_title(x) for x in _titles_for_item(item)}
            if any(ntitle and t and (ntitle in t or t in ntitle) and min(len(ntitle), len(t)) >= 4 for t in titles):
                return item, "title_partial"
    return None, "not_found"
