from otomo.anime_hub_metrics import AnimeHubMetricStore


def test_anime_hub_metrics_persist_module_latency_failure_and_cache(tmp_path):
    store = AnimeHubMetricStore(str(tmp_path / "hub.sqlite3"))
    store.record(
        subject_id=42,
        stage="identity",
        total_ms=120,
        modules={"identity": {"status": "ready", "duration_ms": 100, "cache_hit": False}},
    )
    store.record(
        subject_id=42,
        stage="videos",
        total_ms=900,
        modules={"videos": {"status": "failed", "duration_ms": 880, "cache_hit": True}},
    )
    summary = AnimeHubMetricStore(str(tmp_path / "hub.sqlite3")).summary(30)
    assert summary["runs"] == 2
    assert summary["p95_ms"] == 900
    assert summary["modules"]["identity"]["failure_rate"] == 0
    assert summary["modules"]["videos"]["failure_rate"] == 1
    assert summary["modules"]["videos"]["cache_hit_rate"] == 1
