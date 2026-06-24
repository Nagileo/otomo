"""S1：协同过滤 / 矩阵分解，与流行度基线同台对比（implicit 库）。

    python -m recsys_offline.run_s1 --data data/rating.csv

模型：流行度(基线) · ItemCF(BM25) · ALS-MF · BPR-MF。统一 leave-one-out + 同一抽样用户，打印指标表。
目标：MF/CF 的 NDCG@10 显著超越流行度基线（证明学到了协同的口味结构）。
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from collections import defaultdict

import numpy as np
import scipy.sparse as sp
from implicit.als import AlternatingLeastSquares
from implicit.bpr import BayesianPersonalizedRanking
from implicit.nearest_neighbours import BM25Recommender

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
    ap.add_argument("--sample-users", type=int, default=5000)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--factors", type=int, default=64)
    args = ap.parse_args()

    df = load_positive_interactions(args.data, args.min_rating)
    train, test = leave_one_out(df)
    print(f"正反馈 {len(df):,}；train {len(train):,}；test 用户 {len(test):,}")

    # id → 连续索引
    uids = df["user_id"].unique()
    iids = df["anime_id"].unique()
    u2idx = {u: k for k, u in enumerate(uids)}
    i2idx = {a: k for k, a in enumerate(iids)}
    idx2item = iids  # 索引 → anime_id

    rows = np.fromiter((u2idx[u] for u, _ in train), dtype=np.int32, count=len(train))
    cols = np.fromiter((i2idx[a] for _, a in train), dtype=np.int32, count=len(train))
    mat = sp.csr_matrix(
        (np.ones(len(train), dtype=np.float32), (rows, cols)), shape=(len(uids), len(iids))
    )

    seen: dict[int, set[int]] = defaultdict(set)
    for u, i in train:
        seen[u].add(i)

    users = list(test)
    if args.sample_users and len(users) > args.sample_users:
        random.Random(0).shuffle(users)
        users = users[: args.sample_users]
    truth = {u: test[u] for u in users}
    uidx = np.array([u2idx[u] for u in users], dtype=np.int32)

    results: dict[str, dict] = {}

    # --- 流行度基线 --- #
    t0 = time.monotonic()
    pop = PopularityRecommender().fit(train)
    recs = {u: pop.recommend(seen[u], args.n) for u in users}
    results["流行度baseline"] = (evaluate(recs, truth, ks=(10,)), time.monotonic() - t0)

    # --- implicit 模型 --- #
    models = {
        "ItemCF(BM25)": BM25Recommender(K=100),
        "ALS-MF": AlternatingLeastSquares(factors=args.factors, iterations=15, regularization=0.05, random_state=42),
        "BPR-MF": BayesianPersonalizedRanking(factors=args.factors, iterations=80, random_state=42),
    }
    for name, model in models.items():
        t0 = time.monotonic()
        model.fit(mat, show_progress=False)
        ids, _scores = model.recommend(uidx, mat[uidx], N=args.n, filter_already_liked_items=True)
        recs = {users[k]: [int(idx2item[j]) for j in ids[k]] for k in range(len(users))}
        results[name] = (evaluate(recs, truth, ks=(10,)), time.monotonic() - t0)

    # --- 对比表 --- #
    print(f"\n== S1 对比（评测 {len(users):,} 用户）==")
    print(f"  {'模型':<16}{'NDCG@10':>10}{'Recall@10':>11}{'HitRate@10':>12}{'MRR':>9}{'耗时s':>8}")
    base = results['流行度baseline'][0]['ndcg@10']
    for name, (m, dt) in results.items():
        lift = f"(+{(m['ndcg@10']/base-1)*100:.0f}%)" if name != '流行度baseline' and base else ""
        print(f"  {name:<16}{m['ndcg@10']:>10.4f}{m['recall@10']:>11.4f}{m['hit@10']:>12.4f}{m['mrr']:>9.4f}{dt:>8.1f}  {lift}")


if __name__ == "__main__":
    main()
