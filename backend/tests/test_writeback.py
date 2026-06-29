from __future__ import annotations

import asyncio

from otomo.agent.registry import ToolRegistry
from otomo.memory import LongTermMemory
from otomo.tools.writeback.tool import (
    ConfirmBangumiWriteArgs,
    ExecuteBangumiWriteActionTool,
    PrepareBangumiWriteActionTool,
    PrepareBangumiWriteArgs,
    UpsertWatchPlanArgs,
    UpsertWatchPlanTool,
)


class FakeBangumiClient:
    def __init__(self) -> None:
        self.collection = {
            "subject_id": 1,
            "subject_type": 2,
            "rate": 0,
            "type": 1,
            "tags": [],
            "ep_status": 0,
            "vol_status": 0,
            "private": False,
        }
        self.writes: list[tuple[int, dict]] = []

    async def get_me(self) -> dict:
        return {"username": "Nagileo"}

    async def get_subject(self, subject_id: int) -> dict:
        return {"id": subject_id, "name_cn": "摇曳露营△"}

    async def get_user_collection(self, username: str, subject_id: int) -> dict:
        assert username == "Nagileo"
        assert subject_id == 1
        return dict(self.collection)

    async def set_my_collection(self, subject_id: int, json_body: dict) -> dict:
        self.writes.append((subject_id, dict(json_body)))
        self.collection.update(json_body)
        return {}


def test_write_tools_are_not_model_visible(tmp_path):
    client = FakeBangumiClient()
    ltm = LongTermMemory(tmp_path)
    reg = ToolRegistry()
    reg.register(PrepareBangumiWriteActionTool(client, ltm))
    reg.register(ExecuteBangumiWriteActionTool(client, ltm))

    names = {x["function"]["name"] for x in reg.openai_tools()}
    assert "prepare_bangumi_write_action" in names
    assert "execute_bangumi_write_action" not in names

    blocked = asyncio.run(
        reg.dispatch(
            "execute_bangumi_write_action",
            '{"action_id":"missing","confirmed":true}',
        )
    )
    assert not blocked.ok
    assert "write tool" in (blocked.error or "")


def test_prepare_confirm_write_action_and_decision_log(tmp_path):
    client = FakeBangumiClient()
    ltm = LongTermMemory(tmp_path)
    prepare = PrepareBangumiWriteActionTool(client, ltm)
    execute = ExecuteBangumiWriteActionTool(client, ltm)

    res = asyncio.run(
        prepare.run(
            PrepareBangumiWriteArgs(subject_id=1, collection_type=3, rate=8, reason="开始追")
        )
    )
    assert res.ok and res.data is not None
    assert res.data.requires_confirmation
    assert res.data.action.status == "pending"
    assert res.data.action.before is not None
    assert res.data.memory.pending_write_actions[0].id == res.data.action.id

    denied = asyncio.run(
        execute.run(ConfirmBangumiWriteArgs(action_id=res.data.action.id, confirmed=False))
    )
    assert not denied.ok

    done = asyncio.run(
        execute.run(ConfirmBangumiWriteArgs(action_id=res.data.action.id, confirmed=True))
    )
    assert done.ok and done.data is not None
    assert client.writes == [(1, {"type": 3, "rate": 8})]
    assert done.data.action.status == "executed"
    assert done.data.memory.recent_decisions[-1].kind == "write"


def test_watch_plan_upsert(tmp_path):
    client = FakeBangumiClient()
    ltm = LongTermMemory(tmp_path)
    tool = UpsertWatchPlanTool(client, ltm)

    res = asyncio.run(
        tool.run(
            UpsertWatchPlanArgs(
                subject_id=1,
                name="摇曳露营△",
                status="wishlist",
                priority=5,
                reason="露营治愈",
                tags=["治愈", "日常"],
            )
        )
    )
    assert res.ok and res.data is not None
    assert res.data.watch_plan[0].name == "摇曳露营△"
    assert res.data.memory.watch_plan[0].priority == 5
    assert res.data.memory.recent_decisions[-1].kind == "plan"
