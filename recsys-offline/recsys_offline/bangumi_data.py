"""Bangumi 原生收藏数据加载（自采，见 bangumi_collect.py）。

collections_{stype}.csv: user_id, subject_id, ctype, rate
ctype(收藏状态): 1想看 2看过 3在看 4搁置 5抛弃。

正反馈默认取 看过(2)+在看(3)：已消费/在消费才反映真实口味；想看(1)是意图非消费、抛弃(5)是负反馈，均排除。
rate 稀疏（很多人不打分，rate=0），故默认**不按 rate 过滤**——隐式 CF 用"交互发生"为信号
（与 MAL 侧 rating>=7 的隐式正反馈范式一致）。需要高置信时再传 min_rate。
"""
from __future__ import annotations

import pandas as pd

POSITIVE_CTYPES = (2, 3)  # 看过 / 在看


def load_bangumi_positive(
    path: str,
    collection_types: tuple[int, ...] = POSITIVE_CTYPES,
    min_rate: int = 0,
) -> pd.DataFrame:
    """返回正反馈交互 (user_id, subject_id) 去重 DataFrame。

    min_rate>0 时只保留 rate>=min_rate（注意会滤掉未评分 rate=0 的交互）。
    """
    df = pd.read_csv(path, usecols=["user_id", "subject_id", "ctype", "rate"])
    df = df[df["ctype"].isin(collection_types)]
    if min_rate > 0:
        df = df[df["rate"] >= min_rate]
    return df[["user_id", "subject_id"]].drop_duplicates()


def filter_active(
    df: pd.DataFrame, min_user: int = 5, min_item: int = 5, rounds: int = 5
) -> pd.DataFrame:
    """迭代过滤稀疏交互：物品交互<min_item、用户交互<min_user 的剔除（CF 必备，否则学不动）。

    交替过滤直到稳定或 rounds 用尽（剔用户会让物品计数变化，反之亦然）。
    """
    for _ in range(rounds):
        n0 = len(df)
        ic = df["subject_id"].value_counts()
        df = df[df["subject_id"].isin(ic[ic >= min_item].index)]
        uc = df["user_id"].value_counts()
        df = df[df["user_id"].isin(uc[uc >= min_user].index)]
        if len(df) == n0:
            break
    return df
