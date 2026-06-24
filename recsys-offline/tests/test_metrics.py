"""合成单测：验证指标与流行度基线（不依赖下载任何数据集）。"""
import math

from recsys_offline.baseline import PopularityRecommender
from recsys_offline.metrics import (
    evaluate,
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    recall_at_k,
)


def test_recall_and_hit():
    assert recall_at_k([1, 2, 3], {2, 4}, 2) == 0.5  # 命中 2，relevant 共 2 个
    assert hit_rate_at_k([1, 2], {2}, 1) == 0.0       # top1 未命中
    assert hit_rate_at_k([1, 2], {2}, 2) == 1.0


def test_ndcg():
    # ranked=[1,2,3], relevant={3} → 命中在第 3 位(idx2)：dcg=1/log2(4)=0.5, idcg=1 → 0.5
    assert abs(ndcg_at_k([1, 2, 3], {3}, 3) - 0.5) < 1e-9
    # 命中在第 1 位 → 满分
    assert abs(ndcg_at_k([3, 1, 2], {3}, 3) - 1.0) < 1e-9


def test_mrr():
    assert abs(mrr([1, 2, 3], {3}) - (1 / 3)) < 1e-9
    assert mrr([1, 2], {9}) == 0.0


def test_evaluate_averages():
    recs = {1: [10, 20, 30], 2: [40, 50, 60]}
    truth = {1: {20}, 2: {40}}
    out = evaluate(recs, truth, ks=(1, 3))
    assert out["hit@3"] == 1.0          # 两用户 top3 都命中
    assert out["hit@1"] == 0.5          # 用户1 top1 未中、用户2 中
    assert abs(out["mrr"] - ((1 / 2) + 1) / 2) < 1e-9  # 用户1 第2位、用户2 第1位


def test_popularity_baseline():
    inter = [(1, 100), (2, 100), (3, 100), (1, 200), (2, 200), (1, 300)]
    rec = PopularityRecommender().fit(inter)  # 热度 100>200>300
    assert rec.recommend(seen=set(), n=2) == [100, 200]
    assert rec.recommend(seen={100}, n=2) == [200, 300]  # 排除已看
