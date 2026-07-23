from __future__ import annotations

import asyncio
import json

from otomo.agent import _common as common
from otomo.agent.compaction import SUMMARY_MARKER, compact_agent_state
from otomo.agent.contracts import AgentState
from otomo.config import settings
from otomo.session_store import SessionStore


def test_presentation_contract_is_current_turn_single_source():
    state = AgentState(
        messages=[
            {"role": "assistant", "tool_calls": [
                {"id": "old", "function": {"name": "monthly_watch_report", "arguments": "{}"}}
            ]}
        ]
    )
    common.begin_presentation_turn(state)
    payload = {
        "subject_type": "anime",
        "items": [{"id": 1, "name": "测试动画", "bangumi_score": 8.2}],
    }
    common.record_presentation_panel(state, "recommend_subjects", payload)

    prompt = common.presentation_contract_prompt(state)
    assert '"bangumi_score":8.2' in prompt
    answer = common.append_missing_anchors("按当前条件，先看《测试动画》。", state)
    assert "[[panel:recommend_subjects]]" in answer
    assert "monthly_watch_report" not in answer


def test_presentation_contract_stays_valid_json_when_compacted():
    state = AgentState()
    common.begin_presentation_turn(state)
    common.record_presentation_panel(
        state,
        "review_subject",
        {
            "subject_id": 42,
            "title": "测试作品",
            "score": 8.1,
            "comments": [{"text": "很长的评论" * 100}],
        },
    )
    prompt = common.presentation_contract_prompt(state, limit=80)
    payload = prompt.split("\n", 1)[1].split("\n规则：", 1)[0]
    parsed = json.loads(payload)
    assert parsed[0]["data"]["subject_id"] == 42
    assert parsed[0]["data"]["score"] == 8.1
    assert "comments" not in parsed[0]["data"]


def test_season_cards_are_placed_beside_matching_titles():
    state = AgentState()
    common.begin_presentation_turn(state)
    common.record_presentation_panel(
        state,
        "season_guide_brief",
        {
            "items": [
                {"subject_id": 11, "title": "作品甲"},
                {"subject_id": 22, "title": "作品乙"},
            ]
        },
    )
    answer = common.append_missing_anchors(
        "先看《作品甲》，它最贴合你的口味。\n\n作品乙可以观望。",
        state,
    )
    assert "作品甲" in answer and "[[panel:season_guide_brief:11]]" in answer
    assert "作品乙" in answer and "[[panel:season_guide_brief:22]]" in answer
    assert "[[panel:season_guide_brief]]" not in answer


def test_compaction_summarizes_old_complete_turns_and_keeps_recent_pairs():
    messages = [{"role": "system", "content": "system"}]
    for idx in range(10):
        messages.extend(
            [
                {"role": "user", "content": f"问题 {idx}"},
                {"role": "assistant", "content": f"回答 {idx}"},
            ]
        )
    state = AgentState(messages=messages)
    changed = asyncio.run(compact_agent_state(state, force=True))
    assert changed
    assert any(str(m.get("content") or "").startswith(SUMMARY_MARKER) for m in state.messages)
    assert not any(m.get("content") == "问题 0" for m in state.messages)
    assert any(m.get("content") == "问题 9" for m in state.messages)
    assert state.short_term.get("conversation_summary")


def test_compaction_triggers_on_tool_heavy_character_budget(monkeypatch):
    monkeypatch.setattr(settings, "conversation_compaction_threshold", 1000)
    monkeypatch.setattr(settings, "conversation_compaction_threshold_chars", 20000)
    messages = [{"role": "system", "content": "system"}]
    for idx in range(4):
        messages.extend(
            [
                {"role": "user", "content": f"问题 {idx}"},
                {"role": "assistant", "content": str(idx) * 10000},
            ]
        )
    state = AgentState(messages=messages)
    changed = asyncio.run(compact_agent_state(state))
    assert changed
    assert any(str(m.get("content") or "").startswith(SUMMARY_MARKER) for m in state.messages)
    assert any(m.get("content") == "问题 3" for m in state.messages)


def test_turn_aware_trim_never_starts_with_orphan_tool():
    messages = [{"role": "system", "content": "system"}]
    for idx in range(8):
        messages.extend(
            [
                {"role": "user", "content": f"q{idx}"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": f"t{idx}", "function": {"name": "x", "arguments": "{}"}}],
                },
                {"role": "tool", "tool_call_id": f"t{idx}", "content": "{}"},
                {"role": "assistant", "content": f"a{idx}"},
            ]
        )
    trimmed = common.trim_messages(messages, max_messages=14)
    non_system = [m for m in trimmed if m.get("role") != "system"]
    assert non_system
    assert non_system[0]["role"] == "user"


def test_turn_aware_trim_keeps_multi_tool_transaction_complete():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "early context"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-a", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                {"id": "call-b", "type": "function", "function": {"name": "b", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call-a", "content": "a-result"},
        {"role": "tool", "tool_call_id": "call-b", "content": "b-result"},
        {"role": "assistant", "content": "latest conclusion"},
    ]

    too_small = common.trim_messages(messages, max_messages=4)
    assert [message["role"] for message in too_small] == ["system", "user", "assistant", "assistant"]
    assert not any(message.get("tool_calls") for message in too_small)

    enough_for_group = common.trim_messages(messages, max_messages=6)
    call_messages = [message for message in enough_for_group if message.get("tool_calls")]
    assert len(call_messages) == 1
    expected = {call["id"] for call in call_messages[0]["tool_calls"]}
    actual = {
        message["tool_call_id"]
        for message in enough_for_group
        if message.get("role") == "tool"
    }
    assert actual == expected


def test_turn_aware_trim_enforces_character_budget_without_splitting_tools():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-a", "type": "function", "function": {"name": "a", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call-a", "content": "x" * 5000},
        {"role": "assistant", "content": "final"},
    ]
    trimmed = common.trim_messages(messages, max_messages=40, max_chars=1000)
    assert [message["role"] for message in trimmed] == ["system", "user", "assistant"]
    assert trimmed[-1]["content"] == "final"


def test_delete_owner_sessions_is_isolated(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.sqlite3"))
    store.ensure_session("a1", "discord:1")
    store.ensure_session("a2", "discord:1")
    store.ensure_session("b1", "discord:2")
    assert store.delete_owner_sessions("discord:1") == 2
    assert store.list_sessions("discord:1") == []
    assert [row["id"] for row in store.list_sessions("discord:2")] == ["b1"]
