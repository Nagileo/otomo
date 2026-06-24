"""流行度基线（S0 必报）：按全局热度推 Top-N、排除已看。打不过它 = 模型白做。"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence


class PopularityRecommender:
    def __init__(self) -> None:
        self._ranked: list[int] = []  # 按热度降序的 item-id

    def fit(self, interactions: Iterable[tuple[int, int]]) -> "PopularityRecommender":
        """interactions: (user_id, item_id) 列表（训练集）。"""
        counts = Counter(item for _u, item in interactions)
        self._ranked = [item for item, _c in counts.most_common()]
        return self

    def recommend(self, seen: set[int] | Sequence[int], n: int = 20) -> list[int]:
        seen_set = set(seen)
        out: list[int] = []
        for item in self._ranked:
            if item not in seen_set:
                out.append(item)
                if len(out) >= n:
                    break
        return out
