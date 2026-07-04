"""Small bounded-concurrency helpers for external-source tools."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from typing import TypeVar

T = TypeVar("T")

HOST_LIMITS = {
    "bangumi": 6,
    "egs": 2,
    "vndb": 3,
    "anilist": 3,
    "bilibili": 2,
    "yuc": 2,
    "musicbrainz": 1,
}

_SEMAPHORES: dict[str, asyncio.Semaphore] = {}


def _semaphore(host: str) -> asyncio.Semaphore:
    key = host.strip().lower() or "default"
    if key not in _SEMAPHORES:
        _SEMAPHORES[key] = asyncio.Semaphore(HOST_LIMITS.get(key, 4))
    return _SEMAPHORES[key]


async def gather_limited(
    coros: Iterable[Awaitable[T]],
    *,
    host: str = "default",
    return_exceptions: bool = True,
) -> list[T | BaseException]:
    """Run awaitables through a per-host semaphore.

    Tools use this for independent enrich calls. `return_exceptions=True` is
    deliberate: one flaky external source must not collapse the whole answer.
    """

    sem = _semaphore(host)

    async def run_one(coro: Awaitable[T]) -> T:
        async with sem:
            return await coro

    return await asyncio.gather(
        *(run_one(coro) for coro in coros),
        return_exceptions=return_exceptions,
    )
