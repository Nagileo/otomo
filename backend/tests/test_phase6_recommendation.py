from __future__ import annotations

import asyncio

from pydantic import BaseModel

from otomo.agent import _common as C
from otomo.agent.contracts import AgentState, Tool, ToolResult
from otomo.agent.registry import ToolRegistry
from otomo.memory.models import AspectPreference, UserAspectProfile, UserMemory, memory_summary
from otomo.tools.aspect_profile.tool import (
    AspectExtraction,
    aggregate_aspect_profile,
    collection_comment_samples,
    fallback_extract,
)
from otomo.profile import TasteProfile
from otomo.tools.profile.tool import _enrich_people_stats, _next_actions, _persona, _tag_drift, _yearly_activity
from otomo.tools.recommend.tool import _candidate_aspects, _classify_book_subtype, _classify_music_subtype
from otomo.tools.review.tool import RatingEvidence, _galgame_source_groups
from otomo.tools.watchorder.tool import _eps


def test_aspect_profile_aggregate_like_and_dislike():
    profile = aggregate_aspect_profile(
        "u",
        "anime",
        [
            AspectExtraction(aspect="visual", polarity="positive", snippet="作画很神", confidence=0.8),
            AspectExtraction(aspect="visual", polarity="positive", snippet="画面优秀", confidence=0.8),
            AspectExtraction(aspect="pacing", polarity="negative", snippet="节奏太拖", confidence=0.8),
        ],
        sample_count=3,
        extraction_source="llm",
    )
    assert profile.likes[0].aspect == "visual"
    assert profile.likes[0].weight == 1.0
    assert profile.dislikes[0].aspect == "pacing"
    assert profile.extraction_source == "llm"


def test_aspect_fallback_extracts_multiple_aspects():
    samples = [
        {"subject": "A", "rate": 8, "text": "作画很神但剧情后半太拖，音乐稳定"}
    ]
    extracted = fallback_extract(samples)
    aspects = {x.aspect for x in extracted}
    assert "visual" in aspects
    assert "story" in aspects or "pacing" in aspects


def test_collection_comment_samples_only_keeps_visible_comments():
    rows = [
        {"rate": 9, "comment": "好看", "subject": {"name": "A"}},
        {"rate": 7, "subject": {"name": "B"}},
    ]
    samples = collection_comment_samples(rows, limit=10)
    assert len(samples) == 1
    assert samples[0]["subject"] == "A"
    assert samples[0]["polarity_hint"] == "positive"


def test_memory_summary_includes_aspect_profiles():
    mem = UserMemory(username="u")
    mem.aspect_profiles["anime"] = UserAspectProfile(
        username="u",
        subject_type="anime",
        likes=[AspectPreference(aspect="visual", label="画面/作画", polarity="like")],
    )
    summary = memory_summary(mem)
    assert summary.aspect_profiles["anime"].likes[0].aspect == "visual"


def test_candidate_aspects_maps_domain_tags():
    aspects = _candidate_aspects({"百合", "日常", "作画"})
    assert "character" in aspects
    assert "pacing" in aspects
    assert "visual" in aspects


def test_media_subtype_classification_for_book_and_music():
    assert _classify_book_subtype({"轻小说", "电击文库"}) == "light_novel"
    assert _classify_book_subtype({"漫画", "连载"}) == "comic"
    assert _classify_music_subtype({"OST", "原声"}) == "ost"
    assert _classify_music_subtype({"OP", "主题歌"}) == "theme_song"


def test_watch_copilot_eps_parses_subject_eps():
    assert _eps({"subject": {"eps": "12"}}) == 12
    assert _eps({"subject": {"eps": 0}}) is None


def test_taste_report_persona_and_next_actions():
    profile = TasteProfile(
        username="u",
        watched=3,
        rated=2,
        top_tags=[{"tag": "日常", "weight": 5}, {"tag": "治愈", "weight": 4}],
    )
    assert "日常" in _persona(profile, "anime")
    actions = _next_actions("book", profile, has_aspect=False)
    assert any("冷启动" in x for x in actions)
    assert any("comic" in x for x in actions)


