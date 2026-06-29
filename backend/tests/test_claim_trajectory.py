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


def test_traced_stream_emits_claim_check(tmp_path, monkeypatch):
    import otomo.obs as obs

    monkeypatch.setattr(obs, "_TRACE_DIR", tmp_path)
    events = asyncio.run(_collect(traced_stream(DummyRunner(), "银之匙评分？", AgentState(), {"session_id": "s", "runner": "test"})))
    assert [e.type for e in events] == ["tool_call", "observation", "final", "claim_check"]
    claim = events[-1]
    assert claim.supported_count >= 1
    assert (tmp_path / "traces.jsonl").exists()
    assert (tmp_path / "rl_runs.jsonl").exists()


async def _collect(aiter):
    return [x async for x in aiter]
