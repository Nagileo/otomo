"""Standalone unified subscription worker.

The API process stays focused on request handling. Production runs this module
as a single service so notifications are not duplicated by API workers or
interrupted by frontend/API restarts.
"""
from __future__ import annotations

import asyncio
import contextlib
import signal

from .auth import AuthStore
from .memory import LongTermMemory
from .subscriptions import SubscriptionService, SubscriptionStore


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    ltm = LongTermMemory()
    auth = AuthStore()
    services = [SubscriptionService(SubscriptionStore(), ltm, auth)]
    tasks = [asyncio.create_task(service.run_forever()) for service in services]
    try:
        await stop.wait()
    finally:
        for service in services:
            await service.stop()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    asyncio.run(main())
