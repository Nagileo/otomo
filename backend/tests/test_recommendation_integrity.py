from __future__ import annotations

import asyncio

import pytest

from otomo.agent.contracts import ToolResult
from otomo.memory.models import AspectPreference, UserAspectProfile
from otomo.tools.recommend.explanations import (
    RecommendationClaim,
    RecommendationSupport,
    UNVERIFIED_EXPLANATION,
    audit_item_explanation,
    refresh_item_explanation,
    suppress_unverified_explanation,
)
from otomo.tools.recommend.tool import (
    RecItem,
    RecommendArgs,
    RecommendTool,
    _positive_consumption_items,
)
from otomo.tools.review.tool import AspectSummary, RatingEvidence, ReviewFusionResult


class SeriesClient:
    async def get_me(self):
        raise RuntimeError("anonymous")

    async def search_subjects(self, _query, _stype, **kwargs):
        tags = kwargs.get("tags") or []
        if not set(tags) & {"治愈", "日常"}:
            return {"data": []}
        return {"data": [{
            "id": 2,
            "name_cn": "治愈续作",
            "images": {"common": "https://example.test/sequel.jpg"},
            "rating": {"score": 8.8, "rank": 100},
            "eps": 12,
            "tags": [{"name": "治愈"}],
        }]}

    async def get_subject_relations(self, subject_id):
        if subject_id == 2:
            return [{"id": 1, "name_cn": "悬疑入口", "relation": "前传", "type": 2}]
        return []

    async def get_subject(self, subject_id):
        assert subject_id == 1
        return {
            "id": 1,
            "name_cn": "悬疑入口",
            "images": {"common": "https://example.test/entry.jpg"},
            "rating": {"score": 6.2, "rank": 1600},
            "eps": 10,
            "tags": [{"name": "悬疑"}],
        }


@pytest.mark.asyncio
async def test_series_entry_rebuilds_title_score_tags_and_evidence():
    result = await RecommendTool(SeriesClient()).run(RecommendArgs(
        tags=["治愈"],
        limit=1,
        use_graph=False,
        use_cf=False,
        use_curation=False,
        use_external_recall=False,
        use_semantic=False,
        enrich_evidence=False,
        diversity_strength=0,
    ))

    assert result.ok and result.data
    item = result.data.items[0]
    assert (item.id, item.name, item.image, item.bangumi_score) == (
        1, "悬疑入口", "https://example.test/entry.jpg", 6.2,
    )
    assert item.diversity_tags == ["悬疑"]
    assert item.explicit_tag_matches == []
    assert item.evidence == []
    assert item.external_mappings == []
    assert item.series_origin == "治愈续作"
    assert item.why_recalled == ["系列入口：由《治愈续作》回溯"]
    assert item.score_breakdown
    assert sum(item.score_breakdown.values()) == pytest.approx(item.score, abs=0.01)
    assert any(claim.kind == "provenance" for claim in item.claims)
    assert not any("8.8" in str(claim.model_dump()) for claim in item.claims)


class ReviewClient:
    pass


class ReviewStub:
    async def run(self, _args):
        return ToolResult(
            ok=True,
            data=ReviewFusionResult(
                subject_id=10,
                title="评价候选",
                subject_type="anime",
                spoiler_level="none",
                consensus="整体口碑稳定，视觉表现是主要优点。",
                ratings=[RatingEvidence(
                    source="Bangumi",
                    score=8.1,
                    scale=10,
                    count=500,
                    signal="strong",
                )],
                aspect_summary=[AspectSummary(
                    aspect="visual",
                    label="画面",
                    positive=4,
                    total=4,
                    dominant_sentiment="positive",
                    confidence="high",
                    sources=["Bangumi"],
                )],
            ),
        )


@pytest.mark.asyncio
async def test_review_enrichment_refreshes_fit_risk_and_claims():
    tool = RecommendTool(ReviewClient())
    tool.reviewer = ReviewStub()
    item = RecItem(
        id=10,
        name="评价候选",
        score=1.0,
        reasons=[],
        recall_signals=["标签召回：动画"],
        diversity_tags=["动画"],
    )
    profile = UserAspectProfile(
        username="alice",
        likes=[AspectPreference(
            aspect="visual",
            label="画面表现",
            polarity="like",
            weight=1.0,
            confidence=0.9,
        )],
    )

    await tool._enrich_review_evidence([item], profile, "anime")
    refresh_item_explanation(item, "general")

    assert item.score > 1.0
    assert any("评价证据支持你的好球区" in point for point in item.fit_points)
    assert any(claim.kind == "fit" and "Bangumi" in claim.support for claim in item.claims)
    assert any(claim.kind == "quality" and "口碑稳定" in claim.text for claim in item.claims)


def test_recent_feedback_never_becomes_an_explicit_claim():
    item = RecItem(
        id=20,
        name="反馈候选",
        score=0.5,
        reasons=[],
        explicit_tag_matches=[],
        feedback_tag_matches=["音乐"],
    )
    refresh_item_explanation(item, "general")

    assert any("近期反馈" in point for point in item.fit_points)
    assert not any("本轮明确" in point for point in item.fit_points)
    feedback_claim = next(claim for claim in item.claims if "近期反馈" in claim.text)
    assert feedback_claim.confidence == "low"
    assert audit_item_explanation(item) == []


