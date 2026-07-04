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
from .weekly import WeeklyDigestService


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    service = WeeklyDigestService(LongTermMemory(), AuthStore())
    task = asyncio.create_task(service.run_forever())
    try:
        await stop.wait()
    finally:
        await service.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    asyncio.run(main())
