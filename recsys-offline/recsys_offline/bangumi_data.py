"""Bangumi 原生收藏数据加载与隐式反馈置信度构造。

collections_{stype}.csv: user_id, subject_id, ctype, rate, updated_at
ctype(收藏状态): 1想看 2看过 3在看 4搁置 5抛弃。

正反馈默认取 看过(2)+在看(3)：已消费/在消费才反映真实口味；想看(1)是意图非消费、抛弃(5)是负反馈，均排除。
旧训练只把看过/在看压成二值 1，无法区分 10 分神作和 3 分踩雷。本模块保留原始
收藏状态、评分和时间，并构造可复现的 weighted implicit confidence：

- 看过/在看/想看是不同强度的正信号；
- 低分、搁置和抛弃进入负置信度，避免协同模型继续推相似雷区；
- 较新的行为略强，但保留时间衰减下限，不会抹掉经典长期口味；
- 时间衰减相对数据集内最新时间计算，重跑同一份快照结果稳定。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

POSITIVE_CTYPES = (2, 3)  # 看过 / 在看
WEIGHTING_VERSION = "bangumi-weighted-v2"


def _base_preference(ctype: int, rate: int) -> float:
    """Map one Bangumi collection row to a signed preference confidence."""
    if ctype == 5:  # 抛弃：明确负反馈；低分会进一步加重
        return -(1.0 + max(0, 6 - rate) * 0.12) if rate else -1.0
    if ctype == 4:  # 搁置通常是弱负反馈；高分搁置保守看作弱正
        if rate >= 8:
            return 0.35
        return -(0.3 + max(0, 6 - rate) * 0.08) if rate else -0.35
    if ctype == 1:  # 想看只是意图，不等同于消费后喜欢
        return 0.3
    if ctype not in POSITIVE_CTYPES:
        return 0.0
    if rate <= 0:
        return 1.0 if ctype == 2 else 0.8
    rating_weight = {
        10: 1.9,
        9: 1.65,
        8: 1.4,
        7: 1.15,
        6: 0.75,
        5: 0.25,
        4: -0.45,
        3: -0.7,
        2: -0.95,
        1: -1.2,
    }
    value = rating_weight.get(rate, 1.0)
    return value * (0.85 if ctype == 3 and value > 0 else 1.0)


def _time_multiplier(
    timestamps: pd.Series,
    *,
    half_life_days: float,
    floor: float,
) -> pd.Series:
    parsed = pd.to_datetime(timestamps, utc=True, errors="coerce")
    newest = parsed.max()
    if pd.isna(newest) or half_life_days <= 0:
        return pd.Series(np.ones(len(parsed), dtype=np.float32), index=timestamps.index)
    age_days = (newest - parsed).dt.total_seconds().div(86400).clip(lower=0)
    decay = floor + (1.0 - floor) * np.power(0.5, age_days / half_life_days)
    return decay.fillna(1.0).astype(np.float32)


def load_bangumi_weighted(
    path: str,
    *,
    half_life_days: float = 730.0,
    time_floor: float = 0.55,
) -> pd.DataFrame:
    """Return deduplicated signed interactions with a ``weight`` column."""
    columns = pd.read_csv(path, nrows=0).columns
    usecols = ["user_id", "subject_id", "ctype", "rate"]
    if "updated_at" in columns:
        usecols.append("updated_at")
    frame = pd.read_csv(path, usecols=usecols)
    for column in ("user_id", "subject_id", "ctype", "rate"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0).astype(int)
    frame = frame[(frame["user_id"] > 0) & (frame["subject_id"] > 0)]
    if "updated_at" in frame.columns:
        frame["_ts"] = pd.to_datetime(frame["updated_at"], utc=True, errors="coerce")
        frame = frame.sort_values(["user_id", "subject_id", "_ts"], na_position="first")
    frame = frame.drop_duplicates(subset=["user_id", "subject_id"], keep="last")
    base = pd.Series(
        (_base_preference(int(row.ctype), int(row.rate)) for row in frame.itertuples()),
        index=frame.index,
        dtype=np.float32,
    )
    if "updated_at" in frame.columns:
        multiplier = _time_multiplier(
            frame["updated_at"], half_life_days=half_life_days, floor=time_floor,
        )
    else:
        multiplier = pd.Series(np.ones(len(frame), dtype=np.float32), index=frame.index)
    frame["weight"] = (base * multiplier).astype(np.float32)
    frame = frame[frame["weight"] != 0].drop(columns=["_ts"], errors="ignore")
    return frame.reset_index(drop=True)


def load_bangumi_positive(
    path: str,
    collection_types: tuple[int, ...] = POSITIVE_CTYPES,
    min_rate: int = 0,
) -> pd.DataFrame:
    """返回正反馈交互 (user_id, subject_id) 去重 DataFrame。

    min_rate>0 时只保留 rate>=min_rate（注意会滤掉未评分 rate=0 的交互）。
    """
    columns = pd.read_csv(path, nrows=0).columns
    usecols = ["user_id", "subject_id", "ctype", "rate"]
    if "updated_at" in columns:
        usecols.append("updated_at")
    df = pd.read_csv(path, usecols=usecols)
    df = df[df["ctype"].isin(collection_types)]
    if min_rate > 0:
        df = df[df["rate"] >= min_rate]
    keep = ["user_id", "subject_id"] + (["updated_at"] if "updated_at" in df.columns else [])
    return df[keep].drop_duplicates(subset=["user_id", "subject_id"], keep="last")


def filter_active(
    df: pd.DataFrame, min_user: int = 5, min_item: int = 5, rounds: int = 5
) -> pd.DataFrame:
    """迭代过滤稀疏交互：物品交互<min_item、用户交互<min_user 的剔除（CF 必备，否则学不动）。

    交替过滤直到稳定或 rounds 用尽（剔用户会让物品计数变化，反之亦然）。
    """
    for _ in range(rounds):
        n0 = len(df)
        support = df[df["weight"] > 0] if "weight" in df.columns else df
        ic = support["subject_id"].value_counts()
        df = df[df["subject_id"].isin(ic[ic >= min_item].index)]
        support = df[df["weight"] > 0] if "weight" in df.columns else df
        uc = support["user_id"].value_counts()
        df = df[df["user_id"].isin(uc[uc >= min_user].index)]
        if len(df) == n0:
            break
    return df
