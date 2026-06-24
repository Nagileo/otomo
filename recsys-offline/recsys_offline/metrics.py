"""排序指标（纯 Python，便于合成单测）。

每个指标作用于：ranked（推荐的 item-id 有序列表，去重）+ relevant（该用户的留出相关 item-id 集合）。
评测惯例见 docs/05 §3、docs/06 §5：NDCG@K 为头牌，必与流行度基线对比。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def hit_rate_at_k(ranked: Sequence[int], relevant: set[int], k: int) -> float:
    return 1.0 if set(ranked[:k]) & relevant else 0.0


def recall_at_k(ranked: Sequence[int], relevant: set[int], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def precision_at_k(ranked: Sequence[int], relevant: set[int], k: int) -> float:
    return (len(set(ranked[:k]) & relevant) / k) if k else 0.0


def ndcg_at_k(ranked: Sequence[int], relevant: set[int], k: int) -> float:
    """二元相关性的 NDCG@K。"""
    dcg = sum(1.0 / math.log2(i + 2) for i, it in enumerate(ranked[:k]) if it in relevant)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg else 0.0


def mrr(ranked: Sequence[int], relevant: set[int]) -> float:
    """首个命中的倒数排名。"""
    for i, it in enumerate(ranked):
        if it in relevant:
            return 1.0 / (i + 1)
    return 0.0


_METRICS_AT_K = {
    "recall": recall_at_k,
    "ndcg": ndcg_at_k,
    "hit": hit_rate_at_k,
    "precision": precision_at_k,
}


def evaluate(
    recommendations: dict[int, Sequence[int]],
    relevant: dict[int, set[int]],
    ks: Iterable[int] = (5, 10, 20),
) -> dict[str, float]:
    """对每个用户算指标后跨用户取平均。recommendations/relevant 按 user-id 对齐。"""
    users = [u for u in relevant if relevant[u]]
    if not users:
        return {}
    out: dict[str, float] = {}
    for k in ks:
        for name, fn in _METRICS_AT_K.items():
            out[f"{name}@{k}"] = sum(fn(recommendations.get(u, []), relevant[u], k) for u in users) / len(users)
    out["mrr"] = sum(mrr(recommendations.get(u, []), relevant[u]) for u in users) / len(users)
    return out
