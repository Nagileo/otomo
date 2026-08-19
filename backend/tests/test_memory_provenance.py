from __future__ import annotations

from otomo.api.app import _manageable_memory
from otomo.memory.models import FeedbackItem, MemoryItem, UserMemory


def test_manageable_memory_explains_sources_backlinks_and_conflicts():
    memory = UserMemory(
        username="alice",
        likes=[MemoryItem(value="百合", source="explicit_user", confidence=1.0)],
        dislikes=[MemoryItem(value="百合动画", source="derived_from_feedback", confidence=0.5)],
        feedback=[FeedbackItem(
            subject_id=42,
            name="测试作品",
            signal="more",
            note="recommendation_card:web:more",
            source="explicit_user",
            confidence=0.9,
        )],
    )

    payload = _manageable_memory(memory)

    assert payload["likes"][0]["provenance"]["label"] == "你明确告诉 Otomo"
    assert payload["feedback"][0]["provenance"]["kind"] == "recommendation_feedback"
    assert payload["feedback"][0]["provenance"]["href"] == "/subject/42"
    assert payload["conflicts"][0]["like"] == "百合"
    assert payload["conflicts"][0]["dislike"] == "百合动画"

