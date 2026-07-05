"""Pixiv data tools.

Pixiv has no stable public official API. These tools use PixivPy-Async only when
explicitly enabled and authenticated with a refresh_token. They never download or
rehost original images; outputs are metadata, thumbnails, tags and outbound links.
"""
from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ...config import settings
from .._cache import acached


class PixivRankingArgs(BaseModel):
    mode: Literal["day", "week", "month"] = Field("day", description="Pixiv 插画排行榜周期")
    limit: int = Field(20, ge=1, le=50)


class PixivIllustSearchArgs(BaseModel):
    query: str = Field(..., description="Pixiv 插画搜索关键词 / tag")
    limit: int = Field(20, ge=1, le=50)
    search_target: Literal["partial_match_for_tags", "exact_match_for_tags", "title_and_caption"] = (
        "partial_match_for_tags"
    )


class PixivArtistPortfolioArgs(BaseModel):
    artist_id: int | None = Field(None, description="Pixiv 用户 ID；优先使用 SauceNAO 返回的 user_id")
    artist_name: str | None = Field(None, description="画师名；无 artist_id 时 best-effort 搜索用户")
    limit: int = Field(20, ge=1, le=50)


class PixivIllust(BaseModel):
    pid: int
    title: str
    artist: str
    artist_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    thumb_url: str = ""
    url: str
    sanity_level: int | None = None
    x_restrict: int | None = None
    page_count: int | None = None


class PixivResult(BaseModel):
    query: str = ""
    mode: str = ""
    count: int = 0
    results: list[PixivIllust] = Field(default_factory=list)
    source: str = "pixiv"
    caveats: list[str] = Field(default_factory=list)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _items(obj: Any, key: str) -> list[Any]:
    value = _get(obj, key, [])
    return value if isinstance(value, list) else []


def _pixiv_enabled() -> tuple[bool, str]:
    if not settings.pixiv_enabled:
        return False, "PIXIV_ENABLED=false，Pixiv API 工具未启用。"
    if not settings.pixiv_refresh_token:
        return False, "缺少 PIXIV_REFRESH_TOKEN；请用小号 refresh_token，本地 demo 建议开启。"
    return True, ""


# access_token 官方有效期 1h；到期前复用同一已登录客户端，
# 避免每次工具调用都打 pixiv auth 端点（频繁 refresh 容易触发风控/invalid_grant）。
_API_TTL_SECONDS = 50 * 60
_api_instance: Any = None
_api_authed_at: float = 0.0


async def _open_pixiv_api():
    global _api_instance, _api_authed_at
    if _api_instance is not None and time.monotonic() - _api_authed_at < _API_TTL_SECONDS:
        return _api_instance
    try:
        from pixivpy_async import AppPixivAPI
    except ImportError as e:  # pragma: no cover - optional dependency
        raise RuntimeError("未安装 PixivPy-Async；请执行 pip install -e \".[pixiv]\"") from e
    kwargs: dict[str, Any] = {}
    if settings.pixiv_proxy:
        kwargs["proxy"] = settings.pixiv_proxy
    api = AppPixivAPI(**kwargs)
    await api.login(refresh_token=settings.pixiv_refresh_token)
    stale = _api_instance
    _api_instance, _api_authed_at = api, time.monotonic()
    if stale is not None:
        await _close_pixiv_api(stale)
    return api


def _invalidate_pixiv_api() -> None:
    """调用失败（token 失效/网络断）后重置，下次重新 login。"""
    global _api_instance, _api_authed_at
    _api_instance = None
    _api_authed_at = 0.0


async def _close_pixiv_api(api: Any) -> None:
    close = getattr(api, "close", None)
    if close:
        maybe = close()
        if hasattr(maybe, "__await__"):
            await maybe


