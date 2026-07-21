"""LTR 第一步：逻辑回归学习排序，替代手调线性和（推荐进化链的第三环）。

叙事：手调权重 → 随机搜索(eval_recommend --search) → **学习排序**。前两步都在调
一个固定形式的线性和的系数；这一步让数据自己决定特征怎么组合。

方法：对每个评测用户 hold-out N 部高分作 → recommend(export_features=True, 大 limit)
拿到候选池的特征向量 → 命中 hold-out 的候选=正样本、其余=负样本 → 训 LogisticRegression
→ 5 折 CV 报 AUC，并把学到的系数 vs 现有手调权重并列，离线对比 top-K 命中。

诚实边界：hold-out 命中数很稀疏（每用户 5 部藏进几十候选），正样本极少，AUC 会有方差；
这是"打通学习排序管线 + 拿到特征重要性信号"，不是"训出生产模型"。是否接入生产由增益定。

用法（backend/ 下，需 scikit-learn）：
  python -m scripts.train_ltr --users-file ../eval/holdout_users.json --max-users 20 --holdout 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from otomo.tools.bangumi.client import BangumiClient  # noqa: E402
from otomo.tools.recommend.tool import RecommendArgs, RecommendTool, RerankWeights  # noqa: E402

FEATURE_ORDER = [
    "affinity", "graph", "cf", "external", "explicit",
    "memory_pen", "temporary_pen", "aspect", "subtype_pen", "semantic", "quality",
]


class HoldoutClient:
    def __init__(self, inner: BangumiClient, holdout_ids: set[int]) -> None:
        self._inner = inner
        self._holdout = holdout_ids

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def get_all_user_collections(self, *args, **kwargs):
        items = await self._inner.get_all_user_collections(*args, **kwargs)
        return [it for it in items if int((it.get("subject") or {}).get("id") or 0) not in self._holdout]


async def collect_samples(args: argparse.Namespace) -> tuple[list[list[float]], list[int]]:
    spec = json.loads(Path(args.users_file).read_text(encoding="utf-8"))
    users = [u["username"] for u in spec.get("users") or []][: args.max_users]
    rng = random.Random(args.seed)
    X: list[list[float]] = []
    y: list[int] = []
    async with BangumiClient() as client:
        for idx, user in enumerate(users, 1):
            try:
                allc = await client.get_all_user_collections(user, 2, None, max_items=1000)
            except Exception as e:  # noqa: BLE001
                print(f"  [{user}] 拉取失败 {type(e).__name__}")
                continue
            pool = [it for it in allc if int(it.get("rate") or 0) >= args.min_rate and (it.get("subject") or {}).get("id")]
            if len(pool) < args.holdout + 3:
                continue
            held = rng.sample(pool, args.holdout)
            held_ids = {int(it["subject"]["id"]) for it in held}
            proxy = HoldoutClient(client, held_ids)
            tool = RecommendTool(proxy)
            res = await tool.run(RecommendArgs(
                subject_type="anime", username=user, limit=20, enrich_evidence=False,
                use_graph=True, use_cf=True, use_aspect_profile=True, use_curation=True,
                use_external_recall=True, use_semantic=True, use_series=False,
                export_features=True,  # pool_limit 内部放大到 60 以容纳正样本
            ))
            if not res.ok or res.data is None:
                continue
            pos = 0
            for it in res.data.items:
                if not it.features:
                    continue
                label = 1 if int(it.id) in held_ids else 0
                X.append([float(it.features.get(k, 0.0)) for k in FEATURE_ORDER])
                y.append(label)
                pos += label
            print(f"  [{idx}/{len(users)}] {user}: {len(res.data.items)} 候选, {pos} 正样本")
    return X, y


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--users-file", required=True)
    ap.add_argument("--max-users", type=int, default=20)
    ap.add_argument("--min-rate", type=int, default=8)
    ap.add_argument("--holdout", type=int, default=5)
    ap.add_argument("--pool", type=int, default=40, help="每用户候选池大小（正样本要能落进来）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="../eval/ltr_report.json")
    args = ap.parse_args()

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_score
    except ImportError:
        sys.exit("需要 scikit-learn：pip install scikit-learn")

    print(f"收集训练样本（{args.max_users} 用户 × hold-out {args.holdout}）…")
    X, y = await collect_samples(args)
    X_arr, y_arr = np.array(X, dtype=float), np.array(y, dtype=int)
    n_pos = int(y_arr.sum())
    print(f"\n样本 {len(y)} 条，正样本 {n_pos}（{n_pos / max(len(y), 1) * 100:.1f}%）")
    if n_pos < 10:
        sys.exit(f"正样本太少（{n_pos}），加大 --max-users 或 --pool 再试。")

    clf = LogisticRegression(max_iter=1000, class_weight="balanced")
    auc = cross_val_score(clf, X_arr, y_arr, cv=5, scoring="roc_auc")
    clf.fit(X_arr, y_arr)
    coefs = dict(zip(FEATURE_ORDER, clf.coef_[0].round(3), strict=False))
    hand = RerankWeights().model_dump()

    print(f"\n5 折 CV AUC = {auc.mean():.3f} ± {auc.std():.3f}")
    print(f"\n{'特征':<14}{'LTR 系数':<12}{'手调权重':<10}")
    for k in FEATURE_ORDER:
        hk = {"graph": "graph_per_hit", "cf": "cf_cap", "external": "external_cap",
              "explicit": "explicit_hit", "memory_pen": "memory_penalty",
              "temporary_pen": "temporary_penalty", "quality": "quality_popular"}.get(k, k)
        print(f"{k:<14}{coefs[k]:<12}{hand.get(hk, '—')}")

    report = {
        "samples": len(y), "positives": n_pos,
        "cv_auc_mean": float(auc.mean()), "cv_auc_std": float(auc.std()),
        "ltr_coefficients": coefs, "hand_weights": hand,
        "note": "特征重要性信号；正样本稀疏 AUC 有方差，是否接入生产由更大样本增益定。",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入 {out}")


if __name__ == "__main__":
    asyncio.run(main())
