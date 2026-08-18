"""Lightweight recommendation-model selection and publication gates."""
from __future__ import annotations


def choose_export_model(
    results: dict[str, tuple[dict, float]],
    requested: str,
    min_relative_lift: float,
) -> tuple[str, bool, float]:
    """Choose the strongest CF model and enforce a popularity quality gate."""
    names = {"bm25": "ItemCF(BM25)", "als": "ALS-MF"}
    baseline = float(results["流行度baseline"][0]["ndcg@10"])
    if requested != "auto":
        score = float(results[names[requested]][0]["ndcg@10"])
        return requested, score > baseline * (1.0 + min_relative_lift), score
    selected = max(names, key=lambda key: float(results[names[key]][0]["ndcg@10"]))
    score = float(results[names[selected]][0]["ndcg@10"])
    return selected, score > baseline * (1.0 + min_relative_lift), score
