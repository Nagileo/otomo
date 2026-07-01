from __future__ import annotations

import asyncio

from otomo.agent.contracts import (
    AgentState,
    Citation,
    FinalEvent,
    ObservationEvent,
    ToolCallEvent,
)
from otomo.claim_verifier import verify_answer_claims
from otomo.obs import traced_stream


def test_claim_verifier_supports_facts_from_observations():
    result = verify_answer_claims(
        "《银之匙》是 A-1 Pictures 制作，Bangumi 评分 7.9。你可能会喜欢它的农业日常氛围。",
        [
            {
                "name": "get_subject_persons",
                "summary": "A-1 Pictures relation=动画制作",
                "sources": [],
                "entities": [],
                "data": {"title": "银之匙", "staff": [{"relation": "动画制作", "name": "A-1 Pictures"}]},
            },
            {
                "name": "recommend_subjects",
                "summary": "推荐：银之匙；理由：农业日常",
                "sources": [],
                "entities": [],
                "data": {"items": [{"name": "银之匙", "reasons": ["农业日常"]}]},
            },
        ],
    )
    assert result.claims
    assert any(c.kind == "canonical_fact" and c.supported for c in result.claims)
    assert result.supported_count >= 1


def test_claim_verifier_rejects_conflicting_staff_claim():
    result = verify_answer_claims(
        "《银之匙》是 8-bit 制作。",
        [
            {
                "name": "get_subject_persons",
                "summary": "A-1 Pictures relation=动画制作",
                "sources": [],
                "entities": [],
                "data": {"title": "银之匙", "staff": [{"relation": "动画制作", "name": "A-1 Pictures"}]},
            },
        ],
    )
    assert result.claims
    assert any(c.kind == "canonical_fact" and not c.supported for c in result.claims)
    assert result.unsupported_count >= 1


def test_claim_verifier_does_not_revise_narrative_or_discourse_summary():
    result = verify_answer_claims(
        "## 第11集「大家的梦、我的梦」（2020-12-12播出）\n"
        "这一集是第一季的剧情高潮，也是粉丝公认的神回。讨论数（160条）为全季最高。"
        "她发现偷看的目光不再只看着自己一个人了。",
        [
            {
                "name": "get_episode_comments",
                "summary": "第11集；讨论约160条；多条评论称神回、演出好",
                "sources": [],
                "entities": [],
                "data": {
                    "episode": {"sort": 11, "name_cn": "大家的梦、我的梦", "airdate": "2020-12-12", "comment": 160},
                    "comments": ["神回", "这一集演出很好", "步梦这里很压抑"],
                },
            },
        ],
    )
    assert result.claims
    assert not result.needs_revision
    assert result.unsupported_count == 0
    assert result.unverifiable_count >= 1


class DummyRunner:
    async def stream(self, user_input: str, state: AgentState | None = None):
        yield ToolCallEvent(name="get_subject", args={"subject_id": 1})
        yield ObservationEvent(
            name="get_subject",
            ok=True,
            summary="银之匙；评分 7.9",
            sources=[Citation(title="银之匙", url="https://bgm.tv/subject/1")],
            data={"title": "银之匙", "ratings": [{"source": "Bangumi", "score": 7.9}]},
        )
        yield FinalEvent(answer="《银之匙》Bangumi 评分 7.9。", steps=1)


class FakeRevisionMessage:
    content = "本轮 Bangumi staff 证据显示，《银之匙》的动画制作为 A-1 Pictures。"


class FakeRevisionChoice:
    message = FakeRevisionMessage()


class FakeRevisionResponse:
    choices = [FakeRevisionChoice()]


class FakeRevisionCompletions:
    async def create(self, **kwargs):
        return FakeRevisionResponse()


class FakeRevisionChat:
    completions = FakeRevisionCompletions()


class FakeRevisionLLM:
    chat = FakeRevisionChat()


class RevisionRunner:
    llm = FakeRevisionLLM()
    model = "fake"

    async def stream(self, user_input: str, state: AgentState | None = None):
        yield ObservationEvent(
            name="get_subject_persons",
            ok=True,
            summary="A-1 Pictures relation=动画制作",
            sources=[Citation(title="银之匙", url="https://bgm.tv/subject/1")],
            data={"title": "银之匙", "staff": [{"relation": "动画制作", "name": "A-1 Pictures"}]},
        )
        yield FinalEvent(answer="《银之匙》是 8-bit 制作。", steps=1)


def test_traced_stream_emits_claim_check(tmp_path, monkeypatch):
    import otomo.obs as obs

    monkeypatch.setattr(obs, "_TRACE_DIR", tmp_path)
    events = asyncio.run(_collect(traced_stream(DummyRunner(), "银之匙评分？", AgentState(), {"session_id": "s", "runner": "test"})))
    assert [e.type for e in events] == ["tool_call", "observation", "final", "claim_check"]
    claim = events[-1]
    assert claim.supported_count >= 1
    assert (tmp_path / "traces.jsonl").exists()
    assert (tmp_path / "rl_runs.jsonl").exists()


def test_traced_stream_auto_revises_blocking_claim(tmp_path, monkeypatch):
    import otomo.obs as obs

    monkeypatch.setattr(obs, "_TRACE_DIR", tmp_path)
    events = asyncio.run(_collect(traced_stream(RevisionRunner(), "银之匙谁做的？", AgentState(), {"session_id": "s", "runner": "test"})))
    assert [e.type for e in events] == ["observation", "final", "claim_check", "final", "claim_check"]
    assert "8-bit" in events[1].answer
    assert "A-1 Pictures" in events[3].answer
    assert events[4].unsupported_count == 0


async def _collect(aiter):
    return [x async for x in aiter]
