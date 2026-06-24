"""数据集加载（Anime Recommendations Database, Kaggle CC0）。

rating.csv: user_id, anime_id, rating（-1=看过但未评分）。取 rating≥min_rating 为正反馈（用户喜欢）。
"""
from __future__ import annotations

import pandas as pd


def load_positive_interactions(path: str, min_rating: int = 7) -> pd.DataFrame:
    """返回正反馈交互 DataFrame（user_id, anime_id），即 rating≥min_rating。"""
    df = pd.read_csv(path, usecols=["user_id", "anime_id", "rating"])
    df = df[df["rating"] >= min_rating][["user_id", "anime_id"]]
    return df.drop_duplicates()


def load_anime_titles(path: str) -> dict[int, str]:
    """anime_id → 名称（用于把指标/推荐打印成人能看的）。"""
    df = pd.read_csv(path, usecols=["anime_id", "name"])
    return dict(zip(df["anime_id"], df["name"]))
