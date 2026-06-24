"""Bangumi 原生 CF：评测(超流行度基线) + 全量重训 + **导出 i2i 相似度表**。

    python -m recsys_offline.run_bangumi_cf --data data/bangumi/collections_anime.csv

闭环关键：导出的 i2i 表 key = Bangumi subject_id，与在线完全一致 →
  直接作为在线 recommend_subjects 的"协同召回 provider"（看过 X 的人也看 Y），
  补上在线天生缺失的协同信号、治重度用户饱和。这是离线真正反哺在线的产物。

两段：
  1) 评测：leave-one-out，ItemCF(BM25)/ALS 对比流行度基线（证明学到协同口味结构）。
  2) 生产：用**全量**交互重训 ALS → similar_items 导 top-K 邻居 → JSON。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import defaultdict

import numpy as np
import scipy.sparse as sp
from implicit.als import AlternatingLeastSquares
from implicit.nearest_neighbours import BM25Recommender

from .bangumi_data import filter_active, load_bangumi_positive
from .baseline import PopularityRecommender
from .metrics import evaluate
from .split import leave_one_out

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


def _csr(pairs, u2idx, i2idx, n_u, n_i):
    rows = np.fromiter((u2idx[u] for u, _ in pairs), np.int32, len(pairs))
    cols = np.fromiter((i2idx[i] for _, i in pairs), np.int32, len(pairs))
    return sp.csr_matrix((np.ones(len(pairs), np.float32), (rows, cols)), shape=(n_u, n_i))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/bangumi/collections_anime.csv")
    ap.add_argument("--min-user", type=int, default=5, help="用户最少交互数（过滤过冷用户）")
    ap.add_argument("--min-item", type=int, default=5, help="物品最少交互数（过滤过冷物品）")
    ap.add_argument("--factors", type=int, default=64)
    ap.add_argument("--sample-users", type=int, default=5000)
    ap.add_argument("--topk", type=int, default=50, help="i2i 每个物品导出的邻居数")
    ap.add_argument("--export-model", choices=["bm25", "als"], default="bm25",
                    help="导出 i2i 用的模型：小数据 bm25 更稳，数据够大可切 als")
    ap.add_argument("--out", default="", help="i2i JSON 输出路径（默认与 data 同目录 i2i_<stype>.json）")
    args = ap.parse_args()

    df = load_bangumi_positive(args.data)
    raw_u, raw_i = df["user_id"].nunique(), df["subject_id"].nunique()
    df = filter_active(df, args.min_user, args.min_item)
    uids = df["user_id"].unique()
    sids = df["subject_id"].unique()
    print(
        f"原始 {len(df):,} 交互前 {raw_u:,}用户/{raw_i:,}物品 → 过滤后 "
        f"{len(df):,} 交互 / {len(uids):,}用户 / {len(sids):,}物品"
        f"（密度 {len(df)/max(len(uids)*len(sids),1)*100:.3f}%）"
    )
    if len(uids) < 50 or len(sids) < 50:
        print("⚠ 数据太稀疏，先扩大采集（更大 uid 区间）再训练。")
        return

    u2idx = {u: k for k, u in enumerate(uids)}
    i2idx = {s: k for k, s in enumerate(sids)}
    idx2item = sids

    # ---------- 1) 评测：leave-one-out ---------- #
    train, test = leave_one_out(df, item_col="subject_id")
    mat = _csr(train, u2idx, i2idx, len(uids), len(sids))
    seen: dict[int, set[int]] = defaultdict(set)
    for u, i in train:
        seen[u].add(i)

    users = list(test)
    if args.sample_users and len(users) > args.sample_users:
        random.Random(0).shuffle(users)
        users = users[: args.sample_users]
    truth = {u: test[u] for u in users}
    uidx = np.array([u2idx[u] for u in users], np.int32)

    results: dict[str, tuple[dict, float]] = {}

    t0 = time.monotonic()
    pop = PopularityRecommender().fit(train)
    recs = {u: pop.recommend(seen[u], 20) for u in users}
    results["流行度baseline"] = (evaluate(recs, truth, ks=(10,)), time.monotonic() - t0)

    for name, model in {
        "ItemCF(BM25)": BM25Recommender(K=100),
        "ALS-MF": AlternatingLeastSquares(
            factors=args.factors, iterations=15, regularization=0.05, random_state=42
        ),
    }.items():
        t0 = time.monotonic()
        model.fit(mat, show_progress=False)
        ids, _ = model.recommend(uidx, mat[uidx], N=20, filter_already_liked_items=True)
        recs = {users[k]: [int(idx2item[j]) for j in ids[k]] for k in range(len(users))}
        results[name] = (evaluate(recs, truth, ks=(10,)), time.monotonic() - t0)

    print(f"\n== Bangumi 原生 CF 评测（{len(users):,} 用户 leave-one-out）==")
    print(f"  {'模型':<16}{'NDCG@10':>10}{'Recall@10':>11}{'HitRate@10':>12}{'MRR':>9}{'耗时s':>8}")
    base = results["流行度baseline"][0]["ndcg@10"]
    for name, (m, dt) in results.items():
        lift = f"(+{(m['ndcg@10']/base-1)*100:.0f}%)" if name != "流行度baseline" and base else ""
        print(
            f"  {name:<16}{m['ndcg@10']:>10.4f}{m['recall@10']:>11.4f}"
            f"{m['hit@10']:>12.4f}{m['mrr']:>9.4f}{dt:>8.1f}  {lift}"
        )

    # ---------- 2) 生产：全量重训选定模型 → 导出 i2i ---------- #
    # 用评测胜出的模型导出：小数据 BM25 更稳(本身即共现 i2i)，数据够大时 ALS 泛化更好。
    print(f"\n用全量交互重训 {args.export_model.upper()}（生产模型）…")
    full = _csr(
        list(df[["user_id", "subject_id"]].itertuples(index=False, name=None)),
        u2idx, i2idx, len(uids), len(sids),
    )
    if args.export_model == "als":
        prod = AlternatingLeastSquares(
            factors=args.factors, iterations=20, regularization=0.05, random_state=42
        )
    else:
        prod = BM25Recommender(K=max(args.topk + 1, 100))
    prod.fit(full, show_progress=False)

    print(f"导出 i2i（每物品 top-{args.topk} 邻居）…")
    all_idx = np.arange(len(sids), dtype=np.int32)
    nbr_ids, nbr_scores = prod.similar_items(all_idx, N=args.topk + 1)  # 含自身
    item_counts = np.asarray(full.sum(axis=0)).ravel()  # 各物品交互数（写进 meta，便于在线兜底）

    i2i: dict[str, list] = {}
    for k in range(len(sids)):
        sid = int(idx2item[k])
        pairs = []
        for j, sc in zip(nbr_ids[k], nbr_scores[k]):
            if j == k or sc <= 0:
                continue
            pairs.append([int(idx2item[j]), round(float(sc), 4)])
            if len(pairs) >= args.topk:
                break
        if pairs:
            i2i[str(sid)] = pairs

    out = args.out
    if not out:
        base_name = os.path.basename(args.data).replace("collections_", "i2i_").replace(".csv", ".json")
        out = os.path.join(os.path.dirname(args.data), base_name)
    payload = {
        "meta": {
            "model": args.export_model,
            "factors": args.factors,
            "n_items": len(i2i),
            "topk": args.topk,
            "n_interactions": int(len(df)),
            "n_users": int(len(uids)),
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "popular": [int(idx2item[k]) for k in np.argsort(-item_counts)[:200]],  # 热度兜底
        },
        "items": i2i,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    size_mb = os.path.getsize(out) / 1e6
    print(f"  → {out}  覆盖 {len(i2i):,} 物品，{size_mb:.1f} MB")
    print("  在线接入：recommend_subjects 读此表，对用户高分作品查 i2i 邻居作协同召回。")


if __name__ == "__main__":
    main()
