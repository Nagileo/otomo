from __future__ import annotations

import asyncio

from otomo.session_realtime import SessionRealtimeHub


def test_realtime_hub_rejects_second_writer_and_decorates_device_state():
    async def scenario():
        hub = SessionRealtimeHub()
        claimed, first = await hub.claim("user:u", "s1", "r1", "device-a")
        assert claimed
        claimed_again, current = await hub.claim("user:u", "s1", "r2", "device-b")
        assert not claimed_again
        assert current.request_id == "r1"

        rows = await hub.decorate_sessions("user:u", [{"id": "s1"}], "device-b")
        assert rows[0]["running"] is True
        assert rows[0]["activity_is_current_device"] is False

        await hub.release(first)
        rows = await hub.decorate_sessions("user:u", [{"id": "s1"}], "device-b")
        assert rows[0]["running"] is False

    asyncio.run(scenario())


def test_realtime_stream_is_owner_scoped():
    async def scenario():
        hub = SessionRealtimeHub()
        stream = hub.stream("user:u", "device-a")
        initial = await anext(stream)
        assert initial["type"] == "session_sync"
        await hub.notify("user:u", "session_changed", session_id="s1")
        event = await anext(stream)
        assert event["type"] == "session_changed"
        assert event["session_id"] == "s1"
        await stream.aclose()

    asyncio.run(scenario())
