from __future__ import annotations

import asyncio

from otomo.agent import _common as C
from otomo.agent.contracts import AgentState
from otomo.memory import LongTermMemory
from otomo.memory.consolidate import consolidate_preference
from otomo.memory.models import FeedbackItem, UserMemory, VisualFeedbackItem, memory_summary
from otomo.tools.memory.tool import (
    FeedbackArgs,
    ForgetMemoryArgs,
    ForgetUserMemoryTool,
    GetUserMemoryArgs,
    GetUserMemoryTool,
    RememberPreferenceArgs,
    RememberUserPreferenceTool,
    RecordRecommendationFeedbackTool,
)


class FakeBangumiClient:
    async def get_me(self) -> dict:
        return {"username": "Nagileo"}


def test_consolidate_preference_add_update_and_opposite_delete():
    mem = UserMemory(username="u")

    action, changed = consolidate_preference(mem, "like", "芳文社日常", confidence=0.8)
    assert (action, changed) == ("ADD", True)
    assert [x.value for x in mem.likes] == ["芳文社日常"]
    assert mem.dislikes == []

    action, changed = consolidate_preference(mem, "like", "芳文社日常", confidence=0.9)
    assert (action, changed) == ("UPDATE", True)
    assert mem.likes[0].confidence == 0.9

    action, changed = consolidate_preference(mem, "dislike", "芳文社日常", confidence=0.95)
    assert (action, changed) == ("ADD", True)
    assert mem.likes == []
    assert [x.value for x in mem.dislikes] == ["芳文社日常"]

    action, changed = consolidate_preference(mem, "like", "   ")
    assert (action, changed) == ("NOOP", False)


def test_memory_store_load_save_and_feedback_limit(tmp_path):
    ltm = LongTermMemory(tmp_path)
    mem = UserMemory(username="u")
    consolidate_preference(mem, "like", "百合", confidence=0.8)
    mem.feedback.append(FeedbackItem(name="A", signal="like"))
    mem.feedback.append(FeedbackItem(name="B", signal="dislike"))
    mem.visual_feedback.append(VisualFeedbackItem(id="vf1", predicted_title="A", signal="wrong"))
    ltm.save_user(mem)

    loaded = ltm.load_user("u")
    assert loaded.username == "u"
    assert loaded.likes[0].value == "百合"
    assert loaded.updated_at
    assert [x.name for x in memory_summary(loaded, feedback_limit=1).recent_feedback] == ["B"]
    assert [x.predicted_title for x in memory_summary(loaded, feedback_limit=1).recent_visual_feedback] == ["A"]
    assert memory_summary(loaded, feedback_limit=0).recent_feedback == []
    assert memory_summary(loaded, feedback_limit=0).recent_visual_feedback == []


def test_memory_tools_read_write_for_current_user(tmp_path):
    client = FakeBangumiClient()
    ltm = LongTermMemory(tmp_path)
    remember = RememberUserPreferenceTool(client, ltm)
    get_memory = GetUserMemoryTool(client, ltm)
    feedback = RecordRecommendationFeedbackTool(client, ltm)

    res = asyncio.run(
        remember.run(RememberPreferenceArgs(kind="like", value="治愈日常", confidence=0.9))
    )
    assert res.ok and res.data is not None
    assert res.data.username == "Nagileo"
    assert res.data.action == "ADD"
    assert res.data.memory.likes[0].value == "治愈日常"

    res = asyncio.run(
        remember.run(RememberPreferenceArgs(kind="spoiler", value="none"))
    )
    assert res.ok and res.data is not None
    assert res.data.memory.spoiler_default == "none"

    res = asyncio.run(
        remember.run(RememberPreferenceArgs(kind="progress", subject="摇曳露营△", episode=5))
    )
    assert res.ok and res.data is not None
    assert res.data.memory.progress["摇曳露营△"].episode == 5
    res = asyncio.run(
        remember.run(RememberPreferenceArgs(kind="progress", subject="摇曳露营△", episode=5))
    )
    assert res.ok and res.data is not None
    assert res.data.action == "NOOP"

    res = asyncio.run(
        feedback.run(FeedbackArgs(name="ARIA", signal="more", note="多推这种慢节奏治愈"))
    )
    assert res.ok and res.data is not None
    assert res.data.memory.recent_feedback[-1].name == "ARIA"

    res = asyncio.run(get_memory.run(GetUserMemoryArgs(feedback_limit=0)))
    assert res.ok and res.data is not None
    assert res.data.memory.likes[0].value == "治愈日常"
    assert res.data.memory.recent_feedback == []


def test_forget_memory_tool(tmp_path):
    client = FakeBangumiClient()
    ltm = LongTermMemory(tmp_path)
    remember = RememberUserPreferenceTool(client, ltm)
    forget = ForgetUserMemoryTool(client, ltm)

    asyncio.run(remember.run(RememberPreferenceArgs(kind="dislike", value="后宫")))
    res = asyncio.run(forget.run(ForgetMemoryArgs(kind="dislike", value="后宫")))
    assert res.ok and res.data is not None
    assert res.data.action == "DELETE"
    assert res.data.memory.dislikes == []


def test_runtime_memory_prompt_replaces_previous_snapshot():
    state = AgentState()
    state.messages.append({"role": "system", "content": "base"})
    state.short_term["memory"] = {
        "username": "u",
        "likes": [{"value": "百合"}],
        "dislikes": [],
        "spoiler_default": "none",
    }
    C.inject_runtime_state(state.messages, state)
    state.short_term["memory"] = {
        "username": "u",
        "likes": [{"value": "日常"}],
        "dislikes": [{"value": "后宫"}],
        "spoiler_default": "none",
    }
    C.inject_runtime_state(state.messages, state)

    runtime_messages = [m for m in state.messages if str(m.get("content") or "").startswith("[[OTOMO_RUNTIME_STATE]]")]
    assert len(runtime_messages) == 1
    content = runtime_messages[0]["content"]
    assert "日常" in content
    assert "后宫" in content
    assert "百合" not in content
