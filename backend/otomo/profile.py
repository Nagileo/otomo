"""口味画像计算（A4 产品能力）：从用户 Bangumi 收藏聚合出"二次元口味"。

只用结构化收藏数据（社区标签 + 用户评分 + 年份），输出结构化画像；"二次元人格"标签留给 agent 据此叙述。
"""
from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field

# 媒介/来源类标签对"题材口味"是噪声（年代单独统计）；保留题材与 staff 名
_STOP_TAGS = {
    "TV", "剧场版", "OVA", "OAD", "WEB", "PV", "动画", "TV动画", "日本", "中国", "美国",
    # 来源/媒介类标签太宽泛，对题材口味是噪声
    "漫画改", "原创", "小说改", "游戏改", "轻小说改", "漫改", "改编",
}


def _is_noise(tag: str) -> bool:
    return tag in _STOP_TAGS or (tag.isdigit() and len(tag) == 4)


class TasteProfile(BaseModel):
    username: str
    watched: int = 0
    rated: int = 0
    avg_rating: float | None = None
    top_tags: list[dict] = Field(default_factory=list)       # [{tag, weight}]
    decade_distribution: dict[str, int] = Field(default_factory=dict)
    favorites: list[str] = Field(default_factory=list)        # 高分作品名


def compute_taste_profile(username: str, items: list[dict]) -> TasteProfile:
    tag_weight: Counter[str] = Counter()
    decades: Counter[str] = Counter()
    rates: list[int] = []
    fav: list[tuple[int, str]] = []

    for it in items:
        rate = it.get("rate") or 0
        subj = it.get("subject") or {}
        if rate:
            rates.append(rate)
        date = subj.get("date") or ""
        if len(date) >= 4 and date[:4].isdigit():
            decades[f"{date[:3]}0s"] += 1
        weight = rate if rate else 1  # 评分越高，其标签越能代表口味
        for t in subj.get("tags") or []:
            name = (t or {}).get("name")
            if name and not _is_noise(name):
                tag_weight[name] += weight
        name = subj.get("name_cn") or subj.get("name")
        if rate >= 9 and name:
            fav.append((rate, name))

    fav.sort(key=lambda x: (-x[0]))
    return TasteProfile(
        username=username,
        watched=len(items),
        rated=len(rates),
        avg_rating=round(sum(rates) / len(rates), 2) if rates else None,
        top_tags=[{"tag": k, "weight": v} for k, v in tag_weight.most_common(15)],
        decade_distribution=dict(decades.most_common()),
        favorites=[n for _r, n in fav[:8]],
    )
