"""S2（核心）：ALS 召回 → LightGBM LambdaMART 重排，对比 ALS 单独。

    python -m recsys_offline.run_s2 --data data/rating.csv --anime data/anime.csv

漏斗：ALS 取 top-K 候选 → 每个 (用户,候选) 算交叉特征 → LambdaMART 学习重排 → 取 top-N。
防泄漏：ALS 训练不含留出项；LTR 训练用户与评测用户**不相交**。报 NDCG@10 较 ALS 单独的提升。
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter, defaultdict

import lightgbm as lgb
import numpy as np
import pandas as pd
import scipy.sparse as sp
from implicit.als import AlternatingLeastSquares

from .data import load_positive_interactions
from .metrics import evaluate
from .split import leave_one_out

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

K = 100   # ALS 召回候选数
N = 10    # 最终推荐长度（评 NDCG@10）


def build_item_features(anime_csv: str, i2idx: dict[int, int], n_items: int):
    a = pd.read_csv(anime_csv, usecols=["anime_id", "genre", "type", "episodes", "rating", "members"])
    rating = dict(zip(a["anime_id"], pd.to_numeric(a["rating"], errors="coerce")))
    members = dict(zip(a["anime_id"], pd.to_numeric(a["members"], errors="coerce")))
    eps = dict(zip(a["anime_id"], pd.to_numeric(a["episodes"], errors="coerce")))
    typ = dict(zip(a["anime_id"], a["type"].astype(str)))
    genre = dict(zip(a["anime_id"], a["genre"].astype(str)))

    f_rating = np.full(n_items, np.nan, np.float32)
    f_members = np.zeros(n_items, np.float32)
    f_eps = np.full(n_items, np.nan, np.float32)
    f_type = np.zeros(n_items, np.int32)
    genres: list[list[str]] = [[] for _ in range(n_items)]
    type_codes: dict[str, int] = {}
    for aid, idx in i2idx.items():
        f_rating[idx] = rating.get(aid, np.nan)
        m = members.get(aid, 0)
        f_members[idx] = np.log1p(m if m == m else 0)  # log 人气
        f_eps[idx] = eps.get(aid, np.nan)
        f_type[idx] = type_codes.setdefault(typ.get(aid, "Unknown"), len(type_codes))
        g = genre.get(aid, "")
        g = g if isinstance(g, str) else ""
        genres[idx] = [x.strip() for x in g.split(",") if x.strip()] if g and g != "nan" else []
    return f_rating, f_members, f_eps, f_type, genres


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/rating.csv")
    ap.add_argument("--anime", default="data/anime.csv")
    ap.add_argument("--min-rating", type=int, default=7)
    ap.add_argument("--ltr-users", type=int, default=8000)
    ap.add_argument("--eval-users", type=int, default=4000)
    ap.add_argument("--factors", type=int, default=64)
    args = ap.parse_args()

    df = load_positive_interactions(args.data, args.min_rating)
    train, test = leave_one_out(df)
    uids = df["user_id"].unique()
    iids = df["anime_id"].unique()
    u2idx = {u: k for k, u in enumerate(uids)}
    i2idx = {a: k for k, a in enumerate(iids)}
    n_items = len(iids)

    rows = np.fromiter((u2idx[u] for u, _ in train), np.int32, len(train))
    cols = np.fromiter((i2idx[a] for _, a in train), np.int32, len(train))
    mat = sp.csr_matrix((np.ones(len(train), np.float32), (rows, cols)), shape=(len(uids), n_items))

    seen_idx: dict[int, list[int]] = defaultdict(list)
    for u, i in train:
        seen_idx[u].append(i2idx[i])

    print("训练 ALS …")
    als = AlternatingLeastSquares(factors=args.factors, iterations=15, regularization=0.05, random_state=42)
    als.fit(mat, show_progress=False)

    f_rating, f_members, f_eps, f_type, genres = build_item_features(args.anime, i2idx, n_items)

    # 用户按 held-out item-idx 与 genre 画像
    held_idx = {u: i2idx[next(iter(s))] for u, s in test.items()}
    test_users = list(test)
    random.Random(0).shuffle(test_users)
    ltr_users = test_users[: args.ltr_users]
    eval_users = test_users[args.ltr_users : args.ltr_users + args.eval_users]

    def user_genre_counter(u: int) -> Counter:
        gc: Counter = Counter()
        for it_idx in seen_idx[u]:
            gc.update(genres[it_idx])
        return gc

    def feats(u: int, cand_idx: np.ndarray, cand_score: np.ndarray) -> np.ndarray:
        gc = user_genre_counter(u)
        tot = max(sum(gc.values()), 1)
        overlap = np.array([sum(gc.get(g, 0) for g in genres[c]) / tot for c in cand_idx], np.float32)
        return np.column_stack([
            cand_score.astype(np.float32),           # ALS 分
            f_rating[cand_idx],                        # 全局评分
            f_members[cand_idx],                       # log 人气
            f_eps[cand_idx],                           # 集数
            overlap,                                   # genre 与用户口味重叠（每候选不同→关键排序信号）
            f_type[cand_idx].astype(np.float32),       # 类型
        ])

    def candidates(users: list[int]):
        uidx = np.array([u2idx[u] for u in users], np.int32)
        ids, scores = als.recommend(uidx, mat[uidx], N=K, filter_already_liked_items=True)
        return ids, scores  # (len,K)

    FEATURE_NAMES = ["als_score", "item_rating", "members_log", "episodes", "genre_overlap", "type"]

    # ---- 构造 LTR 训练集（只用 ltr_users）---- #
    print(f"构造 LTR 训练特征（{len(ltr_users)} 用户 × {K} 候选）…")
    ids, scores = candidates(ltr_users)
    X, y, group = [], [], []
    for k, u in enumerate(ltr_users):
        cand = ids[k]
        X.append(feats(u, cand, scores[k]))
        y.append((cand == held_idx[u]).astype(np.int8))  # 命中留出项=1
        group.append(K)
    X = np.vstack(X)
    y = np.concatenate(y)
    print(f"  LTR 训练样本 {X.shape}，正例(召回命中){int(y.sum())}")

    ranker = lgb.LGBMRanker(
        objective="lambdarank", metric="ndcg", n_estimators=300, learning_rate=0.05,
        num_leaves=31, random_state=42, verbose=-1,
    )
    ranker.fit(X, y, group=group)

    # ---- 评测（eval_users）：ALS 单独 vs LambdaMART 重排 ---- #
    print(f"评测 {len(eval_users)} 用户 …")
    ids_e, scores_e = candidates(eval_users)
    als_recs, ltr_recs, truth = {}, {}, {}
    for k, u in enumerate(eval_users):
        cand = ids_e[k]
        als_recs[u] = [int(iids[c]) for c in cand[:N]]                 # ALS 原序 top-N
        pred = ranker.predict(feats(u, cand, scores_e[k]))
        order = cand[np.argsort(-pred)]                                # GBM 重排
        ltr_recs[u] = [int(iids[c]) for c in order[:N]]
        truth[u] = test[u]

    m_als = evaluate(als_recs, truth, ks=(10,))
    m_ltr = evaluate(ltr_recs, truth, ks=(10,))

    print("\n== S2：ALS 召回 vs LambdaMART 重排 ==")
    print(f"  {'方法':<22}{'NDCG@10':>10}{'Recall@10':>11}{'MRR':>9}")
    print(f"  {'ALS 单独':<22}{m_als['ndcg@10']:>10.4f}{m_als['recall@10']:>11.4f}{m_als['mrr']:>9.4f}")
    lift = (m_ltr['ndcg@10'] / m_als['ndcg@10'] - 1) * 100 if m_als['ndcg@10'] else 0
    print(f"  {'ALS→LambdaMART 重排':<20}{m_ltr['ndcg@10']:>10.4f}{m_ltr['recall@10']:>11.4f}{m_ltr['mrr']:>9.4f}  (+{lift:.0f}%)")
    print("\n  特征重要度：")
    for name, imp in sorted(zip(FEATURE_NAMES, ranker.feature_importances_), key=lambda x: -x[1]):
        print(f"    {name:<14}{imp}")


if __name__ == "__main__":
    main()
