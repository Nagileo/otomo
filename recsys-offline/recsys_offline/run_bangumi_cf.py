"""Bangumi 原生 CF：评测(超流行度基线) + 全量重训 + **导出 i2i 相似度表**。

    python -m recsys_offline.run_bangumi_cf --data data/bangumi/collections_anime.csv

闭环关键：导出的 i2i 表 key = Bangumi subject_id，与在线完全一致 →
  直接作为在线 recommend_subjects 的"协同召回 provider"（看过 X 的人也看 Y），
  补上在线天生缺失的协同信号、治重度用户饱和。这是离线真正反哺在线的产物。

两段：
  1) 评测：leave-one-out，ItemCF(BM25)/ALS 对比流行度基线（证明学到协同口味结构）。
  2) 生产：按时间切分 NDCG 自动择优 → 质量门禁 → 全量加权重训 → i2i JSON。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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

from .bangumi_data import WEIGHTING_VERSION, filter_active, load_bangumi_weighted
from .baseline import PopularityRecommender
from .metrics import evaluate
from .model_selection import choose_export_model
from .split import leave_one_out, remove_held_out_interactions, temporal_leave_one_out

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


def _csr(pairs, u2idx, i2idx, n_u, n_i):
    rows = np.fromiter((u2idx[u] for u, _ in pairs), np.int32, len(pairs))
    cols = np.fromiter((i2idx[i] for _, i in pairs), np.int32, len(pairs))
    return sp.csr_matrix((np.ones(len(pairs), np.float32), (rows, cols)), shape=(n_u, n_i))


def _weighted_csr(frame, u2idx, i2idx, n_u, n_i):
    rows = np.fromiter((u2idx[int(u)] for u in frame["user_id"]), np.int32, len(frame))
    cols = np.fromiter((i2idx[int(i)] for i in frame["subject_id"]), np.int32, len(frame))
    values = frame["weight"].to_numpy(dtype=np.float32)
    return sp.csr_matrix((values, (rows, cols)), shape=(n_u, n_i))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/bangumi/collections_anime.csv")
    ap.add_argument("--min-user", type=int, default=5, help="用户最少交互数（过滤过冷用户）")
    ap.add_argument("--min-item", type=int, default=5, help="物品最少交互数（过滤过冷物品）")
    ap.add_argument("--factors", type=int, default=64)
    ap.add_argument("--sample-users", type=int, default=5000)
    ap.add_argument("--topk", type=int, default=50, help="i2i 每个物品导出的邻居数")
    ap.add_argument("--export-model", choices=["auto", "bm25", "als"], default="auto",
                    help="auto 按 NDCG@10 择优；所有模式都必须超过流行度质量门禁")
    ap.add_argument("--min-relative-lift", type=float, default=0.01,
                    help="auto 发布所需的相对流行度基线增益")
    ap.add_argument("--half-life-days", type=float, default=730.0,
                    help="行为时间权重半衰期；相对数据快照最新时间计算")
    ap.add_argument("--time-floor", type=float, default=0.55,
                    help="时间权重下限，避免抹掉长期经典口味")
    ap.add_argument("--out", default="", help="i2i JSON 输出路径（默认与 data 同目录 i2i_<stype>.json）")
    ap.add_argument("--split", choices=["temporal", "random"], default="temporal")
    args = ap.parse_args()
    if args.min_relative_lift < 0:
        ap.error("--min-relative-lift must be >= 0")
    if not 0 <= args.time_floor <= 1:
        ap.error("--time-floor must be between 0 and 1")

    df = load_bangumi_weighted(
        args.data,
        half_life_days=args.half_life_days,
        time_floor=args.time_floor,
    )
    raw_u, raw_i, raw_n = df["user_id"].nunique(), df["subject_id"].nunique(), len(df)
    df = filter_active(df, args.min_user, args.min_item)
    positive = df[(df["ctype"].isin((2, 3))) & (df["weight"] > 0)].copy()
    uids = positive["user_id"].unique()
    sids = positive["subject_id"].unique()
    df = df[df["user_id"].isin(uids) & df["subject_id"].isin(sids)].copy()
    positive = positive[positive["user_id"].isin(uids) & positive["subject_id"].isin(sids)]
    negative_count = int((df["weight"] < 0).sum())
    print(
        f"原始 {raw_n:,} 交互 / {raw_u:,}用户 / {raw_i:,}物品 → 过滤后 "
        f"{len(df):,} 加权交互（正 {int((df['weight'] > 0).sum()):,} / 负 {negative_count:,}）"
        f" / {len(uids):,}用户 / {len(sids):,}物品"
        f"（密度 {len(df)/max(len(uids)*len(sids),1)*100:.3f}%）"
    )
    if len(uids) < 50 or len(sids) < 50:
        print("⚠ 数据太稀疏，先扩大采集（更大 uid 区间）再训练。")
        return

    u2idx = {u: k for k, u in enumerate(uids)}
    i2idx = {s: k for k, s in enumerate(sids)}
    idx2item = sids

    # ---------- 1) 评测：leave-one-out ---------- #
    split_used = args.split
    has_timestamps = (
        "updated_at" in positive.columns
        and positive["updated_at"].astype(str).str.strip().replace("nan", "").ne("").any()
    )
    if args.split == "temporal" and has_timestamps:
        train, test = temporal_leave_one_out(positive, item_col="subject_id")
    else:
        if args.split == "temporal":
            print("⚠ CSV 没有 updated_at，评测降级为固定随机 leave-one-out；新采集任务会写入时间戳。")
            split_used = "random_fallback"
        train, test = leave_one_out(positive, item_col="subject_id")
    train_weighted = remove_held_out_interactions(df, test)
    positive_mat = _csr(train, u2idx, i2idx, len(uids), len(sids))
    weighted_mat = _weighted_csr(train_weighted, u2idx, i2idx, len(uids), len(sids))
    seen: dict[int, set[int]] = defaultdict(set)
    for row in train_weighted.itertuples():
        seen[int(row.user_id)].add(int(row.subject_id))

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

    model_specs = {
        "ItemCF(BM25)": (
            BM25Recommender(K=100), positive_mat,
        ),
        "ALS-MF": (
            AlternatingLeastSquares(
                factors=args.factors, iterations=15, regularization=0.05, random_state=42
            ),
            weighted_mat,
        ),
    }
    for name, (model, matrix) in model_specs.items():
        t0 = time.monotonic()
        model.fit(matrix, show_progress=False)
        ids, _ = model.recommend(uidx, matrix[uidx], N=20, filter_already_liked_items=True)
        recs = {users[k]: [int(idx2item[j]) for j in ids[k]] for k in range(len(users))}
        results[name] = (evaluate(recs, truth, ks=(10,)), time.monotonic() - t0)

    print(f"\n== Bangumi 原生 CF 评测（{len(users):,} 用户 {split_used} leave-one-out）==")
    print(f"  {'模型':<16}{'NDCG@10':>10}{'Recall@10':>11}{'HitRate@10':>12}{'MRR':>9}{'耗时s':>8}")
    base = results["流行度baseline"][0]["ndcg@10"]
    for name, (m, dt) in results.items():
        lift = f"(+{(m['ndcg@10']/base-1)*100:.0f}%)" if name != "流行度baseline" and base else ""
        print(
            f"  {name:<16}{m['ndcg@10']:>10.4f}{m['recall@10']:>11.4f}"
            f"{m['hit@10']:>12.4f}{m['mrr']:>9.4f}{dt:>8.1f}  {lift}"
        )

    # ---------- 2) 自动择优 + 质量门禁 + 全量重训 ---------- #
    selected_model, publishable, selected_score = choose_export_model(
        results, args.export_model, args.min_relative_lift,
    )
    baseline_score = float(results["流行度baseline"][0]["ndcg@10"])
    relative_lift = selected_score / baseline_score - 1 if baseline_score else 0.0
    print(
        f"\n选择 {selected_model.upper()}：NDCG@10={selected_score:.4f}，"
        f"相对流行度 {relative_lift * 100:+.1f}%"
    )
    if not publishable:
        print("⚠ 所选协同模型未通过流行度质量门禁；保留线上旧模型，不发布本次产物。")
        raise SystemExit(3)

    print(f"用全量加权交互重训 {selected_model.upper()}（生产模型）…")
    full_positive = _csr(
        list(positive[["user_id", "subject_id"]].itertuples(index=False, name=None)),
        u2idx, i2idx, len(uids), len(sids),
    )
    full_weighted = _weighted_csr(df, u2idx, i2idx, len(uids), len(sids))
    if selected_model == "als":
        prod = AlternatingLeastSquares(
            factors=args.factors, iterations=20, regularization=0.05, random_state=42
        )
        full = full_weighted
    else:
        prod = BM25Recommender(K=max(args.topk + 1, 100))
        full = full_positive
    prod.fit(full, show_progress=False)

    print(f"导出 i2i（每物品 top-{args.topk} 邻居）…")
    all_idx = np.arange(len(sids), dtype=np.int32)
    nbr_ids, nbr_scores = prod.similar_items(all_idx, N=args.topk + 1)  # 含自身
    item_counts = np.asarray(full_positive.sum(axis=0)).ravel()

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
    built_at = datetime.now(timezone.utc)
    payload = {
        "meta": {
            "model": selected_model,
            "factors": args.factors,
            "n_items": len(i2i),
            "topk": args.topk,
            "n_interactions": int(len(df)),
            "n_positive_interactions": int((df["weight"] > 0).sum()),
            "n_negative_interactions": negative_count,
            "n_users": int(len(uids)),
            "built_at": built_at.isoformat(),
            "version": built_at.strftime("%Y%m%d-%H%M%S"),
            "eval_split": split_used,
            "eval": {
                name: metrics
                for name, (metrics, _duration) in results.items()
            },
            "baseline_ndcg_at_10": round(baseline_score, 6),
            "selected_ndcg_at_10": round(selected_score, 6),
            "relative_lift": round(relative_lift, 6),
            "quality_gate_passed": publishable,
            "weighting_version": WEIGHTING_VERSION,
            "half_life_days": args.half_life_days,
            "time_floor": args.time_floor,
            "time_weighting_applied": bool(has_timestamps),
            "subject_type": os.path.basename(args.data).removeprefix("collections_").removesuffix(".csv"),
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
