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


def test_write_tools_model_visible_but_gated(tmp_path):
    """新设计：写工具对模型可见可调（用户口头确认即执行），护栏下移到工具层——
    confirmed=false 拒绝、动作必须已 prepare；默认 dispatch(allow_write=False) 仍拦截。"""
    client = FakeBangumiClient()
    ltm = LongTermMemory(tmp_path)
    reg = ToolRegistry()
    reg.register(PrepareBangumiWriteActionTool(client, ltm))
    reg.register(ExecuteBangumiWriteActionTool(client, ltm))

    names = {x["function"]["name"] for x in reg.openai_tools(include_write=True)}
    assert "execute_bangumi_write_action" in names

    # 未确认 → 工具层拒绝
    res = asyncio.run(
        reg.dispatch("execute_bangumi_write_action", '{"action_id":"x","confirmed":false}', allow_write=True)
    )
    assert not res.ok and "confirmed" in (res.error or "")
    # 不存在的动作 → 拒绝（只能执行已 prepare 的）
    res2 = asyncio.run(
        reg.dispatch("execute_bangumi_write_action", '{"action_id":"missing","confirmed":true}', allow_write=True)
    )
    assert not res2.ok and "找不到" in (res2.error or "")
    # 默认 dispatch 不带 allow_write 仍拦截（HTTP 面等非 agent 路径的保底）
    blocked = asyncio.run(reg.dispatch("execute_bangumi_write_action", '{"action_id":"x","confirmed":true}'))
    assert not blocked.ok and "write tool" in (blocked.error or "")


def test_prepare_dedupes_same_pending_action(tmp_path):
    """同一作品同一操作重复 prepare 不再堆积多个待确认（用户"再加一下"曾造出重复写回）。"""
    client = FakeBangumiClient()
    ltm = LongTermMemory(tmp_path)
    tool = PrepareBangumiWriteActionTool(client, ltm)
    a1 = asyncio.run(tool.run(PrepareBangumiWriteArgs(subject_id=1, collection_type=3, reason="加入在看")))
    a2 = asyncio.run(tool.run(PrepareBangumiWriteArgs(subject_id=1, collection_type=3, reason="再加一下")))
    assert a1.ok and a2.ok
    assert a1.data.action.id == a2.data.action.id  # 复用同一个待确认动作
    assert "已存在" in a2.data.warning
    mem = ltm.load_user("Nagileo")
    assert len([x for x in mem.pending_write_actions if x.status == "pending"]) == 1
    # payload 不同（改成想看）→ 允许新动作
    a3 = asyncio.run(tool.run(PrepareBangumiWriteArgs(subject_id=1, collection_type=1, reason="改想看")))
    assert a3.ok and a3.data.action.id != a1.data.action.id


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
