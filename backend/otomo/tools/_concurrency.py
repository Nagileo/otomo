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
    "bangumi_data": 1,
    "mikan": 2,
    "dmhy": 1,
    "acgnx": 1,
    "moegirl": 2,
    "netabare": 2,
    "anitabi": 2,        # 公益站克制：低并发 + fetch 内 0.15s 间隔（曾 300 连发被 403）
    "anitabi_batch": 3,  # 旅行模式外层批量；与内层 "anitabi" 分层（同名嵌套会死锁）
    "qbittorrent": 1,
}

# 按 (event loop, host) 隔离：asyncio.Semaphore 一旦产生 waiter 就绑死首个 loop，
# 跨 loop 复用（如 pytest 每用例一个新 loop）会抛 RuntimeError 且被 return_exceptions
# 吞成"该候选失败"静默丢数据。生产 uvicorn 单 loop 时行为与全局缓存一致。
_SEMAPHORES: dict[tuple[int, str], asyncio.Semaphore] = {}


def _semaphore(host: str) -> asyncio.Semaphore:
    host_key = host.strip().lower() or "default"
    key = (id(asyncio.get_running_loop()), host_key)
    if key not in _SEMAPHORES:
        if len(_SEMAPHORES) > 256:  # 已结束的 loop 留下的条目，防慢性泄漏
            current = key[0]
            for stale in [k for k in _SEMAPHORES if k[0] != current]:
                _SEMAPHORES.pop(stale, None)
        _SEMAPHORES[key] = asyncio.Semaphore(HOST_LIMITS.get(host_key, 4))
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

    ⚠ 不可重入：传入的协程在持有信号量槽期间，绝不能再对**同一 host** 调
    gather_limited——外层任务数 ≥ 上限时内层永远拿不到槽，直接死锁
    （AbandonAnalysisTool 曾踩过）。嵌套场景内层用普通 asyncio.gather。
    """

    sem = _semaphore(host)

    async def run_one(coro: Awaitable[T]) -> T:
        async with sem:
            return await coro

    return await asyncio.gather(
        *(run_one(coro) for coro in coros),
        return_exceptions=return_exceptions,
    )
