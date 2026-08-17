from __future__ import annotations

import asyncio

from otomo.chat_runs import ChatRunHub


def test_chat_run_replays_events_and_survives_stream_disconnect():
    async def scenario():
        hub = ChatRunHub()
        release = asyncio.Event()

        async def worker(run):
            await run.publish("meta", {"type": "meta", "run_id": run.id})
            await release.wait()
            await run.publish("final", {"type": "final", "answer": "done"})

        run = await hub.start("r1", "user:alice", "s1", "device-a", worker)
        stream = run.stream()
        first = await anext(stream)
        assert first is not None and first.event == "meta"
        await stream.aclose()  # disconnecting a subscriber must not cancel the worker
        assert run.task is not None and not run.task.done()

        release.set()
        await run.task
        replay = [event async for event in run.stream(after=first.sequence)]
        assert [event.event for event in replay if event is not None] == ["final"]
        assert run.status == "completed"

    asyncio.run(scenario())


def test_chat_run_cancel_is_owner_scoped():
    async def scenario():
        hub = ChatRunHub()

        async def worker(_run):
            await asyncio.Event().wait()

        run = await hub.start("r1", "user:alice", "s1", "device-a", worker)
        assert await hub.cancel("user:bob", run.id) is False
        assert await hub.cancel("user:alice", run.id) is True
        assert run.cancel_reason == "user"
        assert run.task is not None
        await run.task
        assert run.status == "cancelled"

    asyncio.run(scenario())


def test_chat_run_shutdown_marks_service_interruption():
    async def scenario():
        hub = ChatRunHub()

        async def worker(_run):
            await asyncio.Event().wait()

        run = await hub.start("r1", "user:alice", "s1", "device-a", worker)
        await hub.shutdown()
        assert run.cancel_reason == "shutdown"
        assert run.status == "cancelled"

    asyncio.run(scenario())
