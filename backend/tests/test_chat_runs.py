from __future__ import annotations

import asyncio

from otomo.chat_runs import ChatRunHub


def test_chat_run_replays_events_and_survives_stream_disconnect(tmp_path):
    async def scenario():
        hub = ChatRunHub(str(tmp_path / "runs.sqlite3"))
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


def test_chat_run_cancel_is_owner_scoped(tmp_path):
    async def scenario():
        hub = ChatRunHub(str(tmp_path / "runs.sqlite3"))

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


def test_chat_run_shutdown_marks_service_interruption(tmp_path):
    async def scenario():
        path = str(tmp_path / "runs.sqlite3")
        hub = ChatRunHub(path)

        async def worker(_run):
            await asyncio.Event().wait()

        run = await hub.start("r1", "user:alice", "s1", "device-a", worker)
        await hub.shutdown()
        assert run.cancel_reason == "shutdown"
        assert run.status == "interrupted"
        restored = await ChatRunHub(path).get("user:alice", run.id)
        assert restored is not None
        assert restored.status == "interrupted"
        events = [item async for item in restored.stream()]
        assert events[-1].event == "interrupted"

    asyncio.run(scenario())


def test_chat_run_restart_recovers_events_and_marks_running_as_interrupted(tmp_path):
    async def scenario():
        path = str(tmp_path / "runs.sqlite3")
        first = ChatRunHub(path)
        run = first._attach(  # simulate a process that died without lifespan shutdown
            __import__("otomo.chat_runs", fromlist=["ChatRun"]).ChatRun(
                "r-restart", "user:alice", "s1", "device-a", status="running"
            )
        )
        first.store.create(first.namespace, run)
        await run.publish("answer_delta", {"delta": "half"})

        second = ChatRunHub(path)
        restored = await second.get("user:alice", "r-restart")
        assert restored is not None
        assert restored.status == "interrupted"
        assert restored.error == "服务重启，任务执行已中断"
        events = [item async for item in restored.stream()]
        assert [item.event for item in events if item is not None] == ["answer_delta", "interrupted"]

    asyncio.run(scenario())
