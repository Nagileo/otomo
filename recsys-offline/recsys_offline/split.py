"""数据集划分。

本数据集无时间戳 → 用 **leave-one-out**（每个用户随机留出 1 个正反馈作测试）。
（docs/05 偏好时间分割以防未来泄漏；有时间戳的数据集再换。）固定 seed 保证可复现。
"""
from __future__ import annotations

import random

import pandas as pd


def leave_one_out(
    df: pd.DataFrame, seed: int = 42, min_items: int = 2
) -> tuple[list[tuple[int, int]], dict[int, set[int]]]:
    """df: (user_id, anime_id) 正反馈。返回 (train 交互对, test={user: {held_item}})。

    每个 >=min_items 个正反馈的用户随机留 1 个进 test、其余进 train；不足的全进 train。
    """
    rng = random.Random(seed)
    train: list[tuple[int, int]] = []
    test: dict[int, set[int]] = {}
    for uid, grp in df.groupby("user_id")["anime_id"]:
        items = grp.tolist()
        if len(items) < min_items:
            train.extend((uid, it) for it in items)
            continue
        held = items[rng.randrange(len(items))]
        test[uid] = {held}
        train.extend((uid, it) for it in items if it != held)
    return train, test
