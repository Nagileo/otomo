"""Standalone weekly digest worker.

The API process should stay focused on request handling. Production runs this
module as a separate service so scheduled weekly digests are not duplicated by
API workers or interrupted by frontend/API restarts.
"""
from __future__ import annotations

import asyncio
import contextlib
import signal

from .auth import AuthStore
from .memory import LongTermMemory
from .config import settings
from .subscriptions import SubscriptionService, SubscriptionStore
from .weekly import WeeklyDigestService


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    ltm = LongTermMemory()
    auth = AuthStore()
    services = []
    if settings.weekly_scheduler_enabled:
        services.append(WeeklyDigestService(ltm, auth))
    if settings.subscription_scheduler_enabled:
        services.append(SubscriptionService(SubscriptionStore(), ltm, auth))
    if not services:
        # Standalone worker is usually run with at least one scheduler enabled,
        # but keeping weekly as a default makes local smoke testing explicit.
        services.append(WeeklyDigestService(ltm, auth))
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