def _is_safe_illust(raw: Any) -> bool:
    x_restrict = int(_get(raw, "x_restrict", 0) or 0)
    sanity = int(_get(raw, "sanity_level", 0) or 0)
    tags = [_tag_name(t).lower() for t in _items(raw, "tags")]
    return x_restrict <= 0 and sanity < 4 and not any(t in {"r-18", "r18", "r-18g"} for t in tags)


def _tag_name(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    name = _get(raw, "name", "")
    translated = _get(raw, "translated_name", "") or _get(raw, "translated", "")
    return str(translated or name or "").strip()


def _illust(raw: Any) -> PixivIllust | None:
    if not raw or not _is_safe_illust(raw):
        return None
    pid = int(_get(raw, "id", 0) or 0)
    if not pid:
        return None
    user = _get(raw, "user", {}) or {}
    image_urls = _get(raw, "image_urls", {}) or {}
    thumb = (
        _get(image_urls, "square_medium", "")
        or _get(image_urls, "medium", "")
        or _get(image_urls, "large", "")
    )
    return PixivIllust(
        pid=pid,
        title=str(_get(raw, "title", "") or ""),
        artist=str(_get(user, "name", "") or ""),
        artist_id=int(_get(user, "id", 0) or 0) or None,
        tags=[t for t in (_tag_name(tag) for tag in _items(raw, "tags")) if t][:12],
        thumb_url=str(thumb or ""),
        url=f"https://www.pixiv.net/artworks/{pid}",
        sanity_level=int(_get(raw, "sanity_level", 0) or 0),
        x_restrict=int(_get(raw, "x_restrict", 0) or 0),
        page_count=int(_get(raw, "page_count", 0) or 0) or None,
    )


def _collect_illusts(payload: Any, limit: int) -> list[PixivIllust]:
    raw_items = _items(payload, "illusts")
    out: list[PixivIllust] = []
    for raw in raw_items:
        item = _illust(raw)
        if item is not None:
            out.append(item)
        if len(out) >= limit:
            break
    return out


@acached(ttl=settings.pixiv_cache_ttl)
async def _pixiv_ranking(mode: str, limit: int) -> list[PixivIllust]:
    api = await _open_pixiv_api()
    try:
        payload = await api.illust_ranking(mode=mode)
    except Exception:
        _invalidate_pixiv_api()
        raise
    return _collect_illusts(payload, limit)


@acached(ttl=settings.pixiv_cache_ttl)
async def _pixiv_search(query: str, search_target: str, limit: int) -> list[PixivIllust]:
    api = await _open_pixiv_api()
    try:
        payload = await api.search_illust(query, search_target=search_target, sort="date_desc")
    except Exception:
        _invalidate_pixiv_api()
        raise
    return _collect_illusts(payload, limit)


@acached(ttl=settings.pixiv_cache_ttl)
async def _pixiv_user_illusts(artist_id: int, limit: int) -> list[PixivIllust]:
    api = await _open_pixiv_api()
    try:
        payload = await api.user_illusts(artist_id)
    except Exception:
        _invalidate_pixiv_api()
        raise
    return _collect_illusts(payload, limit)


@acached(ttl=settings.pixiv_cache_ttl)
async def _pixiv_resolve_artist(artist_name: str) -> int | None:
    api = await _open_pixiv_api()
    if not hasattr(api, "search_user"):
        return None
    try:
        payload = await api.search_user(artist_name)
    except Exception:
        _invalidate_pixiv_api()
        raise
    users = _items(payload, "user_previews") or _items(payload, "users")
    for raw in users:
        user = _get(raw, "user", raw)
        uid = int(_get(user, "id", 0) or 0)
        if uid:
            return uid
    return None


def _result_sources(items: list[PixivIllust]) -> list[Citation]:
    return [
        Citation(title=f"Pixiv — {it.title}", url=it.url, source="pixiv", image=it.thumb_url)
        for it in items[:8]
    ]


def _caveats() -> list[str]:
    return [
        "Pixiv 无稳定官方公开 API；本工具依赖 PixivPy-Async 与 refresh_token，公网部署默认建议关闭。",
        "已硬过滤 R18/x_restrict/sanity_level 高风险条目；只返回元数据、缩略图与外链，不下载或转存原图。",
        "Pixiv 是插画/同人话语源与创作者入口，不是作品事实源；事实需回到 Bangumi/VNDB/yuc 等源核验。",
    ]


class GetPixivRankingTool(Tool):
    name = "get_pixiv_ranking"
    description = "读取 Pixiv 插画排行榜元数据。仅在 PIXIV_ENABLED=true 且配置 refresh_token 时可用；默认过滤 R18。"
    args_model = PixivRankingArgs
    result_model = PixivResult

    async def run(self, args: PixivRankingArgs) -> ToolResult[PixivResult]:
        ok, reason = _pixiv_enabled()
        if not ok:
            return ToolResult(ok=False, error=reason)
        try:
            items = await _pixiv_ranking(args.mode, args.limit)
        except Exception as e:  # noqa: BLE001
            return ToolResult(ok=False, error=f"Pixiv 排行读取失败：{type(e).__name__}: {e}")
        return ToolResult(
            ok=True,
            data=PixivResult(mode=args.mode, count=len(items), results=items, caveats=_caveats()),
            sources=_result_sources(items),
        )


class SearchPixivIllustsTool(Tool):
    name = "search_pixiv_illusts"
    description = "按 tag/关键词搜索 Pixiv 插画元数据。用于同人图趋势、画风参考、SauceNAO 溯源后的延伸。"
    args_model = PixivIllustSearchArgs
    result_model = PixivResult

    async def run(self, args: PixivIllustSearchArgs) -> ToolResult[PixivResult]:
        ok, reason = _pixiv_enabled()
        if not ok:
            return ToolResult(ok=False, error=reason)
        query = args.query.strip()
        if not query:
            return ToolResult(ok=False, error="query 不能为空")
        try:
            items = await _pixiv_search(query, args.search_target, args.limit)
        except Exception as e:  # noqa: BLE001
            return ToolResult(ok=False, error=f"Pixiv 搜索失败：{type(e).__name__}: {e}")
        return ToolResult(
            ok=True,
            data=PixivResult(query=query, count=len(items), results=items, caveats=_caveats()),
            sources=_result_sources(items),
        )


class GetPixivArtistPortfolioTool(Tool):
    name = "get_pixiv_artist_portfolio"
    description = "读取 Pixiv 画师代表作元数据；优先传 artist_id，可由 SauceNAO/Pixiv 外链获得。"
    args_model = PixivArtistPortfolioArgs
    result_model = PixivResult

    async def run(self, args: PixivArtistPortfolioArgs) -> ToolResult[PixivResult]:
        ok, reason = _pixiv_enabled()
        if not ok:
            return ToolResult(ok=False, error=reason)
        artist_id = args.artist_id
        query = str(artist_id or args.artist_name or "").strip()
        if not artist_id and args.artist_name:
            try:
                artist_id = await _pixiv_resolve_artist(args.artist_name.strip())
            except Exception as e:  # noqa: BLE001
                return ToolResult(ok=False, error=f"Pixiv 画师搜索失败：{type(e).__name__}: {e}")
        if not artist_id:
            return ToolResult(ok=False, error="需要 artist_id；artist_name 解析失败或当前 PixivPy-Async 不支持 search_user")
        try:
            items = await _pixiv_user_illusts(artist_id, args.limit)
        except Exception as e:  # noqa: BLE001
            return ToolResult(ok=False, error=f"Pixiv 画师作品读取失败：{type(e).__name__}: {e}")
        return ToolResult(
            ok=True,
            data=PixivResult(query=query, count=len(items), results=items, caveats=_caveats()),
            sources=_result_sources(items),
        )


def build_pixiv_tools() -> list[Tool]:
    return [GetPixivRankingTool(), SearchPixivIllustsTool(), GetPixivArtistPortfolioTool()]