def test_dashboard_yearly_activity_and_tag_drift():
    items = [
        {"type": 2, "rate": 8, "subject": {"id": 1, "name": "old-a", "date": "2018-01-01", "tags": [{"name": "战斗"}]}},
        {"type": 2, "rate": 7, "subject": {"id": 2, "name": "old-b", "date": "2018-04-01", "tags": [{"name": "战斗"}]}},
        {"type": 4, "rate": 5, "subject": {"id": 3, "name": "old-c", "date": "2019-01-01", "tags": [{"name": "奇幻"}]}},
        {"type": 2, "rate": 9, "subject": {"id": 4, "name": "old-d", "date": "2019-04-01", "tags": [{"name": "奇幻"}]}},
        {"type": 2, "rate": 9, "subject": {"id": 5, "name": "new-a", "date": "2025-01-01", "tags": [{"name": "日常"}]}},
        {"type": 2, "rate": 10, "subject": {"id": 6, "name": "new-b", "date": "2025-04-01", "tags": [{"name": "日常"}]}},
        {"type": 3, "rate": 0, "subject": {"id": 7, "name": "new-c", "date": "2026-01-01", "tags": [{"name": "百合"}]}},
        {"type": 2, "rate": 8, "subject": {"id": 8, "name": "new-d", "date": "2026-04-01", "tags": [{"name": "百合"}]}},
    ]
    yearly = _yearly_activity(items)
    y2025 = next(x for x in yearly if x["year"] == "2025")
    assert y2025["total"] == 2
    assert y2025["avg_rating"] == 9.5
    drift = _tag_drift(items)
    assert {x["tag"] for x in drift if x["trend"] == "rising"} & {"日常", "百合"}
    assert {x["tag"] for x in drift if x["trend"] == "receding"} & {"战斗", "奇幻"}


class DashboardEnrichClient:
    async def get_subject_persons(self, subject_id: int):
        if subject_id == 10:
            return [
                {"id": 1, "name": "A-1 Pictures", "relation": "动画制作"},
                {"id": 2, "name": "导演甲", "relation": "监督"},
            ]
        return [
            {"id": 1, "name": "A-1 Pictures", "relation": "动画制作"},
            {"id": 3, "name": "脚本乙", "relation": "脚本"},
        ]

    async def get_subject_characters(self, subject_id: int):
        return [
            {"id": 100 + subject_id, "name": "角色", "actors": [{"id": 5, "name": "声优丙"}]},
        ]


def test_dashboard_enrichment_collects_staff_studio_and_cv():
    items = [
        {"type": 2, "rate": 10, "subject": {"id": 10, "name": "A", "date": "2024-01-01"}},
        {"type": 2, "rate": 9, "subject": {"id": 11, "name": "B", "date": "2025-01-01"}},
    ]
    enriched = asyncio.run(_enrich_people_stats(DashboardEnrichClient(), "anime", items, limit=8))
    assert enriched["sampled_count"] == 2
    assert enriched["studio_affinity"][0]["name"] == "A-1 Pictures"
    assert enriched["studio_affinity"][0]["count"] == 2
    assert {x["name"] for x in enriched["staff_affinity"]} >= {"导演甲", "脚本乙"}
    assert enriched["cv_affinity"][0]["name"] == "声优丙"


def test_galgame_source_groups_are_structured():
    groups, notes, consensus = _galgame_source_groups([
        RatingEvidence(source="Bangumi", score=8.1, scale=10, count=200, signal="strong"),
        RatingEvidence(source="ErogameScape/批判空间", score=82, scale=100, count=120, signal="strong"),
        RatingEvidence(source="VNDB", score=78, scale=100, count=500, signal="positive"),
    ])
    assert len(groups) == 3
    assert any("批判空间" in g.group for g in groups)
    assert notes
    assert "galgame" in consensus


class _RecData(BaseModel):
    subject_type: str = "anime"
    based_on_tags: list[str] = []
    mode: str = "normal"
    items: list[dict] = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]


class _Args(BaseModel):
    pass


class _RecommendStub(Tool):
    name = "recommend_subjects"
    description = "stub"
    args_model = _Args
    result_model = _RecData

    async def run(self, args: _Args) -> ToolResult[_RecData]:
        return ToolResult(ok=True, data=_RecData())


def test_step_tools_stores_last_recommend_state():
    class Fn:
        name = "recommend_subjects"
        arguments = "{}"

    class Call:
        id = "1"
        function = Fn()

    class Msg:
        content = ""
        tool_calls = [Call()]

    reg = ToolRegistry()
    reg.register(_RecommendStub())
    state = AgentState()
    events = list(asyncio.run(_collect(C.step_tools(reg, Msg(), [], [], set(), state))))
    assert any(ev.type == "observation" for ev in events)
    assert state.short_term["last_recommend"]["items"][0]["id"] == 1


async def _collect(aiter):
    return [x async for x in aiter]
