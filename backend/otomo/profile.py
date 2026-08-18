"""口味画像计算（A4 产品能力）：从用户 Bangumi 收藏聚合出"二次元口味"。

只用结构化收藏数据（社区标签 + 用户评分 + 年份），输出结构化画像；"二次元人格"标签留给 agent 据此叙述。
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone

from pydantic import BaseModel, Field

# 口味漂移：按收藏更新时间指数衰减，半衰期两年；很老的收藏保底 0.15
# （十年前的本命也是口味的一部分，但不该和上个月看的同权）。0 = 关闭。
PROFILE_DECAY_HALF_LIFE_DAYS = 730.0
_DECAY_FLOOR = 0.15


def _recency_decay(updated_at: str, *, now: datetime | None = None) -> float:
    if PROFILE_DECAY_HALF_LIFE_DAYS <= 0 or not updated_at:
        return 1.0
    try:
        ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except ValueError:
        return 1.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_days = max(((now or datetime.now(timezone.utc)) - ts).total_seconds() / 86400.0, 0.0)
    return max(math.pow(0.5, age_days / PROFILE_DECAY_HALF_LIFE_DAYS), _DECAY_FLOOR)

# 媒介/来源类标签对"题材口味"是噪声（年代单独统计）；保留题材与 staff 名
_STOP_TAGS = {
    "TV", "剧场版", "OVA", "OAD", "WEB", "PV", "动画", "TV动画", "日本", "中国", "美国",
    # 来源/媒介类标签太宽泛，对题材口味是噪声
    "漫画改", "原创", "小说改", "游戏改", "轻小说改", "漫改", "改编",
}

# 这些标签仍有信息量，但在绝大多数动画收藏里都会高频出现。把它们完全删除会
# 让画像失真；保持一个较低权重，避免“日常/校园/恋爱”淹没更细的口味即可。
_GENERIC_TAGS = {
    "日常", "校园", "恋爱", "搞笑", "治愈", "青春", "奇幻", "战斗", "冒险",
    "动画", "漫画", "小说", "游戏", "音乐",
}

_STATUS_WEIGHTS = {
    1: 0.08,   # 想看：兴趣很弱，不能等同喜欢
    2: 0.20,   # 看过：只有配合评分才是可靠正向信号
    3: 0.16,   # 在看：有兴趣，但尚未形成最终评价
    4: -0.18,  # 搁置：弱负向
    5: -0.55,  # 抛弃：较强负向
}


def _is_noise(tag: str) -> bool:
    return tag in _STOP_TAGS or (tag.isdigit() and len(tag) == 4)


class TasteProfile(BaseModel):
    username: str
    watched: int = 0
    rated: int = 0
    avg_rating: float | None = None
    top_tags: list[dict] = Field(default_factory=list)       # [{tag, weight}]
    bottom_tags: list[dict] = Field(default_factory=list)    # [{tag, weight}]，负权重
    status_distribution: dict[str, int] = Field(default_factory=dict)
    decade_distribution: dict[str, int] = Field(default_factory=dict)
    favorites: list[str] = Field(default_factory=list)        # 高分作品名


def compute_taste_profile(username: str, items: list[dict]) -> TasteProfile:
    tag_weight: Counter[str] = Counter()
    tag_documents: Counter[str] = Counter()
    decades: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    rates: list[int] = []
    fav: list[tuple[int, str]] = []

    for it in items:
        rate = int(it.get("rate") or 0)
        if rate:
            rates.append(rate)
    avg_rating = sum(rates) / len(rates) if rates else 0.0

    clean_tags_by_item: list[set[str]] = []
    for it in items:
        subj = it.get("subject") or {}
        clean_tags = {
            str((t or {}).get("name") or "").strip()
            for t in (subj.get("tags") or [])
            if isinstance(t, dict)
        }
        clean_tags = {tag for tag in clean_tags if tag and not _is_noise(tag)}
        clean_tags_by_item.append(clean_tags)
        tag_documents.update(clean_tags)

    corpus_size = max(len(items), 1)
    for it, clean_tags in zip(items, clean_tags_by_item, strict=False):
        rate = int(it.get("rate") or 0)
        status = int(it.get("type") or 0)
        statuses[str(status)] += 1
        subj = it.get("subject") or {}
        date = subj.get("date") or ""
        if len(date) >= 4 and date[:4].isdigit():
            decades[f"{date[:3]}0s"] += 1
        # 评分相对个人均分中心化：低于自己通常水平的作品必须贡献负信号，不能
        # 因为“看过”就继续强化其题材。未评分时仅保留很弱的收藏状态信号。
        if rate and rates:
            rating_signal = max(min((rate - avg_rating) / 2.5, 1.6), -1.6)
        else:
            rating_signal = 0.0
        preference = rating_signal + _STATUS_WEIGHTS.get(status, 0.0)
        recency = _recency_decay(str(it.get("updated_at") or ""))
        for name in clean_tags:
            # 用户收藏内部的轻量 IDF 只做温和去同质化；全站泛标签另行降权。
            df = tag_documents[name]
            idf = min(1.0 + 0.18 * math.log((1 + corpus_size) / (1 + df)), 1.45)
            generic = 0.42 if name in _GENERIC_TAGS else 1.0
            tag_weight[name] += preference * recency * idf * generic
        name = subj.get("name_cn") or subj.get("name")
        if rate >= 9 and name:
            fav.append((rate, name))

    fav.sort(key=lambda x: (-x[0]))
    positive = sorted(((tag, weight) for tag, weight in tag_weight.items() if weight > 0.03), key=lambda x: -x[1])
    negative = sorted(((tag, weight) for tag, weight in tag_weight.items() if weight < -0.03), key=lambda x: x[1])
    return TasteProfile(
        username=username,
        watched=sum(1 for it in items if int(it.get("type") or 0) == 2),
        rated=len(rates),
        avg_rating=round(avg_rating, 2) if rates else None,
        top_tags=[{"tag": k, "weight": round(v, 4)} for k, v in positive[:15]],
        bottom_tags=[{"tag": k, "weight": round(v, 4)} for k, v in negative[:12]],
        status_distribution=dict(statuses),
        decade_distribution=dict(decades.most_common()),
        favorites=[n for _r, n in fav[:8]],
    )
