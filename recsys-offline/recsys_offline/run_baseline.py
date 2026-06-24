"""S0 流行度基线：加载 → leave-one-out → 流行度推荐 → 指标表。

    python -m recsys_offline.run_baseline --data data/rating.csv

打印 Recall@K / NDCG@K / HitRate@K / MRR —— 这是后续 ItemCF/MF/LambdaMART 必须超越的底线。
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from collections import defaultdict

from .baseline import PopularityRecommender
from .data import load_positive_interactions
from .metrics import evaluate
from .split import leave_one_out

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/rating.csv")
    ap.add_argument("--min-rating", type=int, default=7)
    ap.add_argument("--sample-users", type=int, default=5000, help="评测抽样用户数(0=全部)")
    ap.add_argument("--n", type=int, default=20, help="推荐列表长度")
    args = ap.parse_args()

    t0 = time.monotonic()
    df = load_positive_interactions(args.data, args.min_rating)
    print(f"正反馈交互 {len(df):,} 条（rating≥{args.min_rating}）；用户 {df['user_id'].nunique():,}、物品 {df['anime_id'].nunique():,}")

    train, test = leave_one_out(df)
    seen: dict[int, set[int]] = defaultdict(set)
    for u, i in train:
        seen[u].add(i)
    print(f"train 交互 {len(train):,}；test 用户 {len(test):,}（每人留 1）")

    pop = PopularityRecommender().fit(train)

    users = list(test)
    if args.sample_users and len(users) > args.sample_users:
        random.Random(0).shuffle(users)
        users = users[: args.sample_users]

    recs = {u: pop.recommend(seen[u], args.n) for u in users}
    truth = {u: test[u] for u in users}
    metrics = evaluate(recs, truth, ks=(5, 10, 20))

    print(f"\n== 流行度基线（评测 {len(users):,} 用户，{time.monotonic() - t0:.1f}s）==")
    for k in sorted(metrics):
        print(f"  {k:<12} {metrics[k]:.4f}")


if __name__ == "__main__":
    main()
