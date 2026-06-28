"""通用进程内 TTL 缓存（Phase 2）。

Bangumi 走 `BangumiClient._TTLCache` 已有缓存；本模块给**不走 client 的外部源**
（EGS / yuc / B站 / 好友页 HTML）提供同款轻量缓存：按参数缓存 fetch 返回值。
礼貌限流 + 稳 demo。失败（抛异常）不缓存。上线再换 Redis。
"""
from __future__ import annotations

import functools
import time
from typing import Any, Awaitable, Callable, TypeVar

from ..config import settings

T = TypeVar("T")


class TTLCache:
    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        hit = self._store.get(key)
        if not hit:
            return None
        ts, val = hit
        if time.monotonic() - ts > self.ttl:
            self._store.pop(key, None)
            return None
        return val

    def set(self, key: str, val: Any) -> None:
        self._store[key] = (time.monotonic(), val)


def _key(args: tuple, kwargs: dict) -> str:
    return repr((args, sorted(kwargs.items())))


def acached(ttl: float | None = None) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """缓存 async 函数返回值（按参数）。None→settings.cache_ttl。异常不缓存、照常抛。"""
    cache = TTLCache(ttl if ttl is not None else settings.cache_ttl)

    def deco(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            key = _key(args, kwargs)
            hit = cache.get(key)
            if hit is not None:
                return hit
            result = await fn(*args, **kwargs)
            cache.set(key, result)
            return result

        wrapper.cache = cache  # type: ignore[attr-defined]
        return wrapper

    return deco


def scached(ttl: float | None = None) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """缓存 sync 函数返回值（B站 `_sync_bili_*` 跑在 to_thread，用这个）。"""
    cache = TTLCache(ttl if ttl is not None else settings.cache_ttl)

    def deco(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            key = _key(args, kwargs)
            hit = cache.get(key)
            if hit is not None:
                return hit
            result = fn(*args, **kwargs)
            cache.set(key, result)
            return result

        wrapper.cache = cache  # type: ignore[attr-defined]
        return wrapper

    return deco
