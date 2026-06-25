"""Bangumi typed 结果模型（每个工具的 result schema，禁止裸 Any）。

只声明我们会用的字段，extra="ignore" 容忍 API 的丰富返回，避免动态结构拖垮 verifier/评测。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_BASE = ConfigDict(extra="ignore")

SUBJECT_TYPE_NAME = {1: "书籍", 2: "动画", 3: "音乐", 4: "游戏", 6: "三次元"}


class Rating(BaseModel):
    model_config = _BASE
    score: float | None = None
    rank: int | None = None
    total: int | None = None
    count: dict[str, int] | None = None  # 1~10 分各有多少人打 → 口碑分布（集中高分/双峰两极）


def _image_of(raw: dict) -> str | None:
    img = raw.get("images")
    if isinstance(img, dict):
        return img.get("common") or img.get("medium") or img.get("grid") or None
    return None


class SubjectBrief(BaseModel):
    model_config = _BASE
    id: int
    name: str = ""
    name_cn: str = ""
    type: int | None = None
    type_name: str | None = None
    date: str | None = None
    score: float | None = None
    rank: int | None = None
    role: str | None = None  # 在该作品里的职责/角色（来自关系边 staff，如 主演/配音 角色名）
    image: str | None = None  # 封面图 URL

    @classmethod
    def from_raw(cls, raw: dict) -> "SubjectBrief":
        rating = raw.get("rating") or {}
        t = raw.get("type")
        return cls(
            id=raw.get("id"),
            name=raw.get("name", "") or "",
            name_cn=raw.get("name_cn", "") or "",
            type=t,
            type_name=SUBJECT_TYPE_NAME.get(t) if t else None,
            date=raw.get("date"),
            score=rating.get("score") if isinstance(rating, dict) else None,
            rank=rating.get("rank") if isinstance(rating, dict) else None,
            role=raw.get("staff"),
            image=_image_of(raw),
        )


class SubjectDetail(BaseModel):
    model_config = _BASE
    id: int
    name: str = ""
    name_cn: str = ""
    type: int | None = None
    date: str | None = None
    summary: str = ""
    rating: Rating | None = None
    tags: list[str] = Field(default_factory=list)
    image: str | None = None

    @classmethod
    def from_raw(cls, raw: dict) -> "SubjectDetail":
        tags = [t.get("name", "") for t in (raw.get("tags") or []) if isinstance(t, dict)]
        return cls(
            id=raw.get("id"),
            name=raw.get("name", "") or "",
            name_cn=raw.get("name_cn", "") or "",
            type=raw.get("type"),
            date=raw.get("date"),
            summary=(raw.get("summary") or "")[:600],
            rating=Rating.model_validate(raw["rating"]) if raw.get("rating") else None,
            tags=tags[:15],
            image=_image_of(raw),
        )


class PersonBrief(BaseModel):
    model_config = _BASE
    id: int
    name: str = ""
    type: int | None = None
    # 在某作品里的角色/职责上下文（来自关系边）
    relation: str | None = None
    subject_name: str | None = None


class CharacterBrief(BaseModel):
    model_config = _BASE
    id: int
    name: str = ""
    relation: str | None = None  # 主角/配角...


class SubjectListResult(BaseModel):
    model_config = _BASE
    query: str
    count: int
    subjects: list[SubjectBrief] = Field(default_factory=list)


class CharacterListResult(BaseModel):
    model_config = _BASE
    count: int
    characters: list[CharacterBrief] = Field(default_factory=list)


class PersonListResult(BaseModel):
    model_config = _BASE
    count: int
    persons: list[PersonBrief] = Field(default_factory=list)


class RelatedSubject(BaseModel):
    """关联条目（跨媒体边）：某作品改编/原作/续集/不同演绎等指向的另一条目，可跨 type。"""

    model_config = _BASE
    id: int
    name: str = ""
    name_cn: str = ""
    relation: str = ""        # 改编 / 原作 / 续集 / 不同演绎 / 系列 / 角色歌 ...
    type: int | None = None
    type_name: str | None = None
    image: str | None = None

    @classmethod
    def from_raw(cls, raw: dict) -> "RelatedSubject":
        t = raw.get("type")
        return cls(
            id=raw.get("id"),
            name=raw.get("name", "") or "",
            name_cn=raw.get("name_cn", "") or "",
            relation=raw.get("relation", "") or "",
            type=t,
            type_name=SUBJECT_TYPE_NAME.get(t) if t else None,
            image=_image_of(raw),
        )


class RelatedSubjectsResult(BaseModel):
    model_config = _BASE
    subject_id: int
    count: int
    relations: list[RelatedSubject] = Field(default_factory=list)
