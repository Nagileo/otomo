import asyncio

from otomo.agent.contracts import AgentState, FinalEvent
from otomo.eval.runner import run_one
from otomo.eval.verifier import GoldenCase
from otomo.tools.pixiv.tool import GetPixivRankingTool, PixivRankingArgs
from otomo.tools.videos import tool as video_tool
from otomo.tools.videos.tool import BiliSubtitleSegment, BiliVideoSubtitleArgs, GetBiliVideoSubtitlesTool


def test_pixiv_disabled_degrades_without_optional_dependency(monkeypatch):
    monkeypatch.setattr(video_tool.settings, "pixiv_enabled", False, raising=False)
    res = asyncio.run(GetPixivRankingTool().run(PixivRankingArgs(mode="day", limit=3)))
    assert not res.ok
    assert "PIXIV_ENABLED" in (res.error or "")


def test_bili_subtitle_no_public_subtitle_asr_off(monkeypatch):
    monkeypatch.setattr(video_tool, "_sync_bili_pagelist", lambda aid, bvid: {"data": [{"cid": 123}]})
    monkeypatch.setattr(video_tool, "_sync_bili_player", lambda aid, bvid, cid: {"data": {"subtitle": {"subtitles": []}}})
    monkeypatch.setattr(video_tool.settings, "asr_provider", "off", raising=False)

    res = asyncio.run(GetBiliVideoSubtitlesTool().run(BiliVideoSubtitleArgs(aid=1, max_segments=10)))

    assert not res.ok
    assert "ASR" in (res.error or "")


def test_bili_subtitle_no_public_subtitle_uses_mock_asr(monkeypatch):
    monkeypatch.setattr(video_tool, "_sync_bili_pagelist", lambda aid, bvid: {"data": [{"cid": 123}]})
    monkeypatch.setattr(video_tool, "_sync_bili_player", lambda aid, bvid, cid: {"data": {"subtitle": {"subtitles": []}}})

    async def fake_asr(source_url: str, max_segments: int):
        return [BiliSubtitleSegment(start=0.0, end=1.0, text="这是一段导视口播")], ["mock asr"], None

    monkeypatch.setattr(video_tool, "_maybe_asr_segments", fake_asr)

    res = asyncio.run(GetBiliVideoSubtitlesTool().run(BiliVideoSubtitleArgs(aid=1, max_segments=10)))

    assert res.ok
    assert res.data is not None
    assert res.data.source == "bili_asr"
    assert res.data.segments[0].text == "这是一段导视口播"


class _FakeRunner:
    def __init__(self) -> None:
        self.states: list[AgentState | None] = []

    async def stream(self, user_input: str, state: AgentState | None = None):
        self.states.append(state)
        if state is not None:
            state.short_term["turns_seen"] = int(state.short_term.get("turns_seen") or 0) + 1
        yield FinalEvent(answer=f"ack {user_input}")


def test_golden_eval_turns_share_agent_state():
    runner = _FakeRunner()
    case = GoldenCase.model_validate(
        {
            "id": "multi",
            "kind": "multi_turn",
            "turns": [
                {"question": "第一轮", "expect_contains": ["ack"]},
                {"question": "第二轮", "expect_contains": ["第二轮"]},
            ],
        }
    )

    res = asyncio.run(run_one(runner, case, llm=None, model="", client=None))  # type: ignore[arg-type]

    assert res.passed
    assert len(res.turns) == 2
    assert runner.states[0] is runner.states[1]
    assert runner.states[0] is not None
    assert runner.states[0].short_term["turns_seen"] == 2
