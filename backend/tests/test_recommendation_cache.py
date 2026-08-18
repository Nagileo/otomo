from __future__ import annotations

import time

from otomo.recommendation_cache import RecommendationArtifactCache


def test_recommendation_artifact_cache_is_persistent_and_expires(tmp_path):
    path = str(tmp_path / "artifacts.sqlite3")
    cache = RecommendationArtifactCache(path, ttl=60)
    cache.set("review:v2:anime:1", {"subject_id": 1, "consensus": "稳定"})

    restored = RecommendationArtifactCache(path, ttl=60)
    assert restored.get("review:v2:anime:1") == {"subject_id": 1, "consensus": "稳定"}
    assert restored.stats()["hits"] == 1

    with restored._connect() as conn:
        conn.execute(
            "UPDATE recommendation_artifacts SET expires_at=? WHERE cache_key=?",
            (time.time() - 1, "review:v2:anime:1"),
        )
    assert restored.get("review:v2:anime:1") is None
