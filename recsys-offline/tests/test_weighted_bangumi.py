import pandas as pd

from recsys_offline.bangumi_data import (
    WEIGHTING_VERSION,
    filter_active,
    load_bangumi_weighted,
)
from recsys_offline.model_selection import choose_export_model
from recsys_offline.split import remove_held_out_interactions


def test_weighted_interactions_use_latest_state_rating_and_time(tmp_path):
    source = tmp_path / "collections_anime.csv"
    pd.DataFrame([
        {"user_id": 1, "subject_id": 10, "ctype": 2, "rate": 10, "updated_at": "2025-01-01T00:00:00Z"},
        {"user_id": 1, "subject_id": 10, "ctype": 5, "rate": 2, "updated_at": "2026-01-01T00:00:00Z"},
        {"user_id": 1, "subject_id": 11, "ctype": 2, "rate": 9, "updated_at": "2024-01-01T00:00:00Z"},
        {"user_id": 1, "subject_id": 12, "ctype": 2, "rate": 9, "updated_at": "2026-01-01T00:00:00Z"},
        {"user_id": 1, "subject_id": 13, "ctype": 1, "rate": 0, "updated_at": "2026-01-01T00:00:00Z"},
        {"user_id": 1, "subject_id": 14, "ctype": 4, "rate": 0, "updated_at": "2026-01-01T00:00:00Z"},
    ]).to_csv(source, index=False)

    frame = load_bangumi_weighted(str(source), half_life_days=365, time_floor=0.5)
    weights = dict(zip(frame["subject_id"], frame["weight"], strict=True))

    assert WEIGHTING_VERSION == "bangumi-weighted-v2"
    assert len(frame[frame["subject_id"] == 10]) == 1
    assert weights[10] < -1.0  # latest state is abandoned, not the old 10/10
    assert weights[12] > weights[11] > 0  # same rating, recent preference is stronger
    assert 0 < weights[13] < 0.5  # wishlist is intentionally weak
    assert weights[14] < 0  # on-hold is a weak negative


def test_filter_active_counts_positive_support_not_negative_rows():
    frame = pd.DataFrame([
        {"user_id": 1, "subject_id": 10, "weight": 1.0},
        {"user_id": 2, "subject_id": 10, "weight": 0.8},
        {"user_id": 1, "subject_id": 20, "weight": -1.0},
        {"user_id": 2, "subject_id": 20, "weight": -0.5},
        {"user_id": 3, "subject_id": 20, "weight": -0.3},
    ])
    filtered = filter_active(frame, min_user=1, min_item=2)
    assert set(filtered["subject_id"]) == {10}


def test_auto_model_selection_requires_popularity_lift():
    results = {
        "流行度baseline": ({"ndcg@10": 0.10}, 0.1),
        "ItemCF(BM25)": ({"ndcg@10": 0.12}, 0.1),
        "ALS-MF": ({"ndcg@10": 0.15}, 0.1),
    }
    assert choose_export_model(results, "auto", 0.01) == ("als", True, 0.15)

    weak = {
        **results,
        "ItemCF(BM25)": ({"ndcg@10": 0.099}, 0.1),
        "ALS-MF": ({"ndcg@10": 0.10}, 0.1),
    }
    assert choose_export_model(weak, "auto", 0.01) == ("als", False, 0.10)
    assert choose_export_model(weak, "bm25", 0.01) == ("bm25", False, 0.099)


def test_weighted_holdout_removes_only_the_held_user_item_pair():
    frame = pd.DataFrame([
        {"user_id": 1, "subject_id": 10, "weight": 1.5},
        {"user_id": 1, "subject_id": 11, "weight": -0.8},
        {"user_id": 2, "subject_id": 10, "weight": 1.0},
    ])

    train = remove_held_out_interactions(frame, {1: {10}})

    assert set(map(tuple, train[["user_id", "subject_id"]].to_numpy())) == {
        (1, 11),
        (2, 10),
    }
