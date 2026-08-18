"""数据集划分。

新采集数据带 updated_at，默认应使用 temporal leave-one-out，避免未来交互泄漏。
旧 CSV 没有时间戳时保留确定性的随机 leave-one-out 兼容路径，并明确报告降级。
"""
from __future__ import annotations

import random

import pandas as pd


def remove_held_out_interactions(
    frame: pd.DataFrame,
    test: dict[int, set[int]],
    *,
    item_col: str = "subject_id",
) -> pd.DataFrame:
    """Remove each user's held-out item from every weighted training signal.

    The same item may remain for other users. This matters for signed implicit
    data: removing it only from the positive matrix would leak that user's
    held-out row through the weighted ALS matrix.
    """
    held_pairs = {
        (int(user_id), int(item_id))
        for user_id, item_ids in test.items()
        for item_id in item_ids
    }
    keep = [
        (int(user_id), int(item_id)) not in held_pairs
        for user_id, item_id in zip(frame["user_id"], frame[item_col], strict=False)
    ]
    return frame.loc[keep].copy()


def leave_one_out(
    df: pd.DataFrame, seed: int = 42, min_items: int = 2, item_col: str = "anime_id"
) -> tuple[list[tuple[int, int]], dict[int, set[int]]]:
    """df: (user_id, <item_col>) 正反馈。返回 (train 交互对, test={user: {held_item}})。

    每个 >=min_items 个正反馈的用户随机留 1 个进 test、其余进 train；不足的全进 train。
    item_col 默认 anime_id（MAL 数据集）；Bangumi 原生数据传 "subject_id"。
    """
    rng = random.Random(seed)
    train: list[tuple[int, int]] = []
    test: dict[int, set[int]] = {}
    for uid, grp in df.groupby("user_id")[item_col]:
        items = grp.tolist()
        if len(items) < min_items:
            train.extend((uid, it) for it in items)
            continue
        held = items[rng.randrange(len(items))]
        test[uid] = {held}
        train.extend((uid, it) for it in items if it != held)
    return train, test


def temporal_leave_one_out(
    df: pd.DataFrame, min_items: int = 2, item_col: str = "subject_id",
) -> tuple[list[tuple[int, int]], dict[int, set[int]]]:
    if "updated_at" not in df.columns:
        raise ValueError("temporal split requires updated_at")
    frame = df.copy()
    frame["_ts"] = pd.to_datetime(frame["updated_at"], utc=True, errors="coerce")
    train: list[tuple[int, int]] = []
    test: dict[int, set[int]] = {}
    for uid, group in frame.groupby("user_id"):
        group = group.sort_values(["_ts", item_col], na_position="first")
        items = group[item_col].tolist()
        if len(items) < min_items:
            train.extend((uid, item) for item in items)
            continue
        held = items[-1]
        test[int(uid)] = {int(held)}
        train.extend((int(uid), int(item)) for item in items[:-1])
    return train, test