def test_profile_aspect_claim_does_not_borrow_unrelated_review_rating_source():
    item = RecItem(
        id=23,
        name="画像维度候选",
        score=1.0,
        reasons=[],
        aspect_matches=["画面表现(0.80)"],
        review_sources=["Bangumi"],
    )
    refresh_item_explanation(item, "general")

    claim = next(claim for claim in item.claims if claim.text == "画面表现(0.80)")
    assert claim.support == ["长期口味画像（由条目标签映射）"]
    assert claim.evidence[0].kind == "profile_aspect"
    assert audit_item_explanation(item) == []


def test_explanation_audit_rejects_visible_prose_without_matching_support():
    item = RecItem(
        id=21,
        name="不一致候选",
        score=1.0,
        score_breakdown={"base": 0.5},
        reasons=[],
        fit_points=["文字声称命中，但没有证据"],
    )

    issues = audit_item_explanation(item)

    assert any("没有对应证据声明" in issue for issue in issues)
    assert any("分项加总不一致" in issue for issue in issues)


def test_explanation_audit_rejects_source_name_and_forged_typed_fact():
    claim = RecommendationClaim(
        kind="fit",
        text="这部作品非常治愈",
        support=["Bangumi"],
        evidence=[RecommendationSupport(
            kind="subject_tag",
            value="治愈",
            source="Bangumi",
            field="tags",
            subject_id=22,
            label="Bangumi 条目标签：治愈",
        )],
    )
    item = RecItem(
        id=22,
        name="实际只有悬疑标签",
        score=1.0,
        reasons=[],
        diversity_tags=["悬疑"],
        fit_points=[claim.text],
        claims=[claim],
    )

    issues = audit_item_explanation(item)

    assert any("未对齐证据" in issue for issue in issues)


def test_failed_explanation_audit_is_hidden_from_all_presentation_surfaces():
    item = RecItem(
        id=22,
        name="证据错位候选",
        score=1.0,
        reasons=[],
        fit_points=["伪造的适配理由"],
        risks=["伪造的风险"],
        why_recalled=["旧召回说明"],
        claims=[],
    )
    issues = audit_item_explanation(item)
    suppress_unverified_explanation(item, issues)

    assert issues
    assert item.fit_points == [UNVERIFIED_EXPLANATION]
    assert item.risks == []
    assert item.why_recalled == []
    assert item.claims == []


def test_personalization_seeds_exclude_low_ratings_and_non_consumption():
    items = [
        {"type": 2, "rate": 9, "subject": {"id": 1}},
        {"type": 3, "rate": 0, "subject": {"id": 2}},
        {"type": 2, "rate": 4, "subject": {"id": 3}},
        {"type": 5, "rate": 0, "subject": {"id": 4}},
        {"type": 1, "rate": 10, "subject": {"id": 5}},
    ]

    seeds = _positive_consumption_items(items)

    assert [item["subject"]["id"] for item in seeds] == [1, 2]


class ConcurrentRecallClient:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def _network_turn(self) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
        finally:
            self.active -= 1

    async def search_subjects(self, _query, stype, **kwargs):
        await self._network_turn()
        tag = kwargs["tags"][0]
        sid = {"日常": 101, "治愈": 102}[tag]
        return {"data": [{
            "id": sid,
            "name_cn": f"{tag}候选",
            "type": stype,
            "tags": [{"name": tag}],
            "rating": {"score": 8.0},
        }]}

    async def get_subject_persons(self, subject_id):
        await self._network_turn()
        return [{"id": subject_id * 10, "name": f"监督{subject_id}", "relation": "监督"}]

    async def get_person_subjects(self, person_id):
        await self._network_turn()
        return [{
            "id": person_id + 1000,
            "name_cn": f"图谱候选{person_id}",
            "type": 2,
            "tags": [{"name": "动画"}],
        }]


@pytest.mark.asyncio
async def test_tag_and_graph_recall_parallelize_without_losing_signals():
    client = ConcurrentRecallClient()
    tool = RecommendTool(client)
    candidates: dict[int, dict] = {}

    await tool._tag_recall(
        candidates,
        2,
        ["日常", "治愈"],
        {"日常": 1.0, "治愈": 0.8},
        1.0,
        [],
        set(),
        False,
    )

    assert client.max_active >= 2
    assert candidates[101]["matched"] == {"日常"}
    assert candidates[102]["matched"] == {"治愈"}

    client.max_active = 0
    await tool._graph_recall(candidates, 2, [1, 2, 3], set())

    assert client.max_active >= 2
    assert candidates[1010]["graph"] == {"同监督·监督1"}
    assert candidates[1020]["graph"] == {"同监督·监督2"}
    assert candidates[1030]["graph"] == {"同监督·监督3"}
