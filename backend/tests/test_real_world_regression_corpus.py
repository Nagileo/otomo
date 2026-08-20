from __future__ import annotations

import json
from pathlib import Path

from otomo.series_overrides import SeriesOverrideStore
from otomo.tools.media_identity import build_media_identity
from otomo.tools.release.tool import _classify_release_content
from otomo.tools.videos.tool import classify_subject_video


CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "anime_hub_real_world_regressions.json").read_text(
        encoding="utf-8"
    )
)


def test_real_world_false_positive_corpus_stays_rejected(tmp_path):
    identity = build_media_identity(title="轻音少女", aliases=["K-ON!"])
    for case in CORPUS["release_false_positives"]:
        assert _classify_release_content(case["title"], identity)[0] == case["expected"]

    for case in CORPUS["bilibili_false_positives"]:
        role, _uploader, watch_candidate, *_rest = classify_subject_video(
            case["title"],
            "音乐收藏UP",
            case["description"],
            duration_seconds=case["duration_seconds"],
            expected_duration_seconds=24 * 60,
            match_confidence=0.98,
        )
        assert role == case["expected_role"]
        assert watch_candidate is False

    rules = CORPUS["series_rules"]
    store = SeriesOverrideStore(tmp_path / "series.json")
    monogatari = store.find_by_subject(rules["monogatari_seed"])
    fate = store.find_by_subject(rules["fate_seed"])
    assert monogatari is not None and fate is not None
    assert [row.subject_id for row in monogatari.mainline[:7]] == rules["monogatari_prefix"]
    assert [row.subject_id for row in fate.mainline] == rules["fate_mainline"]
    assert [row.subject_id for row in fate.optional] == rules["fate_heavens_feel"]


def test_reachability_counterexamples_never_claim_playability():
    for case in CORPUS["official_probe_counterexamples"]:
        assert case["proves_playability"] is False
